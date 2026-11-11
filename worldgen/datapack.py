"""Write the Hermes HQ datapack (Minecraft 26.2 / Omo Space).

Format facts (checked against the 26.2 jar and the user's working packs):
  * pack.mcmeta: pack_format = min_format = max_format = 107 (26.2 version.json
    pack_version.data_major 107; same as every Omo pack in the live save).
  * Folders are singular: data/<ns>/function/*.mcfunction, data/<ns>/structure/*.nbt.
  * Structures: gzip NBT, DataVersion 4903 (26.2 world_version).

Omo blocks come from structure templates (`place template`), because only a
template places a CC:T monitor wall that is already joined (Width/Height/
XIndex/YIndex stored per panel), exactly like omo-space mod/tools/make_kits.py.
Every Omo Computer (omolife:hermes_computer) carries only an OmoPreset {Name, Kind, Profile, Tabs}:
no Workspace / Station / WorkspaceName, so each player's copy makes its own
workspace the first time the desk is opened.

All user-derived text is cleaned (control/format chars, newlines, the
section sign and characters outside the BMP removed; length clamped) and
then written with json.dumps(ensure_ascii=True), which is valid SNBT, so a
profile label can never end a string or a command.
"""
import gzip
import io
import json
import re
import shutil
import struct
import time
import unicodedata
import zipfile
from pathlib import Path

from . import hall as H
from . import layout as L

PACK_FORMAT = 107
DATA_VERSION = 4903
NAMESPACE_RE = re.compile(r"^[a-z0-9_]{1,48}\Z")
RESERVED_NAMESPACES = {"minecraft", "omolife", "computercraft", "fabric", "realms"}
MAX_FILL = 32768

ACCENT = "minecraft:gold_block"
# sign (dye) colour -> chat/text component colour
CHAT = {"yellow": "yellow", "pink": "light_purple", "orange": "gold", "cyan": "dark_aqua", "red": "red", "lime": "green",
        "light_blue": "aqua", "magenta": "light_purple"}
STAGE_BUDGET = 24000      # blocks (fill volume, ~1 per setblock, more per template) per build tick
STAGE_GAP = 2             # ticks between stages
STAGE_MAX_CMDS = 3000     # commands per stage (well under maxCommandChainLength 65536)


# --------------------------------------------------------------------------- text
def clean(value, limit=64):
    """A safe single-line string: no control/format/private chars, no newline,
    no section sign, BMP only, whitespace collapsed, at most `limit` chars."""
    out = []
    for ch in str(value if value is not None else ""):
        if ch in "\n\r\t\v\f\u2028\u2029":
            out.append(" ")
            continue
        if ch == "\u00a7" or ord(ch) > 0xFFFF:
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        out.append(ch)
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    if len(s) > limit:
        s = s[:max(1, limit - 1)].rstrip() + "\u2026"
    return s


def jstr(value, limit=256):
    return json.dumps(clean(value, limit), ensure_ascii=True)


def jtext(component):
    """A text component (dict / list of dicts) as compact ASCII JSON (valid SNBT)."""
    return json.dumps(component, ensure_ascii=True, separators=(",", ":"))


def comp(text, color="white", limit=64, pre="", post="", **style):
    """One text component; user text is cleaned, `pre`/`post` (our own line breaks) are not."""
    d = {"text": pre + clean(text, limit) + post, "color": color}
    d.update(style)
    return d


# --------------------------------------------------------------------------- NBT
class Byte(int):
    pass


def _payload(v):
    if isinstance(v, (bool, Byte)):
        return 1, struct.pack(">b", int(v))
    if isinstance(v, int):
        return 3, struct.pack(">i", v)
    if isinstance(v, float):
        return 6, struct.pack(">d", v)
    if isinstance(v, str):
        b = v.encode("utf-8")
        if len(b) > 65535:
            raise ValueError("NBT string too long")
        return 8, struct.pack(">H", len(b)) + b
    if isinstance(v, list):
        items = [_payload(x) for x in v]
        kind = items[0][0] if items else 0
        if any(k != kind for k, _ in items):
            raise ValueError("NBT lists hold one type")
        return 9, struct.pack(">bi", kind, len(items)) + b"".join(p for _, p in items)
    if isinstance(v, dict):
        out = b""
        for k, x in v.items():
            kind, p = _payload(x)
            name = k.encode("utf-8")
            out += struct.pack(">bH", kind, len(name)) + name + p
        return 10, out + b"\x00"
    raise TypeError(type(v))


