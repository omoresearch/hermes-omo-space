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


class FakeResult:
    def __init__(self, parsed):
        self.parsed, self.text = parsed, ""


def fake_llm(plan=None, calls=None):
    def call(**kw):
        if calls is not None:
            calls.append(kw)
        if plan == "boom":
            raise RuntimeError("provider down")
        return FakeResult(plan)
    return call


GOOD_PLAN = {"name": "Snow Library", "tagline": "Quiet stacks.", "style": "library", "ground": "snow",
             "time": "night", "rooms": [
                 {"name": "Stacks", "theme": "library", "about": "the omo crew", "profiles": ["omo-a", "omo-b"]},
                 {"name": "Reading Nook", "theme": "birch", "profiles": ["yt-1", "omo-c", "ghost"]}]}


def _run(plugin, argv, ask=None, interactive=False, llm=None):
    args = _parse(plugin, argv)
    out, err = io.StringIO(), io.StringIO()
    answers = iter(ask or [])
    rc = plugin.create.run(args, board_port=8790, llm=llm, out=out, err=err, ask=lambda _q: next(answers),
                           interactive=interactive)
    return rc, out.getvalue(), err.getvalue()


def test_designed_world_is_built(plugin, hermes_home, saves):
    calls = []
    rc, out, err = _run(plugin, ["--saves-dir", str(saves), "--yes", "a", "snowy", "library"],
                        llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0, err
    assert "Wish: a snowy library" in calls[0]["input"][0]["text"]
    assert CANARY not in json.dumps(calls)                      # the model never sees secrets
    world = saves / "Snow Library"
    assert {x.name for x in world.iterdir()} == {"level.dat", "data", "datapacks", "icon.png", "omo-plan.json"}
    plan = json.loads((world / "omo-plan.json").read_text())
    assert [r["name"] for r in plan["rooms"]] == ["Stacks", "Reading Nook", "Commons"]   # 'solo' and default
    assert plan["rooms"][1]["profiles"] == ["omo-c", "yt-1"] or plan["rooms"][1]["profiles"] == ["yt-1", "omo-c"]
    lvl = _read_nbt(world / "level.dat")["Data"]
    assert lvl["LevelName"] == "Snow Library" and lvl["GameType"] == 1 and lvl["Time"] == 18000
    gen = _read_nbt(world / "data/minecraft/world_gen_settings.dat")["data"]
    assert gen["dimensions"]["minecraft:overworld"]["generator"]["settings"]["layers"][-1]["block"] == \
        "minecraft:snow_block"
    pack = world / "datapacks/hermes_hq"
    tick = json.loads((pack / "data/minecraft/tags/function/tick.json").read_text())
    assert tick == {"values": ["hermes_hq:arrival_tick"]}
    join = (pack / "data/hermes_hq/function/arrival_join.mcfunction").read_text()
    assert "Quiet stacks." in join and "Snow Library" in join
    info = (pack / "data/hermes_hq/function/info.mcfunction").read_text()
    assert "Stacks" in info and "Reading Nook" in info
    assert "Ready" in out


def test_no_model_falls_back_to_names(plugin, hermes_home, saves):
    rc, out, err = _run(plugin, ["--saves-dir", str(saves), "--json"], llm=None)
    res = json.loads(out)
    assert rc == 0 and res["designed_by"] == "fallback"
    rooms = {r["name"]: r["profiles"] for r in res["plan"]["rooms"]}
    assert rooms["Omo Studio"] == ["omo-a", "omo-b", "omo-c"]
    assert sorted(sum(rooms.values(), [])) == sorted(["default", "omo-a", "omo-b", "omo-c", "yt-1", "solo"])


def test_model_failure_still_builds(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--json"], llm=fake_llm("boom"))
    assert rc == 0 and json.loads(out)["designed_by"] == "fallback"


def test_never_touches_existing_world(plugin, hermes_home, saves):
    old = saves / "Taken"
    old.mkdir()
    (old / "level.dat").write_bytes(b"keep")
    rc, out, err = _run(plugin, ["--saves-dir", str(saves), "--name", "Taken", "--yes"])
    assert rc == 1 and "already exists" in err
    assert (old / "level.dat").read_bytes() == b"keep" and not (old / "datapacks").exists()


def test_name_is_made_unique(plugin, hermes_home, saves):
    (saves / "Snow Library").mkdir()
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--json"], llm=fake_llm(GOOD_PLAN))
    assert json.loads(out)["world"] == "Snow Library 2"


def test_interactive_wish_change_then_build(plugin, hermes_home, saves):
    calls = []
    rc, out, err = _run(plugin, ["--saves-dir", str(saves)], ask=["snowy library", "make it sunset", ""],
                        interactive=True, llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0, err
    assert len(calls) == 2 and "make it sunset" in calls[1]["input"][0]["text"]
    assert "Stacks" in out and (saves / "Snow Library" / "level.dat").is_file()


def test_interactive_quit_writes_nothing(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["", "q"], interactive=True, llm=fake_llm(GOOD_PLAN))
    assert rc == 0 and "Nothing written" in out and list(saves.iterdir()) == []


def test_ctrl_c_writes_nothing(plugin, hermes_home, saves):
    def boom(_q):
        raise KeyboardInterrupt
    args = _parse(plugin, ["--saves-dir", str(saves)])
    out = io.StringIO()
    rc = plugin.create.run(args, out=out, err=io.StringIO(), ask=boom, interactive=True)
    assert rc == 130 and "nothing written" in out.getvalue() and list(saves.iterdir()) == []


def test_bad_model_plan_is_clamped(plugin):
    design = plugin.design
    profiles = [{"name": n} for n in ("a-1", "a-2", "b")]
    plan = design.validate({"name": "§cX\n" * 30, "style": "nope", "ground": "lava", "time": "x",
                            "rooms": [{"name": "Way Too Long Room Name Here", "theme": "gold",
                                       "profiles": ["A-1", "a-1", "zzz"]}, "junk", {"profiles": []}]}, profiles)
    assert plan["style"] == "falls" and plan["ground"] == "grass" and plan["time"] == "sunset"
    assert "§" not in plan["name"] and "\n" not in plan["name"]
    assert plan["rooms"][0]["profiles"] == ["a-1"] and len(plan["rooms"][0]["name"]) <= 15
    assert plan["rooms"][-1] == {"name": "Commons", "theme": plan["rooms"][-1]["theme"], "about": "everyone else",
                                 "profiles": ["a-2", "b"]}
    assert all(r["theme"] in design.THEMES for r in plan["rooms"])
    with pytest.raises(ValueError):
        design.validate("nope", profiles)


def test_rooms_drive_bays(plugin):
    from importlib import import_module
    lay = import_module(plugin.__name__ + ".worldgen.layout")
    L = lay.plan_layout(5, ["x1", "x2", "y1", "y2", "z"], rooms=[
        {"name": "Edit Bay", "theme": 3, "desks": [0, 1, 2]}, {"name": "Den", "theme": 7, "desks": [3, 4]}])
    assert [(b["label"], b["theme"], b["desks"]) for b in L["bays"]] == [("Edit Bay", 3, [0, 1, 2]),
                                                                         ("Den", 7, [3, 4])]


def test_folder_name_is_safe(plugin):
    from importlib import import_module
    nw = import_module(plugin.__name__ + ".worldgen.newworld")
    assert nw.folder_name("../../etc") == "etc"
    assert nw.folder_name("a/b\\c:d") == "abcd"
    assert nw.folder_name("   ") == "Hermes HQ"
