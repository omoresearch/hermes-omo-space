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


# --------------------------------------------------------------------------- layout (v2: cavern hall)
REAL_NAMES = (["default"] + [f"0{i}-studio-{i}" for i in range(1, 7)] + [f"lf-0{i}-x" for i in range(1, 9)]
              + [f"omo-{i:02d}-x" for i in range(1, 30)] + [f"yt-0{i}-x" for i in range(1, 5)])   # 47 like the user's
assert len(REAL_NAMES) == 48


def _kit_world(d, t):
    """A template cell (tx, ty, tz) of desk d in world coordinates (place template rotation)."""
    X, Y, Z = d["template_pos"]
    dx, dz = lay.rot_xz(d["rotation"], t[0], t[2])
    return [X + dx, Y + t[1], Z + dz]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 12, 46, 47, 48, 96])
def test_layout_no_overlap_and_inside(n):
    L = lay.plan_layout(n, REAL_NAMES[:n] if n <= 48 else None)
    assert len(L["desks"]) == n and [d["index"] for d in L["desks"]] == list(range(n))
    h = L["hall"]
    seen = {}
    for d in L["desks"]:
        assert len(d["cells"]) == lay.KIT_W * lay.KIT_H * lay.KIT_D
        for c in d["cells"]:
            assert tuple(c) not in seen, ("kits overlap", d["index"], seen.get(tuple(c)))
            seen[tuple(c)] = d["index"]
            assert h["x0"] < c[0] < h["x1"] and h["z0"] < c[2] < h["z1"] and 0 <= c[1] < lay.KIT_H
        # the template maps the way `place template` does: computer in the frame row, monitors above it
        assert _kit_world(d, (lay.KIT_MID, 0, 0)) == d["station"]
        assert _kit_world(d, (lay.KIT_MID, 0, 1)) == d["front"]
        assert [d["station"][0], 1, d["station"][2]] in d["monitors"]
        assert tuple(d["facing"]) in lay.ROTATIONS and lay.ROTATIONS[tuple(d["facing"])] == d["rotation"]
        assert d["front"] == [d["station"][0] + d["facing"][0], 0, d["station"][2] + d["facing"][1]]
    for b in L["bays"]:
        assert 1 <= len(b["desks"]) <= lay.BAY_CAP
    assert sorted(i for b in L["bays"] for i in b["desks"]) == list(range(n))
    # the player's own spot and everything north of the facade stay untouched
    assert h["z0"] == 1
    # web walls on screen: the Board only (client keeps 12 pages); every monitor wall within the 16 x 12 limit
    assert L["board"]["monitor_w"] <= 16 and L["board"]["monitor_h"] <= 12
    assert lay.SCREEN_W <= 16 and lay.SCREEN_H <= 12


def test_layout_scales_and_stays_compact():
    sizes = {n: lay.plan_layout(n, REAL_NAMES[:n] if n <= 48 else None)["size"] for n in (1, 4, 12, 47, 96)}
    assert sizes[1][0] <= 50 and sizes[1][2] <= 70
    w, _, d = sizes[47]
    assert w <= 100 and d <= 100, sizes[47]                 # a place, not a warehouse
    assert sizes[96][0] * sizes[96][2] <= 2.2 * w * d
    assert all(s[1] <= 40 for s in sizes.values())


def test_groups_make_neighbourhoods():
    names = ["default"] + [f"omo-{i:02d}" for i in range(14)] + ["yt-1", "yt-2", "yt-3", "lf-a", "solo-x", "07-misc"]
    L = lay.plan_layout(len(names), names)
    by = {}
    for b in L["bays"]:
        by.setdefault(b["group"], []).append(b)
    assert set(by) == {"omo", "yt", "misc"}
    assert len(by["omo"]) == 2 and sorted(len(b["desks"]) for b in by["omo"]) == [7, 7]
    assert len({b["theme"] for b in by["omo"]}) == 1                  # one neighbourhood, one theme
    assert len({by[k][0]["theme"] for k in by}) == 3                  # neighbourhoods look different
    misc = {names[i] for b in by["misc"] for i in b["desks"]}
    assert misc == {"default", "lf-a", "solo-x", "07-misc"}
    # neighbourhood bays sit next to each other along the loop
    order = [b["group"] for b in L["bays"]]
    assert order.index("omo") + 1 == len(order) - order[::-1].index("omo") - 1 or order.count("omo") == 1
    assert lay.group_of("Omo_x") == "omo" and lay.group_of("01-x") == "" and lay.group_of("a-b") == ""