def nbt_bytes(root):
    _, body = _payload(root)
    return gzip.compress(b"\x0a\x00\x00" + body, mtime=0)


def _block(spec):
    name, _, props = spec.partition("[")
    entry = {"Name": name if ":" in name else "minecraft:" + name}
    if props:
        entry["Properties"] = dict(p.split("=", 1) for p in props.rstrip("]").split(","))
    return entry


class Template:
    """A structure template drawn facing south (viewer on +z), like make_kits.Kit."""

    def __init__(self, w, h, d):
        self.size = (w, h, d)
        self.cells = {}

    def put(self, x, y, z, spec, nbt=None):
        w, h, d = self.size
        if not (0 <= x < w and 0 <= y < h and 0 <= z < d):
            raise ValueError((x, y, z))
        self.cells[(x, y, z)] = (spec, nbt)

    def fill(self, x1, y1, z1, x2, y2, z2, spec):
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for z in range(z1, z2 + 1):
                    self.put(x, y, z, spec)

    def wall(self, x0, y0, z, w, h, url=None):
        """A joined w x h monitor wall facing south (make_kits.Kit.wall)."""
        for i in range(w):
            for j in range(h):
                edges = "".join(c for c, on in (("l", i > 0), ("r", i < w - 1), ("u", j < h - 1), ("d", j > 0))
                                if on) or "none"
                nbt = {"id": "omolife:monitor", "XIndex": i, "YIndex": j, "Width": w, "Height": h}
                if url:
                    nbt.update({"OmoSource": "web", "OmoUrl": url, "OmoZoom": 100})
                self.put(x0 + i, y0 + j, z, f"omolife:monitor[facing=south,orientation=north,state={edges}]", nbt)

    def to_bytes(self):
        w, h, d = self.size
        palette, index, blocks = [], {}, []
        for x in range(w):
            for y in range(h):
                for z in range(d):
                    spec, nbt = self.cells.get((x, y, z), ("minecraft:air", None))
                    if spec not in index:
                        index[spec] = len(palette)
                        palette.append(_block(spec))
                    e = {"pos": [x, y, z], "state": index[spec]}
                    if nbt:
                        e["nbt"] = nbt
                    blocks.append(e)
        return nbt_bytes({"DataVersion": DATA_VERSION, "size": [w, h, d], "palette": palette,
                          "blocks": blocks, "entities": []})


def preset_for(profile):
    name = profile["name"]
    pre = {"Name": clean("Hermes \u00b7 " + name if name != "default" else "Hermes \u00b7 default", 40),
           "Kind": "hermes"}
    if name != "default":
        pre["Profile"] = name          # validated against the bridge's PROFILE_RE by inventory
    pre["Tabs"] = [{"Name": "Hermes", "Kind": "hermes"}, {"Name": "Shell", "Kind": "shell"}]
    return pre


def sign_nbt(color, lines, bold_line=1):
    msgs = []
    for i, ln in enumerate((list(lines) + ["", "", "", ""])[:4]):
        m = {"text": clean(ln, 15)}
        if i == bold_line:
            m["bold"] = Byte(1)
        msgs.append(m)
    side = {"messages": msgs, "color": color, "has_glowing_text": Byte(1)}
    return {"id": "minecraft:sign", "is_waxed": Byte(1), "front_text": side, "back_text": dict(side)}


def desk_sign_lines(p):
    model = p.get("model") or "default model"
    return [p["name"], p.get("label") or "Hermes agent", model,
            f"{p.get('skills', 0)} sk \u00b7 {p.get('cron_jobs', 0)} cron"]


