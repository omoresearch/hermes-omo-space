"""`hermes omo create`: a new world with Hermes HQ in it (fake saves dir, fake Hermes root)."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import struct
from pathlib import Path

import pytest


def _parse(plugin, argv):
    parser = argparse.ArgumentParser(prog="hermes")
    subs = parser.add_subparsers(dest="command")
    plugin.cli.setup_parser(subs.add_parser("omo"))
    return parser.parse_args(["omo", "create", *argv])


def _read_nbt(path: Path):
    """Minimal reader: returns the root compound as plain Python."""
    f = io.BytesIO(gzip.decompress(path.read_bytes()))

    def r(n):
        return f.read(n)

    def payload(t):
        if t == 1:
            return struct.unpack(">b", r(1))[0]
        if t == 2:
            return struct.unpack(">h", r(2))[0]
        if t == 3:
            return struct.unpack(">i", r(4))[0]
        if t == 4:
            return struct.unpack(">q", r(8))[0]
        if t == 5:
            return struct.unpack(">f", r(4))[0]
        if t == 6:
            return struct.unpack(">d", r(8))[0]
        if t == 8:
            n = struct.unpack(">H", r(2))[0]
            return r(n).decode()
        if t == 9:
            et, n = struct.unpack(">bi", r(5))
            return [payload(et) for _ in range(n)]
        if t == 10:
            d = {}
            while True:
                ct = struct.unpack(">b", r(1))[0]
                if ct == 0:
                    return d
                n = struct.unpack(">H", r(2))[0]
                key = r(n).decode()
                d[key] = payload(ct)
        if t == 11:
            n = struct.unpack(">i", r(4))[0]
            return list(struct.unpack(f">{n}i", r(4 * n)))
        raise ValueError(t)

    assert struct.unpack(">b", r(1))[0] == 10
    r(struct.unpack(">H", r(2))[0])
    return payload(10)


CANARY = "CANARY-SECRET-create-91c"


@pytest.fixture(autouse=True)
def hermes_root(tmp_path, monkeypatch):
    """A Hermes root with 5 profiles (two neighbourhoods) and canary secrets that must never leak."""
    root = tmp_path / "hroot"
    root.mkdir()
    (root / "config.yaml").write_text("model:\n  default: m1\n  provider: p\n")
    (root / ".env").write_text(f"KEY={CANARY}\n")
    (root / "auth.json").write_text(json.dumps({"t": CANARY}))
    for n in ("omo-a", "omo-b", "omo-c", "yt-1", "solo"):
        d = root / "profiles" / n
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(f"model:\n  default: m-{n}\n")
        (d / ".env").write_text(f"KEY={CANARY}\n")
    monkeypatch.setenv("HERMES_ROOT", str(root))
    return root


@pytest.fixture
def saves(tmp_path):
    d = tmp_path / "saves"
    d.mkdir()
    return d


def _run(plugin, argv, ask=None, interactive=False):
    args = _parse(plugin, argv)
    out, err = io.StringIO(), io.StringIO()
    answers = iter(ask or [])
    rc = plugin.create.run(args, board_port=8790, out=out, err=err, ask=lambda _q: next(answers),
                           interactive=interactive, use_curses=False)
    return rc, out.getvalue(), err.getvalue()


def test_create_yes_makes_world_and_pack(plugin, hermes_home, saves):
    rc, out, err = _run(plugin, ["--saves-dir", str(saves), "--name", "My HQ", "--style", "library",
                                 "--ground", "snow", "--time", "night", "--yes"])
    assert rc == 0, err
    world = saves / "My HQ"
    assert {p.name for p in world.iterdir()} == {"level.dat", "data", "datapacks", "icon.png"}
    assert {p.name for p in (world / "datapacks").iterdir()} == {"hermes_hq"}
    assert (world / "icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    lvl = _read_nbt(world / "level.dat")["Data"]
    assert lvl["LevelName"] == "My HQ" and lvl["GameType"] == 1 and lvl["allowCommands"] == 1
    assert lvl["Time"] == 18000 and lvl["spawn"]["pos"] == [0, -60, -10]
    assert lvl["DataVersion"] == 4903
    gen = _read_nbt(world / "data/minecraft/world_gen_settings.dat")["data"]
    ow = gen["dimensions"]["minecraft:overworld"]["generator"]
    assert ow["type"] == "minecraft:flat" and ow["settings"]["layers"][-1]["block"] == "minecraft:snow_block"
    rules = _read_nbt(world / "data/minecraft/game_rules.dat")["data"]
    assert rules["minecraft:advance_time"] == 0 and rules["minecraft:spawn_monsters"] == 0
    pack = world / "datapacks/hermes_hq"
    tick = json.loads((pack / "data/minecraft/tags/function/tick.json").read_text())
    assert tick == {"values": ["hermes_hq:arrival_tick"]}
    join = (pack / "data/hermes_hq/function/arrival_join.mcfunction").read_text()
    assert "time set 18000" in join and "My HQ" in join
    build = (pack / "data/hermes_hq/function/arrival_build.mcfunction").read_text()
    assert "positioned 0 -60 0 run function hermes_hq:build_at" in build
    assert "Ready" in out and "My HQ" in out


def test_create_never_touches_existing_world(plugin, hermes_home, saves):
    old = saves / "Taken"
    old.mkdir()
    (old / "level.dat").write_bytes(b"keep")
    rc, out, err = _run(plugin, ["--saves-dir", str(saves), "--name", "Taken", "--yes"])
    assert rc == 1 and "already exists" in err
    assert (old / "level.dat").read_bytes() == b"keep" and not (old / "datapacks").exists()


def test_default_name_is_unique(plugin, hermes_home, saves):
    (saves / "Hermes HQ").mkdir()
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--json"])
    assert rc == 0
    res = json.loads(out)
    assert res["name"] == "Hermes HQ 2" and (saves / "Hermes HQ 2" / "level.dat").is_file()


def test_interactive_answers_drive_the_world(plugin, hermes_home, saves):
    # name, style 4 (studio), ground 3 (desert), time 4 (night), confirm
    rc, out, err = _run(plugin, ["--saves-dir", str(saves)], ask=["Studio", "4", "3", "4", "1", ""],
                        interactive=True)
    assert rc == 0, err
    assert "1/5" in out and "5/5" in out
    res_desks = (saves / "Studio/datapacks/hermes_hq/data/hermes_hq/function/info.mcfunction").read_text()
    assert "omo-a" in res_desks and "yt-1" not in res_desks
    lvl = _read_nbt(saves / "Studio" / "level.dat")["Data"]
    assert lvl["Time"] == 18000
    gen = _read_nbt(saves / "Studio" / "data/minecraft/world_gen_settings.dat")["data"]
    assert gen["dimensions"]["minecraft:overworld"]["generator"]["settings"]["biome"] == "minecraft:desert"


def test_interactive_decline_writes_nothing(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["", "", "", "", "", "n"], interactive=True)
    assert rc == 0 and "Nothing written" in out
    assert list(saves.iterdir()) == []


def test_ctrl_c_writes_nothing(plugin, hermes_home, saves):
    def boom(_q):
        raise KeyboardInterrupt
    args = _parse(plugin, ["--saves-dir", str(saves)])
    out = io.StringIO()
    rc = plugin.create.run(args, out=out, err=io.StringIO(), ask=boom, interactive=True, use_curses=False)
    assert rc == 130 and "nothing written" in out.getvalue()
    assert list(saves.iterdir()) == []


def test_bad_answers_are_reasked(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--name", "X", "--ground", "grass", "--time", "noon"],
                      ask=["9", "zz", "2", "", ""], interactive=True)
    assert rc == 0 and "Type one of the numbers" in out
    assert json.loads(_run(plugin, ["--saves-dir", str(saves), "--name", "Y", "--json"])[1])["style"] == "falls"


def test_style_changes_bay_palette(plugin, hermes_home, saves):
    a = json.loads(_run(plugin, ["--saves-dir", str(saves), "--name", "A", "--style", "falls", "--json"])[1])
    b = json.loads(_run(plugin, ["--saves-dir", str(saves), "--name", "B", "--style", "forge", "--json"])[1])
    fa = (saves / "A/datapacks/hermes_hq/data/hermes_hq/structure/desk_0.nbt").read_bytes()
    fb = (saves / "B/datapacks/hermes_hq/data/hermes_hq/structure/desk_0.nbt").read_bytes()
    assert a["desks"] == b["desks"] and fa != fb


def test_json_output_and_no_secrets(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--name", "J", "--json"])
    res = json.loads(out)
    assert rc == 0 and res["world"] == "J" and res["board_url"] == "http://127.0.0.1:8790/board"
    blob = b"".join(p.read_bytes() for p in (saves / "J").rglob("*") if p.is_file())
    assert b"CANARY" not in blob


def test_folder_name_is_safe(plugin):
    from importlib import import_module
    nw = import_module(plugin.__name__ + ".worldgen.newworld")
    assert nw.folder_name("../../etc") == "etc"
    assert nw.folder_name("a/b\\c:d") == "abcd"
    assert nw.folder_name("   ") == "Hermes HQ"