@pytest.mark.parametrize("n", [1, 2, 3, 4, 6, 7, 10, 12, 47, 96])
def test_plate_and_lamp_follow_their_own_station(n):
    """omolife's unlinked Name Plate / Status Lamp follow the NEAREST station block within 8 blocks
    (StatusBlockEntity.station(): BlockPos.distSqr, REACH 8). The templates place them unlinked, so in the
    built hall every plate and lamp must be strictly nearer its own desk's computer than any other desk's."""
    REACH = 8
    L = lay.plan_layout(n, REAL_NAMES[:n] if n <= 48 else None)
    profile = {"name": "p", "label": "", "model": "", "skills": 0, "cron_jobs": 0, "platforms": [], "plugins": 0}
    cells = dp.desk_template(profile).cells

    def find(prefix):
        return [pos for pos, (spec, _) in cells.items() if spec.startswith(prefix)]
    (st_t,), (plate_t,), (lamp_t,) = find("omolife:hermes_computer"), find("omolife:name_plate"), \
        find("omolife:status_lamp")
    assert (st_t, plate_t, lamp_t) == ((lay.KIT_MID, 0, 0), lay.PLATE_T, lay.LAMP_T)
    stations = [tuple(_kit_world(d, st_t)) for d in L["desks"]]
    assert stations == [tuple(d["station"]) for d in L["desks"]]
    for i, d in enumerate(L["desks"]):
        assert _kit_world(d, plate_t) == d["plate"] and _kit_world(d, lamp_t) == d["lamp"]
        for follower in (d["plate"], d["lamp"]):
            dist = sorted((sum((a - b) ** 2 for a, b in zip(follower, s)), j) for j, s in enumerate(stations))
            assert dist[0][1] == i and dist[0][0] <= REACH * REACH, (i, follower, dist[:2])
            assert len(dist) == 1 or dist[1][0] > dist[0][0], ("tie", i, follower, dist[:2])


# --------------------------------------------------------------------------- hall model
SOLIDISH_NOT = ("air", "water", "light", "lantern", "iron_chain", "cave_vines", "cave_vines_plant", "spore_blossom",
                "glow_lichen", "amethyst_cluster", "moss_carpet", "short_grass", "fern", "azalea", "flowering_azalea",
                "firefly_bush", "pink_petals", "lily_pad", "seagrass", "sea_pickle")


def _nm(s):
    return s.split("[")[0].split("{")[0].split(":")[-1]


def _solid(s):
    n = _nm(s)
    return not (n in SOLIDISH_NOT or n.endswith("_carpet") or n.endswith("_sign"))


_HALLS = {}


def _hall(n):
    if n not in _HALLS:
        L = lay.plan_layout(n, REAL_NAMES[:n] if n <= 48 else None)
        _HALLS[n] = worldgen.hall.Hall(L)
    return _HALLS[n]


def _world(hl):
    w = dict(hl.cells)
    w.update({c: "minecraft:" + s if ":" not in s.split("[")[0] else s for c, s in hl.world_templates().items()})
    return w


@pytest.mark.parametrize("n", [1, 4, 47, 96])
def test_hall_model_is_inside_and_leaves_the_player_alone(n):
    hl = _hall(n)
    h = hl.lay["hall"]
    for (x, y, z) in list(hl.cells) + list(hl.world_templates()):
        assert h["x0"] <= x <= h["x1"] and h["y0"] <= y <= h["y1"] and h["z0"] <= z <= h["z1"], (x, y, z)
    assert not any(z < 1 for (_, _, z) in hl.cells)
    # arch open, facade carries the glowing HERMES letters
    for x in range(-2, 3):
        for y in range(0, 4):
            assert hl.cells[(x, y, 1)] == "minecraft:air" and hl.cells[(x, y, 2)] == "minecraft:air"
    letters = [c for c, s in hl.cells.items() if c[2] == 1 and _nm(s) == "ochre_froglight"]
    assert len(letters) >= 40
    assert any(_nm(s) == "gold_block" for c, s in hl.cells.items() if c[2] == 1)