def template_from(size, kcells):
    """A Template from hall.kit_cells / hall.board_cells (abstract nbt -> block entities)."""
    t = Template(*size)
    for (x, y, z), (spec, meta) in kcells.items():
        if spec == "omolife:monitor":
            i, j, w, h = meta["wall"]
            edges = "".join(c for c, on in (("l", i > 0), ("r", i < w - 1), ("u", j < h - 1), ("d", j > 0)) if on) or "none"
            nbt = {"id": "omolife:monitor", "XIndex": i, "YIndex": j, "Width": w, "Height": h}
            if meta.get("url"):
                nbt.update({"OmoSource": "web", "OmoUrl": meta["url"], "OmoZoom": 100})
            t.put(x, y, z, f"omolife:monitor[facing=south,orientation=north,state={edges}]", nbt)
        elif meta and "station" in meta:
            t.put(x, y, z, spec, {"id": "omolife:station", "OmoPreset": meta["station"]})
        elif meta and "sign" in meta:
            color, lines = meta["sign"]
            t.put(x, y, z, H.mc(spec), sign_nbt(color, lines, bold_line=0))
        else:
            t.put(x, y, z, H.mc(spec))
    return t


def desk_template(profile, theme=4):
    """7 x 5 x 2 desk kit (hall.kit_cells): themed frame with gold caps, a joined 5x3 terminal wall, the
    Omo Computer UNDER the screen in the frame row (touching only the bottom-middle panel), name plate and
    status lamp on the desk either side of the open cell in front of the computer, glowing sign on top."""
    color = L.THEMES[theme][1]
    kc = H.kit_cells(theme, sign=(color, desk_sign_lines(profile)), preset=preset_for(profile))
    return template_from((L.KIT_W, L.KIT_H, L.KIT_D), kc)


def board_template(url):
    return template_from((L.BOARD_W, L.BOARD_H, 1), H.board_cells(url))


# --------------------------------------------------------------------------- commands
def _c(v):
    """A relative coordinate token: ~, ~3, ~-2, ~0.5."""
    if isinstance(v, float) and not v.is_integer():
        return f"~{v:g}"
    v = int(v)
    return "~" if v == 0 else f"~{v}"


