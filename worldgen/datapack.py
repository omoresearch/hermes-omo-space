"""Write the Hermes HQ datapack (Minecraft 26.2 / Omo Space).

Format facts (checked against the 26.2 jar and the user's working packs):
  * pack.mcmeta: pack_format = min_format = max_format = 107 (26.2 version.json
    pack_version.data_major 107; same as every Omo pack in the live save).
  * Folders are singular: data/<ns>/function/*.mcfunction, data/<ns>/structure/*.nbt.
  * Structures: gzip NBT, DataVersion 4903 (26.2 world_version).

Omo blocks come from structure templates (`place template`), because only a
template places a CC:T monitor wall that is already joined (Width/Height/
XIndex/YIndex stored per panel), exactly like omo-space mod/tools/make_kits.py.
Every Hermes Computer carries only an OmoPreset {Name, Kind, Profile, Tabs}:
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

from . import layout as L

PACK_FORMAT = 107
DATA_VERSION = 4903
NAMESPACE_RE = re.compile(r"^[a-z0-9_]{1,48}$")
RESERVED_NAMESPACES = {"minecraft", "omolife", "computercraft", "fabric", "realms"}
MAX_FILL = 32768

# palette (monochrome + gold)
FLOOR = "minecraft:white_concrete"
RUNNER = "minecraft:black_concrete"
WALL = "minecraft:white_concrete"
TRIM = "minecraft:black_concrete"
PILLAR = "minecraft:black_concrete"
WINDOW = "minecraft:glass"
CEILING = "minecraft:smooth_quartz"
LIGHT = "minecraft:sea_lantern"
ACCENT = "minecraft:gold_block"
GLOW = "minecraft:glowstone"


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


def desk_template(profile):
    """5 x 4 x 2: back panel (z 0) and the screen layer (z 1). The Hermes Computer
    sits under the middle of its 3x2 terminal wall (touching only that panel), a
    name plate and a status lamp stand on the desk ends, the cell in front stays open."""
    t = Template(L.DESK_W, 4, L.DESK_D)
    t.fill(0, 0, 0, 4, 3, 0, "minecraft:black_concrete")
    t.put(0, 3, 0, ACCENT)
    t.put(4, 3, 0, ACCENT)
    t.wall(1, 1, 1, 3, 2)
    t.put(2, 0, 1, "omolife:hermes_computer[facing=south]", {"id": "omolife:station", "OmoPreset": preset_for(profile)})
    for x in (0, 1, 3, 4):
        t.put(x, 0, 1, "minecraft:smooth_quartz")
    t.put(0, 1, 1, "omolife:name_plate[face=floor,facing=south]")
    t.put(4, 1, 1, "omolife:status_lamp")
    return t


def board_template(url):
    t = Template(L.BOARD_W, L.BOARD_H, 2)
    t.fill(0, 0, 0, L.BOARD_W - 1, L.BOARD_H - 1, 0, "minecraft:black_concrete")
    for x, y in ((0, 0), (L.BOARD_W - 1, 0), (0, L.BOARD_H - 1), (L.BOARD_W - 1, L.BOARD_H - 1)):
        t.put(x, y, 0, ACCENT)
    t.fill(1, 0, 1, L.BOARD_W - 2, 0, 1, "minecraft:smooth_quartz")
    t.wall(1, 1, 1, L.BOARD_MON_W, L.BOARD_MON_H, url)
    return t


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


def build_files(data, namespace="hermes_hq", bridge_port=8780, max_desks=48):
    """{relative path: bytes} for the whole datapack."""
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
    lay = L.plan_layout(len(shown))
    ns = namespace
    tag, origin = ns, ns + "_origin"
    url = f"http://127.0.0.1:{port}/board"
    h = lay["hall"]
    W, H, D = lay["size"]
    files = {}

    def fn(name, lines):
        files[f"data/{ns}/function/{name}.mcfunction"] = ("\n".join(lines) + "\n").encode("ascii")

    def say(color, text, sel="@s"):
        return f"tellraw {sel} {jtext({'text': clean(text, 240), 'color': color})}"

    # ---- templates
    for i, p in enumerate(shown):
        files[f"data/{ns}/structure/desk_{i}.nbt"] = desk_template(p).to_bytes()
    files[f"data/{ns}/structure/board.nbt"] = board_template(url).to_bytes()

    # labels sit inside the hall plus the title just outside the door (z 0.3): kill box z0-1 .. z1
    kill_box = (f"execute positioned {_c(h['x0'])} {_c(h['y0'])} {_c(h['z0'] - 1)} run kill "
                f"@e[type=minecraft:text_display,tag={tag},dx={W - 1},dy={H + 2},dz={D}]")
    exists = f"@e[type=minecraft:marker,tag={origin}]"

    # ---- build
    fn("build", [
        f"# Hermes HQ ({ns}): {len(shown)} desks. Builds a {W}x{H}x{D} hall from your position toward +Z (south).",
        f"execute if entity {exists} run " + say("red", f"A Hermes HQ from this pack is already built nearby. "
                                                       f"Stand near it and run /function {ns}:clear first."),
        f"execute unless entity {exists} run function {ns}:build_go",
    ])
    fn("build_go", [
        "# warn, then build at the block you stand in",
        say("yellow", f"Hermes HQ: replacing EVERYTHING in a {W} x {H} x {D} box in front of you (toward +Z / south, "
                      f"x {h['x0']}..{h['x1']} relative) with the hall. Nothing outside that box is touched."),
        f"execute align xyz run function {ns}:build_at",
        say("green", f"Hermes HQ built: {len(shown)} desks. Walk south through the door. Open a desk once to "
                     f"make its workspace. Remove it with /function {ns}:clear."),
    ])
    b = ["# clear the hall volume (and old labels) first, then build"]
    b.append(kill_box)
    b += fill_cmds(h["x0"], 0, h["z0"], h["x1"], h["y1"], h["z1"], "minecraft:air")
    xi0, xi1, zi0, zi1 = h["x0"] + 1, h["x1"] - 1, h["z0"] + 1, h["z1"] - 1
    # floor
    b += fill_cmds(h["x0"], -1, h["z0"], h["x1"], -1, h["z1"], FLOOR)
    b += fill_cmds(-1, -1, zi0, 1, -1, zi1, RUNNER)
    for z in range(zi0, zi1 + 1, 6):
        b.append(f"setblock ~ ~-1 {_c(z)} {ACCENT}")
    # walls: black base + top band, white between, glass windows on the sides
    for (xa, za, xb, zb) in ((h["x0"], h["z0"], h["x0"], h["z1"]), (h["x1"], h["z0"], h["x1"], h["z1"]),
                             (h["x0"], h["z0"], h["x1"], h["z0"]), (h["x0"], h["z1"], h["x1"], h["z1"])):
        b += fill_cmds(xa, 0, za, xb, 0, zb, TRIM)
        b += fill_cmds(xa, 1, za, xb, L.INNER_H - 2, zb, WALL)
        b += fill_cmds(xa, L.INNER_H - 1, za, xb, L.INNER_H - 1, zb, TRIM)
    for x in (h["x0"], h["x1"]):
        b += fill_cmds(x, 2, zi0 + 1, x, 3, zi1 - 1, WINDOW)
        for z in range(zi0, zi1 + 1, 6):
            b += fill_cmds(x, 1, z, x, L.INNER_H - 2, z, PILLAR)
            b.append(f"setblock {_c(x)} ~{L.INNER_H - 2} {_c(z)} {ACCENT}")
    # ceiling with a light grid
    b += fill_cmds(h["x0"], L.INNER_H, h["z0"], h["x1"], L.INNER_H, h["z1"], CEILING)
    for x in range(0, xi1 + 1, 4):
        for z in range(zi0 + 1, zi1 + 1, 3):
            for xx in sorted({x, -x}):
                if xi0 <= xx <= xi1:
                    b.append(f"setblock {_c(xx)} ~{L.INNER_H} {_c(z)} {LIGHT}")
    # door with a gold frame
    d = lay["door"]
    b += fill_cmds(d["x0"] - 1, 0, d["z"], d["x1"] + 1, d["y1"] + 1, d["z"], ACCENT)
    b += fill_cmds(d["x0"], d["y0"], d["z"], d["x1"], d["y1"], d["z"], "minecraft:air")
    b.append(f"setblock ~ ~4 {_c(d['z'])} {GLOW}")
    # desks + board (templates: joined monitor walls, preset-only stations)
    for i, desk in enumerate(lay["desks"]):
        X, Y, Z = desk["template_pos"]
        b.append(f"place template {ns}:desk_{i} {_c(X)} {_c(Y)} {_c(Z)} 180")
    X, Y, Z = lay["board"]["template_pos"]
    b.append(f"place template {ns}:board {_c(X)} {_c(Y)} {_c(Z)} 180")
    # labels
    for desk, p in zip(lay["desks"], shown):
        b.append(text_display(desk["text_pos"], desk_lines(p), tag, scale=0.42, line_width=170))
    bz = lay["board"]["monitor_z"]
    b.append(text_display([0.5, L.BOARD_H - 0.45, bz + 0.45],
                          [comp("HERMES BOARD", "gold", 20, bold=True), comp(url, "gray", 60, pre="\n")],
                          tag, scale=0.5))
    title = [comp("HERMES HQ", "gold", 20, bold=True),
             comp(f"{len(shown)} agents \u00b7 one desk per Hermes profile", "white", 60, pre="\n")]
    b.append(text_display([0.5, 3.6, 0.3], title, tag, scale=1.0, line_width=260))
    b.append(text_display([0.5, 3.4, 4.5], title, tag, scale=0.7, line_width=260))
    b.append(f"summon minecraft:marker ~ ~ ~ {{Tags:[{json.dumps(tag)},{json.dumps(origin)}]}}")
    fn("build_at", b)

    # ---- preview: particle outline only, no blocks
    pv = ["# outline the footprint with particles; nothing is changed"]
    x0, x1, z0, z1 = h["x0"], h["x1"], h["z0"], h["z1"]
    pts = set()
    for x in range(x0, x1 + 1):
        pts |= {(x, z0), (x, z1)}
    for z in range(z0, z1 + 1):
        pts |= {(x0, z), (x1, z)}
    for x, z in sorted(pts):
        pv.append(f"particle minecraft:end_rod {_c(x + 0.5)} ~0.2 {_c(z + 0.5)} 0 0 0 0 1 force")
    for x, z in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
        for y in range(1, H):
            pv.append(f"particle minecraft:end_rod {_c(x + 0.5)} {_c(y + 0.2)} {_c(z + 0.5)} 0 0 0 0 1 force")
    for x in range(d["x0"], d["x1"] + 1):
        pv.append(f"particle minecraft:happy_villager {_c(x + 0.5)} ~0.5 {_c(d['z'] + 0.5)} 0.2 0.3 0.2 0 4 force")
    fn("preview_at", pv)
    fn("preview", [
        f"# Hermes HQ ({ns}) preview",
        say("aqua", f"Hermes HQ preview: {len(shown)} desks, a {W} x {H} x {D} hall (x {h['x0']}..{h['x1']}, "
                    f"floor 1 below your feet, {H - 1} high) from here toward +Z / south. The green sparks are the "
                    f"door. Run again to show it again; /function {ns}:build to build."),
        f"execute if entity {exists} run " + say("red", "Note: a Hermes HQ from this pack is already built nearby."),
        f"execute align xyz run function {ns}:preview_at",
    ])

    # ---- clear: only where this pack built (its marker), only the hall volume
    c = ["# remove the hall (above the floor), its labels and the origin marker"]
    c.append(kill_box)
    c += fill_cmds(h["x0"], 0, h["z0"], h["x1"], h["y1"], h["z1"], "minecraft:air")
    c.append("kill @s")
    fn("clear_at", c)
    fn("clear", [
        f"# Hermes HQ ({ns}) clear",
        f"execute unless entity {exists} run " + say("red", "No Hermes HQ from this pack found nearby "
                                                             "(stand inside or next to it)."),
        f"execute if entity {exists} run " + say("yellow", "Removing Hermes HQ (the white floor stays; mine or "
                                                            "/fill it yourself)."),
        f"execute as @e[type=minecraft:marker,tag={origin},limit=1,sort=nearest] at @s run function {ns}:clear_at",
    ])

    # ---- info
    info = [f"# Hermes HQ ({ns}) summary",
            "tellraw @s " + jtext([comp("Hermes HQ ", "gold", 20, bold=True),
                                   comp(f"{len(shown)} desks \u00b7 board {url}", "white", 120)])]
    for p in shown:
        info.append("tellraw @s " + jtext([comp(p["name"], "white", 41, bold=True),
                                           comp(" " + _profile_line(p), "gray", 121)]))
    if hidden:
        info.append(say("yellow", f"{len(hidden)} more profiles not built (--max-desks {max_desks})."))
    info.append(say("gray", f"Commands: /function {ns}:preview, {ns}:build, {ns}:clear, {ns}:info"))
    fn("info", info)

    files["pack.mcmeta"] = (json.dumps({"pack": {
        "pack_format": PACK_FORMAT, "min_format": PACK_FORMAT, "max_format": PACK_FORMAT,
        "description": clean(f"Hermes HQ: {len(shown)} Hermes desks + Hermes Board ({ns})", 120)}},
        indent=1, ensure_ascii=True) + "\n").encode("ascii")
    return dict(sorted(files.items()))


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