@pytest.mark.parametrize("n", [1, 4, 47, 96])
def test_hall_walkway_reaches_every_desk(n):
    """Walk from the arch on floor cells (air at y 0 and 1 over a solid floor, no water): every desk's front is
    reachable and close; the Board is visible straight ahead of the foyer."""
    hl = _hall(n)
    w = _world(hl)

    def walk(x, z):
        a, b, f = w.get((x, 0, z)), w.get((x, 1, z)), w.get((x, -1, z))
        if a is None or b is None or f is None:
            return False
        if not _solid(f):
            return False
        ok0 = not _solid(a) and _nm(a) not in ("water",)
        return ok0 and not _solid(b)
    start = (0, 2)
    assert walk(*start)
    dist = {start: 0}
    q = [start]
    for c in q:
        for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (c[0] + a, c[1] + b)
            if nb not in dist and walk(*nb):
                dist[nb] = dist[c] + 1
                q.append(nb)
    far = 0
    for d in hl.lay["desks"]:
        sx, _, sz = d["station"]
        fx, fz = d["facing"]
        stand = (sx + 2 * fx, sz + 2 * fz)          # in front of the open desk cell
        assert stand in dist, ("desk not reachable", d["index"], d["name"], stand)
        far = max(far, dist[stand])
        assert w[tuple(d["front"])] == "minecraft:air"           # the cell in front of the computer is open
    limit = {1: 70, 4: 70, 47: 110, 96: 160}[n]
    assert far <= limit, far
    # Board monitors face the foyer across the front walkway
    for m in hl.lay["board"]["monitors"]:
        assert _nm(w[tuple(m)]) == "monitor"
        assert w[(m[0], m[1], m[2] - 1)] in ("minecraft:air",) or not _solid(w[(m[0], m[1], m[2] - 1)])


@pytest.mark.parametrize("n", [1, 4, 47, 96])
def test_shell_is_closed(n):
    """Every open cell (air inside the hall) has all 6 neighbours set by the build (or a template), except the
    arch's outward side: the hall never opens into whatever terrain or caves surround it."""
    hl = _hall(n)
    mine = set(hl.cells) | set(hl.tplbox)
    for c in hl.opencells:
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nb = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
            assert nb in mine or (nb[2] == 0 and c[2] == 1 and -2 <= c[0] <= 2), ("open to the outside", c, nb)


@pytest.mark.parametrize("n", [4, 47])
def test_water_is_sealed_and_decor_is_supported(n):
    hl = _hall(n)
    w = _world(hl)
    falls = {(fx, fz) for fx, fz in hl.lay["crag"]["falls"]}
    for (x, y, z), s in w.items():
        if _nm(s) != "water":
            continue
        below = w.get((x, y - 1, z))
        assert below and (_solid(below) or _nm(below) in ("water", "seagrass", "sea_pickle")), ("pond leaks down", x, y, z)
        for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = w.get((x + a, y, z + b))
            if y == hl.lay["crag"]["fall_top"] and (x + a, z + b) in falls:
                continue                                    # the waterfall spills out of its notch here
            assert nb and (_solid(nb) or _nm(nb) in ("water", "seagrass", "sea_pickle")), ("pond leaks", x, y, z, nb)
        for a, b, c in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)):
            assert (x + a, y + b, z + c) not in hl.tplbox
    for (x, y, z), s in w.items():
        n_ = _nm(s)
        if n_ == "lantern" and "hanging=true" in s:
            up = w.get((x, y + 1, z))
            assert up and (_solid(up) or _nm(up) == "iron_chain"), ("lantern hangs from nothing", x, y, z, up)
        elif n_ == "lantern" or n_ in ("short_grass", "fern", "azalea", "flowering_azalea", "moss_carpet",
                                       "firefly_bush", "pink_petals") or n_.endswith("_carpet"):
            assert _solid(w.get((x, y - 1, z), "minecraft:air")), ("floats", n_, x, y, z)
        elif n_ in ("cave_vines", "cave_vines_plant", "iron_chain", "spore_blossom"):
            up = w.get((x, y + 1, z))
            assert up and (_solid(up) or _nm(up) in ("cave_vines_plant", "iron_chain")), (n_, x, y, z, up)
        elif n_ == "lily_pad":
            assert _nm(w[(x, y - 1, z)]) == "water"
    for c in hl.wet:
        assert _nm(w.get(c, "minecraft:air")) in ("air", "water"), ("decor in the waterfall", c, w.get(c))


