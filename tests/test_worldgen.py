"""Tests for the Hermes HQ world generator (worldgen/).

Everything runs against a fake HERMES root in tmp_path. The fake root holds
canary secrets in .env, auth.json, state.db and a config.yaml token: none of
them may ever appear in any output.
"""
import argparse
import gzip
import io
import json
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
try:   # tests/conftest.py loads the plugin dir as the package omo_space_plugin
    import omo_space_plugin.worldgen as worldgen  # noqa: E402
except ImportError:   # standalone: import worldgen/ as a top-level package
    sys.path.append(str(REPO))
    import worldgen  # noqa: E402
wcli, dp, inv, lay = worldgen.cli, worldgen.datapack, worldgen.inventory, worldgen.layout
REAL_HOME = Path(__import__("pwd").getpwuid(os.getuid()).pw_dir)

CANARY = "CANARY-SECRET-7f3a9c"
CANARY_CFG = "CANARY-CFGTOKEN-55e1"
EVIL_LABEL = 'He said "hi"}]} ; say pwned \\ back\nslash \u00a7cRED\u00a7r ' + "x" * 400 + " \U0001F600 end"


# --------------------------------------------------------------------------- fixtures
def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(base, *parts):
    _w(base.joinpath("skills", *parts, "SKILL.md"), "---\nname: x\n---\n")


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "hroot"
    # default profile (the root itself)
    _w(root / "config.yaml",
       "model:\n  default: claude-test-model\n  provider: anthropic\n"
       "platforms:\n  telegram:\n    enabled: true\n    token: " + CANARY_CFG + "\n"
       "  discord:\n    enabled: false\n  photon: {enabled: true}\n"
       "plugins:\n  enabled:\n    - omo-space\n    - kanban\n  disabled: []\n")
    _w(root / ".env", f"OPENAI_API_KEY={CANARY}\n")
    _w(root / "auth.json", json.dumps({"token": CANARY}))
    (root / "state.db").write_bytes(CANARY.encode() * 10)
    _w(root / "profile.yaml", "description: 'The main desk'\ndescription_auto: false\n")
    _skill(root, "a")
    _skill(root, "cat", "b")
    _skill(root, "cat", "b", "references", "nested")      # support dir of a skill: not a skill
    _skill(root, ".git", "c")                              # excluded dir
    _w(root / "cron" / "jobs.json", json.dumps({"jobs": [{"id": 1, "prompt": CANARY}, {"id": 2}],
                                                "updated_at": "x"}))
    _w(root / "plugins" / "localplug" / "plugin.yaml", "name: localplug\n")
    _w(root / "plugins" / "kanban" / "plugin.yaml", "name: kanban\n")   # also in enabled: counted once

    p = root / "profiles"
    # alpha: string model, evil label, list-form jobs
    _w(p / "alpha" / "config.yaml", "model: gpt-x\n")
    _w(p / "alpha" / "profile.yaml", "description: " + json.dumps(EVIL_LABEL) + "\n")
    _w(p / "alpha" / ".env", f"KEY={CANARY}\n")
    _skill(p / "alpha", "one")
    _w(p / "alpha" / "cron" / "jobs.json", json.dumps([{"id": "j"}]))
    # beta: only a state.db, nothing else
    (p / "beta").mkdir(parents=True)
    (p / "beta" / "state.db").write_bytes(CANARY.encode())
    # gamma: broken yaml + broken json
    _w(p / "gamma" / "config.yaml", "model: [unclosed\n  : : :\n\t- nope")
    _w(p / "gamma" / "cron" / "jobs.json", "{not json")
    _w(p / "gamma" / "profile.yaml", "display_name: Gamma Ray\ndescription: ignored when display_name set\n")
    # not profiles
    (p / "emptydir").mkdir()
    _w(p / "notes.txt", "hi")
    # weird names: valid dir names on Linux, invalid Hermes/bridge profile names
    for bad in ('evil"; say hi', "new\nline", "uni\u00fc", "-dash"):
        _w(p / bad / "config.yaml", "model: m\n")
    monkeypatch.setenv("HERMES_ROOT", str(root))
    monkeypatch.delenv("OMO_PORT", raising=False)
    return root