def fill_cmds(x0, y0, z0, x1, y1, z1, block, mode=""):
    """fill commands for a box, split along z (then y) to stay under the fill limit."""
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    za, zb = sorted((z0, z1))
    w, h = xb - xa + 1, yb - ya + 1
    out = []
    if w * h > MAX_FILL:
        for y in range(ya, yb + 1):
            out += fill_cmds(xa, y, za, xb, y, zb, block, mode)
        return out
    step = max(1, MAX_FILL // (w * h))
    z = za
    while z <= zb:
        ze = min(zb, z + step - 1)
        out.append(" ".join(filter(None, ["fill", _c(xa), _c(ya), _c(z), _c(xb), _c(yb), _c(ze), block, mode])))
        z = ze + 1
    return out


def text_display(pos, text, tag, scale=0.5, line_width=200, billboard="vertical"):
    s = f"{scale:g}f"
    return (f"summon minecraft:text_display {_c(pos[0])} {_c(pos[1])} {_c(pos[2])} "
            f"{{Tags:[{json.dumps(tag)}],billboard:\"{billboard}\",background:0,shadow:1b,"
            f"line_width:{int(line_width)},text:{jtext(text)},"
            f"transformation:{{translation:[0f,0f,0f],left_rotation:[0f,0f,0f,1f],"
            f"scale:[{s},{s},{s}],right_rotation:[0f,0f,0f,1f]}}}}")


def desk_lines(p):
    model = clean(p.get("model") or "model: profile default", 40)
    stats = f"{p['skills']} skills \u00b7 {p['cron_jobs']} cron \u00b7 {p['plugins']} plugins"
    parts = [comp(p["name"], "white", 40, post="\n", bold=True)]
    if p.get("label"):
        parts.append(comp(p["label"], "gray", 48, post="\n"))
    parts.append(comp(model, "gold", 40, post="\n"))
    parts.append(comp(stats, "white", 64))
    plats = ", ".join(clean(x, 16) for x in p.get("platforms") or [])
    if plats:
        parts.append(comp(plats, "aqua", 60, pre="\n"))
    return parts


def _profile_line(p):
    bits = [p.get("model") or "default model", f"{p['skills']} skills", f"{p['cron_jobs']} cron"]
    if p.get("platforms"):
        bits.append(", ".join(p["platforms"]))
    return clean(" \u00b7 ".join(str(b) for b in bits), 120)


def _rects(layer):
    """Greedy rectangles of equal state in one layer: {(x, z): s} -> [(x0, x1, z0, z1, s)]."""
    out, done = [], set()
    for (x, z) in sorted(layer, key=lambda c: (c[1], c[0])):
        if (x, z) in done:
            continue
        s = layer[(x, z)]
        x1 = x
        while (x1 + 1, z) in layer and (x1 + 1, z) not in done and layer[(x1 + 1, z)] == s:
            x1 += 1
        z1 = z
        while all((xx, z1 + 1) in layer and (xx, z1 + 1) not in done and layer[(xx, z1 + 1)] == s
                  for xx in range(x, x1 + 1)):
            z1 += 1
        for xx in range(x, x1 + 1):
            for zz in range(z, z1 + 1):
                done.add((xx, zz))
        out.append((x, x1, z, z1, s))
    return out


def boxes(cells):
    """{(x, y, z): state} -> [(x0, y0, z0, x1, y1, z1, state)], bottom-up, each <= MAX_FILL blocks."""
    by_y = {}
    for (x, y, z), s in cells.items():
        by_y.setdefault(y, {})[(x, z)] = s
    out, active, prev = [], {}, None
    for y in sorted(by_y):
        cur = set(_rects(by_y[y]))
        for key in list(active):
            y0 = active[key]
            area = (key[1] - key[0] + 1) * (key[3] - key[2] + 1)
            if key not in cur or (y - y0 + 1) * area > MAX_FILL or y != prev + 1:
                out.append((key[0], y0, key[2], key[1], prev, key[3], key[4]))
                del active[key]
        for key in cur:
            if key not in active:
                active[key] = y
        prev = y
    last = max(by_y) if by_y else 0
    for key, y0 in active.items():
        out.append((key[0], y0, key[2], key[1], last, key[3], key[4]))
    out.sort(key=lambda b: (b[1], b[2], b[0]))
    return out


def _box_cmd(b, mode=""):
    x0, y0, z0, x1, y1, z1, s = b
    if (x0, y0, z0) == (x1, y1, z1) and not mode:
        return f"setblock {_c(x0)} {_c(y0)} {_c(z0)} {s}"
    return " ".join(filter(None, ["fill", _c(x0), _c(y0), _c(z0), _c(x1), _c(y1), _c(z1), s, mode]))


def _cost(cmd):
    t = cmd.split(" ")
    if t[0] == "fill":
        a = [int(v[1:] or 0) for v in t[1:7]]
        return (abs(a[3] - a[0]) + 1) * (abs(a[4] - a[1]) + 1) * (abs(a[5] - a[2]) + 1) + 4
    if t[0] == "place":
        return 300
    if t[0] == "summon":
        return 40
    return 3


def stages(cmds, budget=None):
    budget = budget or STAGE_BUDGET
    out, cur, used = [], [], 0
    for c in cmds:
        k = _cost(c)
        if cur and (used + k > budget or len(cur) >= STAGE_MAX_CMDS):
            out.append(cur)
            cur, used = [], 0
        cur.append(c)
        used += k
    if cur:
        out.append(cur)
    return out


def sign_cmd(pos, block, color, lines):
    msgs = [{"text": clean(ln, 15)} for ln in (list(lines) + ["", "", "", ""])[:4]]
    msgs[1]["bold"] = True
    side = f"{{messages:{jtext(msgs)},color:{json.dumps(color)},has_glowing_text:1b}}"
    return f"setblock {_c(pos[0])} {_c(pos[1])} {_c(pos[2])} {block}{{front_text:{side},back_text:{side},is_waxed:1b}}"


def build_commands(hall, ns, tag):
    """Ordered build commands (no staging): shell bottom-up, templates, decor, water, signs."""
    ph = hall.phase
    solid = {c: s for c, s in hall.cells.items() if ph[c] == "solid"}
    cmds = [_box_cmd(b) for b in boxes(solid)]
    for key, (X, Y, Z), rot, _ in hall.templates:
        cmds.append(f"place template {ns}:{key} {_c(X)} {_c(Y)} {_c(Z)} {rot}")
    for phase, rev in (("attach", False), ("hang", True), ("water", False), ("aqua", False)):
        cs = sorted((c for c in hall.cells if ph[c] == phase), key=lambda c: (c[1], c[2], c[0]), reverse=rev)
        cmds += [f"setblock {_c(c[0])} {_c(c[1])} {_c(c[2])} {hall.cells[c]}" for c in cs]
    for c in sorted(hall.signs):
        color, lines = hall.signs[c]
        cmds.append(sign_cmd(c, hall.cells[c], color, lines))
    return cmds


def clear_commands(hall):
    """Air over exactly the built columns (y 0 .. shell top), pond water back to stone. The floor stays."""
    top = {c: t for c, t in hall.mt.items() if t >= 0}
    cmds = []
    if hall.water:
        xs = [c[0] for c in hall.water]
        zs = [c[1] for c in hall.water]
        cmds += fill_cmds(min(xs) - 1, -4, min(zs) - 1, max(xs) + 1, -1, max(zs) + 1, "minecraft:stone",
                          "replace minecraft:water")
    for (x0, x1, z0, z1, t) in _rects(top):
        cmds += fill_cmds(x0, 0, z0, x1, t, z1, "minecraft:air")
    for c in sorted(hall.cells):
        if c[1] > hall.mt.get((c[0], c[2]), 999):      # plants on top of the mound
            cmds.append(f"setblock {_c(c[0])} {_c(c[1])} {_c(c[2])} minecraft:air")
    return cmds


def build_files(data, namespace="hermes_hq", bridge_port=8780, max_desks=48, theme_order=None, arrival=None,
                rooms=None):
    """{relative path: bytes} for the whole datapack.

    ``arrival`` (used by ``hermes omo create`` for a world it made itself): a dict with ``origin``
    [x, y, z] (the build origin, a block on the flat ground), ``title`` and optional ``time``
    (``time set`` value). Adds a load/tick hook that, the first time a player joins, puts them in
    front of the arch and builds the hall once. Without it the pack only builds on /function."""
    if not NAMESPACE_RE.match(namespace or "") or namespace in RESERVED_NAMESPACES:
        raise ValueError(f"bad namespace {namespace!r}: use 1-48 chars of a-z 0-9 _")
    port = int(bridge_port)
    if not 1 <= port <= 65535:
        raise ValueError("bad bridge port")
    max_desks = max(1, min(int(max_desks), L.HARD_MAX_DESKS))
    profiles = list(data.get("profiles") or [])
    if not profiles:
        raise ValueError("no Hermes profiles to build")
    shown, hidden = profiles[:max_desks], profiles[max_desks:]
    lay = L.plan_layout(len(shown), [p["name"] for p in shown], theme_order=theme_order, rooms=rooms)
    ns = namespace
    tag, origin, building, clearing = ns, ns + "_origin", ns + "_building", ns + "_clearing"
    url = f"http://127.0.0.1:{port}/board"
    hall = H.Hall(lay, shown, url=url)
    h = lay["hall"]
    W, Hh, D = lay["size"]
    files = {}

    def fn(name, lines):
        files[f"data/{ns}/function/{name}.mcfunction"] = ("\n".join(lines) + "\n").encode("ascii")

    def say(color, text, sel="@s"):
        return f"tellraw {sel} {jtext({'text': clean(text, 240), 'color': color})}"

    near = "@a[distance=..160]"

    # ---- templates
    for d, p in zip(lay["desks"], shown):
        files[f"data/{ns}/structure/desk_{d['index']}.nbt"] = desk_template(p, d["theme"]).to_bytes()
    files[f"data/{ns}/structure/board.nbt"] = board_template(url).to_bytes()

    # every label sits inside the hall box or just outside the facade (z 0.3): kill box z0-1 .. z1
    kill_box = (f"execute positioned {_c(h['x0'])} {_c(h['y0'])} {_c(h['z0'] - 1)} run kill "
                f"@e[type=minecraft:text_display,tag={tag},dx={W - 1},dy={Hh + 2},dz={D}]")
    kill_items = (f"execute positioned {_c(h['x0'])} {_c(h['y0'])} {_c(h['z0'])} run kill "
                  f"@e[type=minecraft:item,dx={W - 1},dy={Hh + 2},dz={D - 1}]")
    load = f"forceload add {_c(h['x0'])} {_c(h['z0'])} {_c(h['x1'])} {_c(h['z1'])}"
    unload = f"forceload remove {_c(h['x0'])} {_c(h['z0'])} {_c(h['x1'])} {_c(h['z1'])}"
    exists = f"@e[type=minecraft:marker,tag={origin}]"

    # ---- labels (last stage)
    labels = []
    for d, p in zip(lay["desks"], shown):
        labels.append(text_display(d["text_pos"], desk_lines(p), tag, scale=0.4, line_width=170))
    bd = lay["board"]
    labels.append(text_display([0.5, L.BOARD_H + 0.25, bd["monitor_z"] - 0.4],
                               [comp("HERMES BOARD", "gold", 20, bold=True), comp(url, "gray", 60, pre="\n")],
                               tag, scale=0.6))
    title = [comp((arrival or {}).get("title") or "HERMES HQ", "gold", 32, bold=True),
             comp(f"{len(shown)} agents \u00b7 one desk per Hermes profile", "white", 60, pre="\n")]
    labels.append(text_display([0.5, 5.2, 0.3], title, tag, scale=0.7, line_width=260))
    for b in lay["bays"]:
        sx, sz = b["signpost"]
        room = [comp(b["label"], CHAT[b["color"]], 40, bold=True)]
        if b.get("about"):
            room.append(comp(b["about"], "gray", 60, pre="\n", bold=False))
        labels.append(text_display([sx + 0.5, 2.6, sz + 0.5], room, tag, scale=0.8, line_width=180))

    # ---- build: stage 0 runs at once, the rest via schedule from the origin marker (no lag spike)
    body = build_commands(hall, ns, tag)
    st = stages(body)
    nst = len(st)
    fn("build", [
        f"# Hermes HQ v2 ({ns}): {len(shown)} desks. Builds a {W}x{Hh}x{D} cavern hall from your position toward +Z.",
        f"execute if entity {exists} run " + say("red", f"A Hermes HQ from this pack is already built (or being built) "
                                                       f"nearby. Stand near it and run /function {ns}:clear first."),
        f"execute unless entity {exists} run function {ns}:build_go",
    ])
    fn("build_go", [
        "# warn, then build at the block you stand in",
        say("yellow", f"Hermes HQ: replacing everything in the hall's footprint, a {W} x {Hh} x {D} box in front of "
                      f"you (toward +Z / south, x {h['x0']}..{h['x1']} relative, from 4 below your feet), with the "
                      f"cavern hall. Nothing outside that box is touched."),
        f"execute align xyz run function {ns}:build_at",
        say("aqua", f"Building in {nst} steps over {max(1, (nst - 1) * STAGE_GAP)} ticks..."),
    ])
    fn("build_at", [
        "# remember the origin, keep the area loaded, clear old labels, then stage 0",
        f"summon minecraft:marker ~ ~ ~ {{Tags:[{json.dumps(tag)},{json.dumps(origin)},{json.dumps(building)}]}}",
        load,
        kill_box,
        f"function {ns}:b_0",
    ])
    for i, part in enumerate(st):
        lines = [f"# build stage {i + 1}/{nst} (runs as the origin marker)"] + part
        if i + 1 < nst:
            lines.append(f"schedule function {ns}:s_{i + 1} {STAGE_GAP}t")
        else:
            lines += labels
            lines += [kill_items, f"tag @s remove {building}", unload,
                      say("green", f"Hermes HQ built: {len(shown)} desks. Walk south through the arch. Open a desk "
                                   f"once to make its workspace. Remove it with /function {ns}:clear.", near)]
        fn(f"b_{i}", lines)
        if i:
            fn(f"s_{i}", [f"# scheduled stage {i + 1}: only while this pack's origin marker is still building",
                          f"execute as @e[type=minecraft:marker,tag={origin},tag={building},limit=1] at @s "
                          f"run function {ns}:b_{i}"])

    # ---- preview: particle outline only, no blocks
    pv = ["# outline the footprint with particles; nothing is changed"]
    x0, x1, z0, z1 = h["x0"], h["x1"], h["z0"], h["z1"]
    pts = set()
    for x in range(x0, x1 + 1, 2):
        pts |= {(x, z0), (x, z1)}
    for z in range(z0, z1 + 1, 2):
        pts |= {(x0, z), (x1, z)}
    for x, z in sorted(pts):
        pv.append(f"particle minecraft:end_rod {_c(x + 0.5)} ~0.2 {_c(z + 0.5)} 0 0 0 0 1 force")
    for x, z in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
        for y in range(1, Hh - 4, 2):
            pv.append(f"particle minecraft:end_rod {_c(x + 0.5)} {_c(y + 0.2)} {_c(z + 0.5)} 0 0 0 0 1 force")
    d = lay["door"]
    for x in range(d["x0"], d["x1"] + 1):
        pv.append(f"particle minecraft:happy_villager {_c(x + 0.5)} ~0.5 {_c(d['z'] + 0.5)} 0.2 0.3 0.2 0 4 force")
    fn("preview_at", pv)
    fn("preview", [
        f"# Hermes HQ ({ns}) preview",
        say("aqua", f"Hermes HQ preview: {len(shown)} desks in {len(lay['bays'])} bays, a {W} x {Hh} x {D} cavern hall "
                    f"(x {h['x0']}..{h['x1']}, floor 1 below your feet) from here toward +Z / south. The green sparks "
                    f"are the arch. Run again to show it again; /function {ns}:build to build."),
        f"execute if entity {exists} run " + say("red", "Note: a Hermes HQ from this pack is already built nearby."),
        f"execute align xyz run function {ns}:preview_at",
    ])

    # ---- clear: only where this pack built (its marker), only the built columns
    cst = stages(clear_commands(hall))
    fn("clear_at", [
        "# stop a running build, keep the area loaded, remove labels, then clear in stages",
        f"tag @s remove {building}",
        f"tag @s add {clearing}",
        load,
        kill_box,
        f"function {ns}:c_0",
    ])
    for i, part in enumerate(cst):
        lines = [f"# clear stage {i + 1}/{len(cst)} (runs as the origin marker)"] + part
        if i + 1 < len(cst):
            lines.append(f"schedule function {ns}:cs_{i + 1} {STAGE_GAP}t")
        else:
            lines += [kill_items, unload,
                      say("green", "Hermes HQ removed (the floor stays; mine or /fill it yourself).", near),
                      "kill @s"]
        fn(f"c_{i}", lines)
        if i:
            fn(f"cs_{i}", [f"# scheduled clear stage {i + 1}",
                           f"execute as @e[type=minecraft:marker,tag={origin},tag={clearing},limit=1] at @s "
                           f"run function {ns}:c_{i}"])
    fn("clear", [
        f"# Hermes HQ ({ns}) clear",
        f"execute unless entity {exists} run " + say("red", "No Hermes HQ from this pack found nearby "
                                                             "(stand inside or next to it)."),
        f"execute if entity {exists} run " + say("yellow", "Removing Hermes HQ..."),
        f"execute as @e[type=minecraft:marker,tag={origin},limit=1,sort=nearest] unless entity @s[tag={clearing}] "
        f"at @s run function {ns}:clear_at",
    ])

    # ---- info
    info = [f"# Hermes HQ ({ns}) summary",
            "tellraw @s " + jtext([comp("Hermes HQ ", "gold", 20, bold=True),
                                   comp(f"{len(shown)} desks in {len(lay['bays'])} bays \u00b7 board {url}", "white", 120)])]
    for b in lay["bays"]:
        info.append("tellraw @s " + jtext([comp(f"{b['label']} ", CHAT[b["color"]], 40, bold=True),
                                           comp(f"{b['theme_name']} bay, {b['side']}: "
                                                + ", ".join(shown[i]["name"] for i in b["desks"]), "gray", 200)]))
    for p in shown:
        info.append("tellraw @s " + jtext([comp(p["name"], "white", 41, bold=True),
                                           comp(" " + _profile_line(p), "gray", 121)]))
    if hidden:
        info.append(say("yellow", f"{len(hidden)} more profiles not built (--max-desks {max_desks})."))
    info.append(say("gray", f"Commands: /function {ns}:preview, {ns}:build, {ns}:clear, {ns}:info"))
    fn("info", info)

    if arrival:
        files.update(arrival_files(ns, arrival, lay, len(shown), exists))

    files["pack.mcmeta"] = (json.dumps({"pack": {
        "pack_format": PACK_FORMAT, "min_format": PACK_FORMAT, "max_format": PACK_FORMAT,
        "description": clean(f"Hermes HQ v2: {len(shown)} Hermes desks + Hermes Board ({ns})", 120)}},
        indent=1, ensure_ascii=True) + "\n").encode("ascii")
    return dict(sorted(files.items()))


def arrival_files(ns, arrival, lay, ndesks, exists):
    """First-join hook for a world `hermes omo create` made: build once, greet, face the arch."""
    ox, oy, oz = (int(v) for v in arrival["origin"])
    h = lay["hall"]
    title = clean(arrival.get("title") or "Hermes HQ", 40)
    obj = (ns + "_st")[:16]
    files = {}

    def fn(name, lines):
        files[f"data/{ns}/function/{name}.mcfunction"] = ("\n".join(lines) + "\n").encode("ascii")

    files["data/minecraft/tags/function/load.json"] = (json.dumps({"values": [f"{ns}:arrival_load"]}) + "\n").encode()
    files["data/minecraft/tags/function/tick.json"] = (json.dumps({"values": [f"{ns}:arrival_tick"]}) + "\n").encode()
    fn("arrival_load", [f"scoreboard objectives add {obj} dummy"])
    fn("arrival_tick", [f"execute as @a[tag=!{ns}_arrived] at @s run function {ns}:arrival_join"])
    look = f"{ox + 0.5} {oy} {oz - 9.5} facing {ox + 0.5} {oy + 4} {oz + 20}"
    fn("arrival_join", [
        f"tag @s add {ns}_arrived",
        f"tp @s {look}",
        f"execute if score #built {obj} matches 1.. run return 0",
        f"scoreboard players set #built {obj} 1",
        *( [f"time set {int(arrival['time'])}"] if arrival.get("time") is not None else [] ),
        f"forceload add {ox + h['x0']} {oz + h['z0']} {ox + h['x1']} {oz + h['z1']}",
        f"title @s times 10 70 20",
        f"title @s subtitle {jtext(comp(arrival.get('tagline') or f'{ndesks} agents are moving in...', 'gray', 80))}",
        f"title @s title {jtext(comp(title, 'gold', 40, bold=True))}",
        f"schedule function {ns}:arrival_build 60t",
    ])
    fn("arrival_build", [
        f"execute unless entity {exists} positioned {ox} {oy} {oz} run function {ns}:build_at",
        "playsound minecraft:block.beacon.activate master @a ~ ~ ~ 0.6 1.2",
        "tellraw @a " + jtext([comp("Welcome home. ", "gold", 20, bold=True),
                               comp("Walk through the arch. Each desk is one of your Hermes profiles: "
                                    "right-click its Omo Computer once to open it.", "white", 200)]),
    ])
    return files


# --------------------------------------------------------------------------- output
class OutputError(Exception):
    pass


def resolve_out(out, as_zip):
    p = Path(out).expanduser()
    if as_zip and p.suffix.lower() != ".zip":
        p = p.with_name(p.name + ".zip")
    return p


def write_output(files, out, as_zip=False, force=False):
    """Write files to a directory (or a .zip). Refuses to overwrite anything that
    is not empty unless force; never overwrites a non-empty dir that is not a datapack."""
    p = resolve_out(out, as_zip)
    if as_zip:
        if p.exists() and (p.is_dir() or not force):
            raise OutputError(f"{p} already exists (use --force to replace it)" if not p.is_dir()
                              else f"{p} is a directory")
        p.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
        tmp = p.with_name(p.name + f".tmp{int(time.time() * 1000)}")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(p)
        return p
    if p.exists() and not p.is_dir():
        raise OutputError(f"{p} exists and is not a directory")
    if p.is_dir() and any(p.iterdir()):
        if not force:
            raise OutputError(f"{p} is not empty (use --force to replace a datapack there)")
        if not (p / "pack.mcmeta").is_file() or not set(x.name for x in p.iterdir()) <= {"pack.mcmeta", "data"}:
            raise OutputError(f"{p} is not empty and does not look like a datapack; refusing even with --force")
        shutil.rmtree(p / "data", ignore_errors=True)
        (p / "pack.mcmeta").unlink(missing_ok=True)
    for name, data in files.items():
        f = p / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return p