@pytest.mark.parametrize("n", [4, 47])
def test_hall_is_lit_and_themed(n):
    hl = _hall(n)
    w = _world(hl)
    EMIT = ("light", "lantern", "ochre_froglight", "pearlescent_froglight", "verdant_froglight", "shroomlight",
            "sea_lantern", "waxed_copper_bulb", "cave_vines", "sea_pickle")
    lights = [c for c, s in w.items() if _nm(s) in EMIT]
    assert len(lights) > 40
    import bisect
    lights.sort()
    floor = [(x, 0, z) for (x, y, z), s in w.items() if y == 0 and s == "minecraft:air" and _solid(w.get((x, -1, z), "minecraft:air"))]
    for c in floor:   # a light source within 12 (Manhattan): block light >= 3 everywhere, no mob spawns
        assert any(abs(c[0] - l[0]) + abs(c[1] - l[1]) + abs(c[2] - l[2]) <= 12 for l in lights), c
    # every bay has its own theme on its floor and walls, a glowing signpost and a gold chandelier
    for b in hl.lay["bays"]:
        sx, sz = b["signpost"]
        assert (sx, 1, sz) in hl.signs
        col, lines = hl.signs[(sx, 1, sz)]
        assert col == b["color"] and lines[1] == b["label"]
    golds = sum(1 for s in w.values() if _nm(s) == "gold_block")
    assert golds >= 20
    names = {_nm(s) for s in w.values()}
    for must in ("moss_block", "amethyst_block", "azalea_leaves", "shroomlight", "mangrove_log", "water", "lily_pad",
                 "spore_blossom", "cave_vines", "iron_chain", "light", "polished_blackstone_bricks"):
        assert must in names, must


# --------------------------------------------------------------------------- datapack
MC_ROOTS = {"fill", "setblock", "tellraw", "particle", "summon", "data", "execute", "say", "function",
            "place", "kill", "schedule", "forceload", "tag"}


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
    if root == "forceload" and not (toks[1] in ("add", "remove") and _coords_ok(toks[2:6])):
        probs.append("forceload coords not relative")
    if root == "schedule" and not (toks[1] == "function" and re.match(r"^[a-z0-9_]+:[a-z0-9_]+$", toks[2])
                                   and re.match(r"^\d+t$", toks[3])):
        probs.append("bad schedule")
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
    assert lint_line("forceload add 0 0 ~5 ~5") != []
    assert lint_line("schedule function ns:s_1 2t") == []
    assert lint_line("schedule function ns:s_1 2") != []


@pytest.fixture
def built(fake_root):
    data = inv.collect(fake_root)
    return data, dp.build_files(data, namespace="hermes_hq", bridge_port=8781)


def _build_src(files, ns="hermes_hq"):
    """All build stage functions in order (b_0, b_1, ...)."""
    i, out = 0, []
    while f"data/{ns}/function/b_{i}.mcfunction" in files:
        out.append(files[f"data/{ns}/function/b_{i}.mcfunction"].decode())
        i += 1
    return out


def test_pack_meta_and_layout(built):
    _, files = built
    meta = json.loads(files["pack.mcmeta"])
    assert meta["pack"]["pack_format"] == 107
    assert meta["pack"]["min_format"] == 107 and meta["pack"]["max_format"] == 107
    for f in ("build", "build_at", "b_0", "preview", "clear", "clear_at", "c_0", "info"):
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
        for m in re.finditer(r"(?:(?<![a-z_])text:|messages:|tellraw @[as](?:\[[^\]]*\])? )", line):
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
    chat = {"black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple", "gold", "gray", "dark_gray",
            "blue", "green", "aqua", "red", "light_purple", "yellow", "white"}

    def colors(x):
        if isinstance(x, list):
            for y in x:
                yield from colors(y)
        elif isinstance(x, dict):
            if "color" in x:
                yield x["color"]
    used = set(colors(payloads))
    assert used and used <= chat, used - chat          # dye names (cyan, pink) fail to load the whole function
    assert all(ln.isascii() for ln in src.split("\n"))