def _all_bytes(files):
    return b"".join(k.encode() + v for k, v in files.items())


# --------------------------------------------------------------------------- inventory
def test_hermes_root_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path / "r"))
    assert inv.hermes_root() == tmp_path / "r"
    monkeypatch.delenv("HERMES_ROOT")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "base" / "profiles" / "p1"))
    assert inv.hermes_root() == tmp_path / "base"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "base"))
    assert inv.hermes_root() == tmp_path / "base"
    monkeypatch.delenv("HERMES_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert inv.hermes_root() == tmp_path / "home" / ".hermes"


def test_inventory_profiles_and_counts(fake_root):
    data = inv.collect(fake_root)
    names = [p["name"] for p in data["profiles"]]
    assert names == ["default", "alpha", "beta", "gamma"]
    d = data["profiles"][0]
    assert d["model"] == "claude-test-model" and d["provider"] == "anthropic"
    assert d["label"] == "The main desk"
    assert d["skills"] == 2
    assert d["cron_jobs"] == 2
    assert d["platforms"] == ["photon", "telegram"]
    assert d["plugins"] == 3                          # omo-space, kanban, localplug
    a = data["profiles"][1]
    assert a["model"] == "gpt-x" and a["skills"] == 1 and a["cron_jobs"] == 1
    assert a["platforms"] == [] and a["plugins"] == 0
    b = data["profiles"][2]
    assert b["model"] is None and b["skills"] == 0 and b["cron_jobs"] == 0 and b["label"] == ""
    g = data["profiles"][3]
    assert g["label"] == "Gamma Ray" and g["cron_jobs"] == 0
    skipped = {s["name"] for s in data["skipped"]}
    assert skipped == {'evil"; say hi', "new\nline", "uni\u00fc", "-dash"}
    assert all(s["reason"] for s in data["skipped"])


def test_inventory_filters(fake_root):
    data = inv.collect(fake_root, only=["alpha", "default", "nope"], exclude=["default"])
    assert [p["name"] for p in data["profiles"]] == ["alpha"]
    assert "nope" in data["missing"]


def test_inventory_never_reads_secrets(fake_root, monkeypatch):
    opened = []
    real_open = open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", spy)
    real_read_text, real_read_bytes = Path.read_text, Path.read_bytes
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(str(self)), real_read_text(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda self, *a, **k: (opened.append(str(self)), real_read_bytes(self, *a, **k))[1])
    data = inv.collect(fake_root)
    for f in opened:
        base = os.path.basename(f)
        assert base in ("config.yaml", "profile.yaml", "jobs.json", "plugin.yaml") or False, f
    assert CANARY not in json.dumps(data) and CANARY_CFG not in json.dumps(data)


@pytest.mark.parametrize("backend", ["mini", "default"])
def test_yaml_backends_agree(fake_root, monkeypatch, backend):
    if backend == "mini":
        monkeypatch.setattr(inv, "_BACKENDS", [inv._load_mini])
    data = inv.collect(fake_root)
    d = data["profiles"][0]
    assert d["model"] == "claude-test-model"
    assert d["platforms"] == ["photon", "telegram"]
    assert d["plugins"] == 3
    assert data["profiles"][1]["label"].startswith('He said "hi"')


def test_mini_yaml_parser():
    text = ("# c\nmodel:\n  default: 'a: b'   # comment\n  provider: \"x\"\nlist:\n  - one\n  - 'two'\n"
            "flow: [a, b, 'c d']\nnest:\n  deep:\n    k: true\n    n: 3\nempty:\nitems:\n- name: q\n  v: 1\n")
    d = inv._parse_mini(text)
    assert d["model"] == {"default": "a: b", "provider": "x"}
    assert d["list"] == ["one", "two"]
    assert d["flow"] == ["a", "b", "c d"]
    assert d["nest"]["deep"] == {"k": True, "n": 3}
    assert d["empty"] is None
    assert d["items"][0]["name"] == "q"


# --------------------------------------------------------------------------- escaping
def test_sanitize_and_json_text():
    s = dp.clean(EVIL_LABEL, 60)
    assert len(s) <= 60
    assert "\n" not in s and "\u00a7" not in s and "\U0001F600" not in s
    j = dp.jstr('a"b\\c\nd\u00a7e\u00e9')
    assert json.loads(j) == "a\"b\\c de\u00e9"
    assert j.isascii()


# --------------------------------------------------------------------------- layout
@pytest.mark.parametrize("n", [1, 2, 3, 5, 12, 46, 48, 96])
def test_layout_no_overlap_and_inside(n):
    L = lay.plan_layout(n)
    assert len(L["desks"]) == n
    h = L["hall"]
    cells = set()
    for d in L["desks"]:
        fp = {(x, z) for x in range(d["x0"], d["x0"] + lay.DESK_W) for z in (d["zf"], d["zf"] + 1)}
        assert not (fp & cells), "desks overlap"
        # keep a clear walkway in front of every desk
        front = {(x, d["zf"] - 1) for x in range(d["x0"], d["x0"] + lay.DESK_W)}
        assert not (front & cells)
        cells |= fp
        for x, z in fp:
            assert h["x0"] < x < h["x1"] and h["z0"] < z < h["z1"]
    b = L["board"]
    assert h["x0"] < b["x0"] and b["x0"] + b["w"] - 1 < h["x1"]
    assert b["z"] == h["z1"]
    # the central aisle (door to board) is free
    for z in range(h["z0"] + 1, h["z1"]):
        assert (0, z) not in cells
    # desks are at least 4 apart so each status lamp follows its own station
    st = [(d["station"][0], d["station"][2]) for d in L["desks"]]
    for i, a in enumerate(st):
        for c in st[i + 1:]:
            assert abs(a[0] - c[0]) + abs(a[1] - c[1]) >= 5


# --------------------------------------------------------------------------- datapack
MC_ROOTS = {"fill", "setblock", "tellraw", "particle", "summon", "data", "execute", "say", "function",
            "place", "kill"}


def _scan_balance(line):
    """String-aware bracket balance; returns error string or None."""
    stack, q, esc = [], None, False
    pairs = {"}": "{", "]": "["}
    for ch in line:
        if q:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or stack.pop() != pairs[ch]:
                return "unbalanced " + ch
    if q:
        return "unterminated string"
    if stack:
        return "unclosed " + "".join(stack)
    return None


def _coords_ok(tokens):
    return all(t.startswith("~") for t in tokens)


def lint_line(line):
    """Return a list of problems for one mcfunction line."""
    probs = []
    if not line.strip() or line.startswith("#"):
        return probs
    if line != line.strip():
        probs.append("leading/trailing whitespace")
    if line.startswith("$"):
        probs.append("macro line")
    err = _scan_balance(line)
    if err:
        probs.append(err)
    toks = line.split(" ")
    root = toks[0]
    if root not in MC_ROOTS:
        probs.append("unknown root " + root)
    if re.search(r"[\[,](x|y|z)=", line):
        probs.append("absolute selector position")
    if root == "fill" and not _coords_ok(toks[1:7]):
        probs.append("fill coords not relative")
    if root == "setblock" and not _coords_ok(toks[1:4]):
        probs.append("setblock coords not relative")
    if root == "summon" and not _coords_ok(toks[2:5]):
        probs.append("summon coords not relative")
    if root == "particle" and not _coords_ok(toks[2:5]):
        probs.append("particle coords not relative")
    if root == "place" and not (toks[1] == "template" and _coords_ok(toks[3:6])):
        probs.append("place coords not relative")
    if root == "execute":
        for i, t in enumerate(toks):
            if t == "positioned" and not _coords_ok(toks[i + 1:i + 4]):
                probs.append("positioned not relative")
        if " run " in line:
            probs += lint_line(line.split(" run ", 1)[1])
    return probs


def test_linter_catches_bad_lines():
    assert lint_line('tellraw @a {"text":"a"}') == []
    assert lint_line('tellraw @a {"text":"a}') != []
    assert lint_line("fill 0 0 0 ~1 ~1 ~1 stone") != []
    assert lint_line("gamemode creative") != []
    assert lint_line('summon text_display ~ ~ ~ {text:{"text":"x"}') != []


@pytest.fixture
def built(fake_root):
    data = inv.collect(fake_root)
    return data, dp.build_files(data, namespace="hermes_hq", bridge_port=8781)


def test_pack_meta_and_layout(built):
    _, files = built
    meta = json.loads(files["pack.mcmeta"])
    assert meta["pack"]["pack_format"] == 107
    assert meta["pack"]["min_format"] == 107 and meta["pack"]["max_format"] == 107
    for f in ("build", "build_at", "preview", "clear", "clear_at", "info"):
        assert f"data/hermes_hq/function/{f}.mcfunction" in files
    assert not any("/functions/" in k or "/structures/" in k for k in files)
    assert all(k == "pack.mcmeta" or k.startswith("data/hermes_hq/") for k in files)


def test_mcfunction_lint(built):
    _, files = built
    n = 0
    for k, v in files.items():
        if k.endswith(".mcfunction"):
            for i, line in enumerate(v.decode("utf-8").split("\n")):
                assert lint_line(line) == [], (k, i, line[:200])
                n += 1
    assert n > 50


def test_no_secrets_or_paths_in_pack(built, fake_root):
    _, files = built
    blob = _all_bytes(files)
    for f in files.values():
        if f[:2] == b"\x1f\x8b":
            blob += gzip.decompress(f)
    assert CANARY.encode() not in blob and CANARY_CFG.encode() not in blob
    assert str(fake_root).encode() not in blob
    assert b"/home/" not in blob


def _text_payloads(src):
    """Every text component (JSON) after 'text:' or a tellraw selector, parsed."""
    out = []
    for line in src.split("\n"):
        for m in re.finditer(r"(?:text:|tellraw @[as] )", line):
            i = m.end()
            depth, q, esc, j = 0, False, False, i
            for j in range(i, len(line)):
                ch = line[j]
                if q:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        q = False
                    continue
                if ch == '"':
                    q = True
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                    if depth == 0:
                        break
            out.append(json.loads(line[i:j + 1]))
    return out


def test_text_components_escape_user_strings(built):
    data, files = built
    src = "\n".join(v.decode() for k, v in files.items() if k.endswith(".mcfunction"))
    payloads = _text_payloads(src)
    assert len(payloads) > 5
    flat = json.dumps(payloads, ensure_ascii=False)
    assert 'He said \\"hi\\"}]} ; say pwned' in flat
    assert "\u00a7" not in flat
    assert "x" * 200 not in flat           # clamped
    assert "say pwned" not in [ln.split(" ")[0] for ln in src.split("\n")]
    assert all(ln.isascii() for ln in src.split("\n"))


def test_desk_labels_have_line_breaks(built):
    data, files = built
    src = files["data/hermes_hq/function/build_at.mcfunction"].decode()
    desks = [pl for pl in _text_payloads(src) if isinstance(pl, list) and pl[0]["text"].rstrip("\n") == "default"]
    assert len(desks) == 1
    texts = [c["text"] for c in desks[0]]
    assert texts[0] == "default\n" and texts[1] == "The main desk\n" and texts[2] == "claude-test-model\n"
    assert texts[3] == "2 skills \u00b7 2 cron \u00b7 3 plugins" and texts[4] == "\nphoton, telegram"
    # user text never carries its own line break
    evil = [pl for pl in _text_payloads(src) if isinstance(pl, list) and pl[0]["text"] == "alpha\n"][0]
    assert evil[1]["text"].count("\n") == 1 and evil[1]["text"].endswith("\u2026\n")


def _nbt_read(raw):
    """Minimal NBT reader for the generated structures."""
    buf = io.BytesIO(gzip.decompress(raw))

    def rd(fmt):
        return struct.unpack(fmt, buf.read(struct.calcsize(fmt)))

    def payload(t):
        if t == 1:
            return rd(">b")[0]
        if t == 3:
            return rd(">i")[0]
        if t == 4:
            return rd(">q")[0]
        if t == 6:
            return rd(">d")[0]
        if t == 8:
            (n,) = rd(">H")
            return buf.read(n).decode("utf-8")
        if t == 9:
            k, n = rd(">bi")
            return [payload(k) for _ in range(n)]
        if t == 10:
            d = {}
            while True:
                (k,) = rd(">b")
                if k == 0:
                    return d
                (n,) = rd(">H")
                name = buf.read(n).decode("utf-8")
                d[name] = payload(k)
        if t == 11:
            (n,) = rd(">i")
            return list(rd(f">{n}i"))
        raise ValueError(t)

    assert rd(">b")[0] == 10
    (n,) = rd(">H")
    buf.read(n)
    return payload(10)


OMO_IDS = {"omolife:hermes_computer", "omolife:terminal", "omolife:monitor", "omolife:slim_screen",
           "omolife:name_plate", "omolife:status_lamp"}


def test_desk_templates_are_presets_only(built):
    data, files = built
    desks = sorted(k for k in files if re.search(r"/structure/desk_\d+\.nbt$", k))
    assert len(desks) == 4
    seen = {}
    for k in desks:
        s = _nbt_read(files[k])
        assert s["DataVersion"] == 4903
        pal = s["palette"]
        for e in pal:
            assert e["Name"].startswith("minecraft:") or e["Name"] in OMO_IDS
        stations = [b for b in s["blocks"] if pal[b["state"]]["Name"] == "omolife:hermes_computer"]
        assert len(stations) == 1
        nbt = stations[0]["nbt"]
        assert nbt["id"] == "omolife:station"
        assert set(nbt) == {"id", "OmoPreset"}               # no Workspace / Station / WorkspaceName
        pre = nbt["OmoPreset"]
        assert pre["Kind"] == "hermes"
        assert pre["Tabs"] == [{"Name": "Hermes", "Kind": "hermes"}, {"Name": "Shell", "Kind": "shell"}]
        seen[pre.get("Profile", "default")] = pre["Name"]
        mons = [b for b in s["blocks"] if pal[b["state"]]["Name"] == "omolife:monitor"]
        assert len(mons) == 6 and {(m["nbt"]["Width"], m["nbt"]["Height"]) for m in mons} == {(3, 2)}
        assert {(m["nbt"]["XIndex"], m["nbt"]["YIndex"]) for m in mons} == {(i, j) for i in range(3) for j in range(2)}
        assert not any("OmoUrl" in m["nbt"] for m in mons)
        # the station touches exactly one monitor (bottom middle)
        sp = stations[0]["pos"]
        touching = [m for m in mons if sum(abs(a - b) for a, b in zip(m["pos"], sp)) == 1]
        assert len(touching) == 1 and touching[0]["nbt"]["XIndex"] == 1 and touching[0]["nbt"]["YIndex"] == 0
    assert set(seen) == {"default", "alpha", "beta", "gamma"}


def test_default_desk_has_no_profile_key(built):
    _, files = built
    s = _nbt_read(files["data/hermes_hq/structure/desk_0.nbt"])
    pal = s["palette"]
    st = [b for b in s["blocks"] if pal[b["state"]]["Name"] == "omolife:hermes_computer"][0]
    assert "Profile" not in st["nbt"]["OmoPreset"]
    s1 = _nbt_read(files["data/hermes_hq/structure/desk_1.nbt"])
    st1 = [b for b in s1["blocks"] if s1["palette"][b["state"]]["Name"] == "omolife:hermes_computer"][0]
    assert st1["nbt"]["OmoPreset"]["Profile"] == "alpha"
    assert len(st1["nbt"]["OmoPreset"]["Name"]) <= 40


def test_board_template_url(built):
    _, files = built
    s = _nbt_read(files["data/hermes_hq/structure/board.nbt"])
    mons = [b for b in s["blocks"] if s["palette"][b["state"]]["Name"] == "omolife:monitor"]
    assert mons and all(m["nbt"]["OmoUrl"] == "http://127.0.0.1:8781/board" and m["nbt"]["OmoSource"] == "web"
                        for m in mons)
    w, h = mons[0]["nbt"]["Width"], mons[0]["nbt"]["Height"]
    assert len(mons) == w * h and w <= 16 and h <= 12


def test_build_warns_then_clears_only_hall(built):
    data, files = built
    L = lay.plan_layout(len(data["profiles"]))
    h = L["hall"]
    b = files["data/hermes_hq/function/build_go.mcfunction"].decode()
    cmds = [ln for ln in b.split("\n") if ln and not ln.startswith("#")]
    assert cmds[0].startswith("tellraw") and "build_at" in cmds[1]
    at = files["data/hermes_hq/function/build_at.mcfunction"].decode()
    cmds = [ln for ln in at.split("\n") if ln and not ln.startswith("#")]
    first_fill = next(i for i, c in enumerate(cmds) if c.startswith("fill"))
    assert all(not c.startswith(("setblock", "place", "summon")) for c in cmds[:first_fill])
    for c in cmds:
        if c.startswith("fill"):
            t = c.split(" ")
            x1, y1, z1, x2, y2, z2 = (int(v[1:] or 0) for v in t[1:7])
            assert h["x0"] <= min(x1, x2) and max(x1, x2) <= h["x1"]
            assert h["y0"] <= min(y1, y2) and max(y1, y2) <= h["y1"]
            assert h["z0"] <= min(z1, z2) and max(z1, z2) <= h["z1"]
            assert (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1) * (abs(z2 - z1) + 1) <= 32768
    clear = files["data/hermes_hq/function/clear_at.mcfunction"].decode()
    assert "fill" in clear and "place" not in clear


def test_every_label_is_inside_the_kill_box(built):
    """clear must remove every text_display build summoned (selector dx box: [pos, pos+dx+1))."""
    _, files = built
    at = files["data/hermes_hq/function/build_at.mcfunction"].decode()
    m = re.search(r"execute positioned ~(-?\d*) ~(-?\d*) ~(-?\d*) run kill @e\[type=minecraft:text_display,"
                  r"tag=hermes_hq,dx=(\d+),dy=(\d+),dz=(\d+)\]", at)
    assert m
    bx, by, bz, dx, dy, dz = (int(v or 0) for v in m.groups())
    clear = files["data/hermes_hq/function/clear_at.mcfunction"].decode()
    assert m.group(0) in clear
    n = 0
    for line in at.split("\n"):
        if line.startswith("summon minecraft:text_display"):
            x, y, z = (float(v[1:] or 0) for v in line.split(" ")[2:5])
            assert bx <= x < bx + dx + 1 and by <= y < by + dy + 1 and bz <= z < bz + dz + 1, line[:80]
            n += 1
    assert n == 4 + 3


def test_block_ids_exist_in_26_2(built):
    jar = REAL_HOME / ".local/share/omo/runtime/prism/libraries/com/mojang/minecraft/26.2/minecraft-26.2-client.jar"
    if not jar.exists():
        pytest.skip("26.2 client jar not present")
    with zipfile.ZipFile(jar) as z:
        known = {"minecraft:" + n.rsplit("/", 1)[1][:-5] for n in z.namelist()
                 if n.startswith("assets/minecraft/blockstates/") and n.endswith(".json")}
    _, files = built
    used = set()
    for k, v in files.items():
        if k.endswith(".mcfunction"):
            for c in v.decode().split("\n"):
                t = c.split(" ")
                if t[0] == "fill":
                    used.add(t[7].split("[")[0])
                elif t[0] == "setblock":
                    used.add(t[4].split("[")[0].split("{")[0])
        elif k.endswith(".nbt"):
            for e in _nbt_read(v)["palette"]:
                if e["Name"].startswith("minecraft:"):
                    used.add(e["Name"])
    used = {u if ":" in u else "minecraft:" + u for u in used}
    assert used and used <= known, used - known


def test_max_desks_truncates(fake_root):
    data = inv.collect(fake_root)
    files = dp.build_files(data, namespace="hq2", bridge_port=8780, max_desks=2)
    assert len([k for k in files if "/structure/desk_" in k]) == 2
    info = files["data/hq2/function/info.mcfunction"].decode()
    assert "2 more" in info or "not built" in info


@pytest.mark.parametrize("ns", ["Bad", "a-b", "", "x y", "minecraft", "a" * 70, "../x", "hq\n", "hq\nsay pwned"])
def test_bad_namespace_rejected(ns, fake_root, tmp_path, capsys):
    rc = _run(["world", "--namespace", ns, "--out", str(tmp_path / "o")])
    assert rc == 2
    assert not (tmp_path / "o").exists()


# --------------------------------------------------------------------------- CLI
def _run(argv):
    parser = argparse.ArgumentParser(prog="hermes omo")
    sub = parser.add_subparsers(dest="omo_command")
    p = wcli.add_parser(sub)
    assert p is not None
    args = parser.parse_args(argv)
    return wcli.run(args)


def test_cli_dry_run_json(fake_root, capsys, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    rc = _run(["world", "--dry-run", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    d = json.loads(out)
    assert [p["name"] for p in d["inventory"]["profiles"]] == ["default", "alpha", "beta", "gamma"]
    assert d["layout"]["desk_count"] == 4
    assert d["board_url"] == "http://127.0.0.1:8780/board"
    assert d["pack_format"] == 107
    assert CANARY not in out and CANARY_CFG not in out
    assert not any(cwd.iterdir())                  # nothing written


def test_cli_port_from_env(fake_root, capsys, monkeypatch):
    monkeypatch.setenv("OMO_PORT", "8799")
    assert _run(["world", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["board_url"] == "http://127.0.0.1:8799/board"
    assert _run(["world", "--dry-run", "--json", "--bridge-port", "8781"]) == 0
    assert json.loads(capsys.readouterr().out)["board_url"] == "http://127.0.0.1:8781/board"
    assert _run(["world", "--dry-run", "--bridge-port", "70000"]) == 2


def test_cli_writes_dir_and_refuses_overwrite(fake_root, capsys, tmp_path):
    out = tmp_path / "pack"
    assert _run(["world", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert (out / "pack.mcmeta").is_file()
    assert (out / "data/hermes_hq/function/build.mcfunction").is_file()
    assert "/reload" in text and "hermes_hq:preview" in text and "hermes_hq:build" in text and "datapacks" in text
    assert CANARY not in text
    # second run refuses, --force replaces
    assert _run(["world", "--out", str(out)]) == 1
    (out / "data/hermes_hq/function/stale.mcfunction").write_text("say stale\n")
    assert _run(["world", "--out", str(out), "--force"]) == 0
    assert not (out / "data/hermes_hq/function/stale.mcfunction").exists()
    # a non-empty dir that is not a datapack is never overwritten, even with --force
    other = tmp_path / "notapack"
    _w(other / "important.txt", "keep")
    assert _run(["world", "--out", str(other), "--force"]) == 1
    assert (other / "important.txt").read_text() == "keep"
    assert sorted(p.name for p in other.iterdir()) == ["important.txt"]


def test_cli_zip(fake_root, capsys, tmp_path):
    out = tmp_path / "hq.zip"
    assert _run(["world", "--zip", "--out", str(out), "--namespace", "my_hq"]) == 0
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "pack.mcmeta" in names and "data/my_hq/function/build.mcfunction" in names
        assert CANARY.encode() not in b"".join(z.read(n) for n in names)
    assert _run(["world", "--zip", "--out", str(out)]) == 1
    assert _run(["world", "--zip", "--out", str(tmp_path / "noext")]) == 0
    assert (tmp_path / "noext.zip").is_file()


def test_cli_profiles_filter(fake_root, capsys):
    assert _run(["world", "--dry-run", "--json", "--profiles", "alpha,beta", "--exclude", "beta"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in d["inventory"]["profiles"]] == ["alpha"]


def test_cli_no_profiles(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path / "missing"))
    assert _run(["world", "--dry-run"]) == 1


def test_human_output_escapes_weird_names(fake_root, capsys):
    assert _run(["world", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "new\nline" not in out            # shown escaped, never raw
    assert CANARY not in out


def test_package_exports():
    assert worldgen.PACK_FORMAT == 107


def test_trailing_newline_names_are_rejected(fake_root):
    """`$` also matches before a trailing newline; a newline in a profile name, platform name or
    namespace must never pass validation (a newline ends an mcfunction command)."""
    assert not inv.PROFILE_RE.match("work\n")
    assert not inv._PLATFORM_RE.match("telegram\n")
    assert not dp.NAMESPACE_RE.match("hq\n")
    with pytest.raises(ValueError):
        dp.build_files(inv.collect(fake_root), namespace="hq\n")
    (fake_root / "profiles" / "evil\n").mkdir(parents=True)
    (fake_root / "profiles" / "evil\n" / "config.yaml").write_text("model: x\n")
    data = inv.collect(fake_root)
    assert "evil\n" not in [p["name"] for p in data["profiles"]]
    assert "evil\n" in [s["name"] for s in data["skipped"]]