def test_desk_labels_have_line_breaks(built):
    data, files = built
    src = "\n".join(_build_src(files))
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
        assert len(mons) == 15 and {(m["nbt"]["Width"], m["nbt"]["Height"]) for m in mons} == {(5, 3)}
        assert {(m["nbt"]["XIndex"], m["nbt"]["YIndex"]) for m in mons} == {(i, j) for i in range(5) for j in range(3)}
        assert not any("OmoUrl" in m["nbt"] for m in mons)
        # the station touches exactly one monitor (bottom middle)
        sp = stations[0]["pos"]
        touching = [m for m in mons if sum(abs(a - b) for a, b in zip(m["pos"], sp)) == 1]
        assert len(touching) == 1 and touching[0]["nbt"]["XIndex"] == 2 and touching[0]["nbt"]["YIndex"] == 0
        # under the screen in the frame row; the desk cell in front of it is open (air) so it stays clickable
        assert touching[0]["pos"] == [sp[0], sp[1] + 1, sp[2]]
        front = [b for b in s["blocks"] if b["pos"] == [sp[0], sp[1], sp[2] + 1]]
        assert front and pal[front[0]["state"]]["Name"] == "minecraft:air"
        # a glowing, waxed sign with the profile name on the top beam
        signs = [b for b in s["blocks"] if pal[b["state"]]["Name"].endswith("_wall_sign")]
        assert len(signs) == 1
        sn = signs[0]["nbt"]
        assert sn["is_waxed"] == 1 and sn["front_text"]["has_glowing_text"] == 1
        lines = [m["text"] for m in sn["front_text"]["messages"]]
        assert len(lines) == 4 and all(len(x) <= 15 for x in lines) and lines[0] == dp.clean(pre.get("Profile", "default"), 15)
        assert all("\n" not in x and "\u00a7" not in x for x in lines)
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


def test_build_warns_then_runs_in_stages(built):
    data, files = built
    ns = "hermes_hq"
    L = lay.plan_layout(len(data["profiles"]), [p["name"] for p in data["profiles"]])
    h = L["hall"]
    fns = {k.split("/")[-1][:-11]: v.decode() for k, v in files.items() if k.endswith(".mcfunction")}
    go = [ln for ln in fns["build_go"].split("\n") if ln and not ln.startswith("#")]
    assert go[0].startswith("tellraw") and "build_at" in go[1]
    at = [ln for ln in fns["build_at"].split("\n") if ln and not ln.startswith("#")]
    assert at[0].startswith("summon minecraft:marker") and "hermes_hq_building" in at[0]
    assert at[1].startswith("forceload add") and at[-1] == f"function {ns}:b_0"
    stages = _build_src(files)
    assert len(stages) >= 2
    for i, src in enumerate(stages):
        cmds = [ln for ln in src.split("\n") if ln and not ln.startswith("#")]
        assert len(cmds) <= dp.STAGE_MAX_CMDS + 200
        if i + 1 < len(stages):
            assert cmds[-1] == f"schedule function {ns}:s_{i + 1} {dp.STAGE_GAP}t"
            s_ = [ln for ln in fns[f"s_{i + 1}"].split("\n") if ln and not ln.startswith("#")]
            assert s_ == [f"execute as @e[type=minecraft:marker,tag={ns}_origin,tag={ns}_building,limit=1] at @s "
                          f"run function {ns}:b_{i + 1}"]
        else:
            assert f"tag @s remove {ns}_building" in cmds and any(c.startswith("forceload remove") for c in cmds)
        for c in cmds:
            if c.startswith("fill"):
                t = c.split(" ")
                x1, y1, z1, x2, y2, z2 = (int(v[1:] or 0) for v in t[1:7])
                assert h["x0"] <= min(x1, x2) and max(x1, x2) <= h["x1"]
                assert h["y0"] <= min(y1, y2) and max(y1, y2) <= h["y1"]
                assert h["z0"] <= min(z1, z2) and max(z1, z2) <= h["z1"]
                assert (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1) * (abs(z2 - z1) + 1) <= 32768
    first = [ln for ln in stages[0].split("\n") if ln and not ln.startswith("#")]
    assert first[0].startswith(("fill", "setblock"))         # the shell first, bottom-up
    clear = fns["clear_at"] + "".join(v for k, v in fns.items() if re.match(r"c_\d+$", k))
    assert "fill" in clear and "place template" not in clear and "kill @s" in clear
    assert "unless entity @s[tag=hermes_hq_clearing]" in fns["clear"]


def _simulate(cmds, world=None):
    """Apply fill/setblock lines (relative to 0 0 0) to a dict; returns (world, touched)."""
    world = {} if world is None else world
    touched = set()
    for c in cmds:
        t = c.split(" ")
        if t[0] == "fill":
            x1, y1, z1, x2, y2, z2 = (int(v[1:] or 0) for v in t[1:7])
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    for z in range(min(z1, z2), max(z1, z2) + 1):
                        if len(t) > 9 and t[8] == "replace":
                            if world.get((x, y, z)) is None or world[(x, y, z)].split("[")[0] != t[9]:
                                continue
                        world[(x, y, z)] = t[7]
                        touched.add((x, y, z))
        elif t[0] == "setblock":
            x, y, z = (int(v[1:] or 0) for v in t[1:4])
            world[(x, y, z)] = t[4].split("{")[0]
            touched.add((x, y, z))
    return world, touched


@pytest.mark.parametrize("n", [4, 47])
def test_compiled_commands_rebuild_the_model_and_clear_only_it(n):
    profs = [{"name": nm, "label": "", "model": "m", "skills": 1, "cron_jobs": 0, "plugins": 0, "platforms": []}
             for nm in REAL_NAMES[:n]]
    files = dp.build_files({"profiles": profs}, bridge_port=8863)
    L = lay.plan_layout(n, REAL_NAMES[:n])
    hl = worldgen.hall.Hall(L, profs, url="http://127.0.0.1:8863/board")
    cmds = [ln for src in _build_src(files) for ln in src.split("\n")]
    world, touched = _simulate(cmds)
    assert world == hl.cells                                  # fills + setblocks == the block model, exactly
    assert not touched & set(hl.tplbox)                       # templates own their cells
    places = [ln for ln in cmds if ln.startswith("place template")]
    assert len(places) == n + 1
    # clear: air over built cells only (y >= 0), pond water back to stone; never outside what the build set
    fns = {k.split("/")[-1][:-11]: v.decode() for k, v in files.items() if k.endswith(".mcfunction")}
    clear = [ln for k, v in sorted(fns.items()) if re.match(r"c_\d+$", k) for ln in v.split("\n")]
    after, cleared = _simulate(clear, dict(world))
    built = set(hl.cells) | set(hl.tplbox)
    assert cleared <= built
    assert {c for c in built if c[1] >= 0} <= cleared
    assert not any(s.startswith("minecraft:water") for s in after.values())


def test_every_label_is_inside_the_kill_box(built):
    """clear must remove every text_display build summoned (selector dx box: [pos, pos+dx+1))."""
    data, files = built
    at = files["data/hermes_hq/function/build_at.mcfunction"].decode()
    m = re.search(r"execute positioned ~(-?\d*) ~(-?\d*) ~(-?\d*) run kill @e\[type=minecraft:text_display,"
                  r"tag=hermes_hq,dx=(\d+),dy=(\d+),dz=(\d+)\]", at)
    assert m
    bx, by, bz, dx, dy, dz = (int(v or 0) for v in m.groups())
    clear = files["data/hermes_hq/function/clear_at.mcfunction"].decode()
    assert m.group(0) in clear
    n = 0
    for line in "\n".join(_build_src(files)).split("\n"):
        if line.startswith("summon minecraft:text_display"):
            x, y, z = (float(v[1:] or 0) for v in line.split(" ")[2:5])
            assert bx <= x < bx + dx + 1 and by <= y < by + dy + 1 and bz <= z < bz + dz + 1, line[:80]
            n += 1
    L = lay.plan_layout(len(data["profiles"]), [p["name"] for p in data["profiles"]])
    assert n == len(data["profiles"]) + 2 + len(L["bays"])


def test_one_web_wall_and_sane_function_count(fake_root):
    data = inv.collect(fake_root)
    files = dp.build_files(data, namespace="hq", bridge_port=8863)
    webs = 0
    for k, v in files.items():
        if k.endswith(".nbt"):
            s = _nbt_read(v)
            webs += sum(1 for b in s["blocks"] if b.get("nbt", {}).get("OmoUrl"))
    assert webs == lay.BOARD_MON_W * lay.BOARD_MON_H          # one 9 x 5 Board, nothing else asks for a page
    profs = [{"name": f"omo-{i:02d}", "label": "", "model": "m", "skills": 1, "cron_jobs": 0, "plugins": 0,
              "platforms": []} for i in range(96)]
    big = dp.build_files({"profiles": profs}, namespace="hq", max_desks=96)
    fns = [k for k in big if k.endswith(".mcfunction")]
    assert len(fns) <= 60
    assert len([k for k in big if "/structure/" in k]) == 97


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
                if t[0] == "fill" and len(t) > 9 and t[8] == "replace":
                    used.add(t[9])
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
