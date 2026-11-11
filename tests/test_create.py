"""`hermes omo create`: a new world with Hermes HQ in it (fake saves dir, fake Hermes root)."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import time
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


@pytest.fixture(autouse=True)
def no_launcher(plugin, monkeypatch):
    """Never start a real game app from a test."""
    monkeypatch.setattr(plugin.firsttime, "launcher_command", lambda vanilla, **k: None)
    monkeypatch.setattr(plugin.firsttime, "open_game", lambda vanilla: False)


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
    assert "Your world is ready" in out and "W A S D" in out


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
    rc, out, err = _run(plugin, ["--saves-dir", str(saves)], ask=["", "snowy library", "make it sunset", "", "n"],
                        interactive=True, llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0, err
    assert len(calls) == 2 and "make it sunset" in calls[1]["input"][0]["text"]
    assert "Stacks" in out and (saves / "Snow Library" / "level.dat").is_file()


def test_design_call_has_long_timeout(plugin, hermes_home, saves):
    # The host's 30 s default timed out on a 96-profile setup and fell back to name rooms.
    calls = []
    rc, _, err = _run(plugin, ["--saves-dir", str(saves)], ask=["", "snowy library", "make it sunset", "", "n"],
                      interactive=True, llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0, err
    assert len(calls) == 2                                      # first design and the 'change' redesign
    assert all((c.get("timeout") or 0) >= 120 for c in calls), [c.get("timeout") for c in calls]


def test_interactive_quit_writes_nothing(plugin, hermes_home, saves):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["", "", "q"], interactive=True,
                     llm=fake_llm(GOOD_PLAN))
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
                                 "why": "", "profiles": ["a-2", "b"]}
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


def test_tool_creates_world(plugin, hermes_home, saves, monkeypatch):
    monkeypatch.setattr(plugin.onboarding, "pick_target", lambda s, t="auto", **k: (saves, t == "minecraft"))
    res = json.loads(plugin.create.tool_create({"wish": "snowy library", "game": "minecraft"}, llm=fake_llm(GOOD_PLAN)))
    assert res["created"] and res["world"] == "Snow Library" and res["game"] == "Minecraft 26.2"
    assert [r["name"] for r in res["rooms"]][:2] == ["Stacks", "Reading Nook"]
    blob = b"".join(x.read_bytes() for x in (saves / "Snow Library" / "datapacks").rglob("*") if x.is_file())
    assert b"omolife" not in blob                                # plain Minecraft: vanilla blocks only


def test_tool_bad_args_never_raise(plugin, hermes_home, saves, monkeypatch):
    monkeypatch.setattr(plugin.onboarding, "pick_target", lambda s, t="auto", **k: (saves, False))
    for bad in (None, "x", {"game": "lava", "wish": 5}, {"name": "../../x"}):
        res = json.loads(plugin.create.tool_create(bad, llm=None))
        assert "created" in res


def test_pick_target(plugin, tmp_path):
    ob = plugin.onboarding
    home = tmp_path / "h"
    assert ob.pick_target(None, home=home, platform="linux", env={})[1] is False       # nothing: wait for Omo
    (home / ".minecraft").mkdir(parents=True)
    path, van = ob.pick_target(None, home=home, platform="linux", env={})
    assert van and path == home / ".minecraft" / "saves"
    (home / ".local/share/omo").mkdir(parents=True)
    path, van = ob.pick_target(None, home=home, platform="linux", env={})
    assert not van and path == home / ".local/share/omo/runtime/game/saves"
    assert ob.pick_target(None, "minecraft", home=home, platform="linux", env={})[1] is True


def test_open_last_and_first_timer_text(plugin, hermes_home, saves, monkeypatch):
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--yes"], llm=fake_llm(GOOD_PLAN))
    assert rc == 0 and "Double-click \"Snow Library\"" in out and "W A S D" in out
    buf = io.StringIO()
    assert plugin.create.open_last(out=buf, interactive=False) == 0
    text = buf.getvalue()
    assert "Snow Library" in text and ("Singleplayer" in text or "Java Edition" in text)


def test_open_game_asked_and_started(plugin, hermes_home, saves, monkeypatch):
    started = []
    monkeypatch.setattr(plugin.firsttime, "launcher_command", lambda vanilla, **k: ["true"])
    monkeypatch.setattr(plugin.firsttime, "open_game", lambda vanilla: started.append(vanilla) or True)
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["", "", "", "n", ""], interactive=True,
                      llm=fake_llm(GOOD_PLAN))
    assert rc == 0 and started == [False] and "Opening Omo Space" in out


def test_no_game_installed_explains_what_to_get(plugin, hermes_home, tmp_path, monkeypatch):
    omo_saves = tmp_path / "nothing" / "saves"
    monkeypatch.setattr(plugin.onboarding, "pick_target", lambda s, t="auto", **k: (omo_saves, False))
    monkeypatch.setattr(plugin.onboarding, "omo_data_dir", lambda *a, **k: tmp_path / "nothing-here")
    rc, out, _ = _run(plugin, ["--yes"], llm=fake_llm(GOOD_PLAN))
    assert rc == 0 and "Minecraft Java Edition account" in out and "Omo Space" in out
    assert (omo_saves / "Snow Library" / "level.dat").is_file()


def test_vanilla_without_minecraft_refuses(plugin, hermes_home, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.onboarding, "pick_target",
                        lambda s, t="auto", **k: (tmp_path / "no-mc" / ".minecraft" / "saves", True))
    rc, _, err = _run(plugin, ["--for", "minecraft", "--yes"], llm=fake_llm(GOOD_PLAN))
    assert rc == 1 and "isn't installed" in err and not (tmp_path / "no-mc").exists()


# --------------------------------------------------------------------------- audit, consent, adapt, light up
def _profile(root, name, *, label="", skills=(), platforms=(), cron=0, sessions=None):
    """A profile with real skill dirs, platforms, cron jobs and a state.db sessions table."""
    import sqlite3
    d = root if name == "default" else root / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    plat = "".join(f"  {x}:\n    enabled: true\n" for x in platforms)
    (d / "config.yaml").write_text("model:\n  default: m\n" + (f"platforms:\n{plat}" if plat else ""))
    if label:
        (d / "profile.yaml").write_text(f"description: '{label}'\n")
    for sk in skills:
        (d / "skills" / "cat" / sk).mkdir(parents=True, exist_ok=True)
        (d / "skills" / "cat" / sk / "SKILL.md").write_text(f"secret body {CANARY}")
    if cron:
        (d / "cron").mkdir(exist_ok=True)
        (d / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"id": i} for i in range(cron)]}))
    if sessions is not None:
        con = sqlite3.connect(d / "state.db")
        con.execute("create table sessions (id text, source text, started_at real, title text, system_prompt text)")
        for src, ago in sessions:
            con.execute("insert into sessions values (?,?,?,?,?)",
                        ("s", src, time.time() - ago * 86400, f"title {CANARY}", f"prompt {CANARY}"))
        con.commit()
        con.close()
    return d


@pytest.fixture
def rich_root(tmp_path, monkeypatch):
    root = tmp_path / "rich"
    _profile(root, "default", label="The main desk", platforms=("discord",), cron=3, sessions=[("cli", 1)] * 5)
    for n in ("ads-a", "ads-b", "ads-c"):
        _profile(root, n, label="Paid ads", skills=("meta-ads", "common"), sessions=[("discord", 2)])
    for n in ("dev-a", "dev-b", "dev-c"):
        _profile(root, n, label="Builds the app", skills=("github-pr", "common"), sessions=[("cli", 3)])
    _profile(root, "old-one", label="Retired helper", skills=("common",), sessions=[("cli", 90)])
    _profile(root, "never", skills=("common",))
    monkeypatch.setenv("HERMES_ROOT", str(root))
    return root


def test_audit_reads_usage_without_contents(plugin, rich_root):
    from importlib import import_module
    inv = import_module(plugin.__name__ + ".worldgen.inventory")
    data = inv.collect(rich_root)
    a = plugin.audit.run(data["profiles"], rich_root)
    rows = plugin.audit.by_name(a)
    assert a["shape"] == "team" and a["count"] == 9
    assert rows["default"]["chat"] == ["discord"] and rows["default"]["cron_jobs"] == 3
    assert rows["default"]["sessions"] == 5 and rows["default"]["used_on"] == ["terminal"]
    assert rows["ads-a"]["skills"] == ["meta-ads"]           # "common" is in every profile, so it says nothing
    assert sorted(a["quiet"]) == ["never", "old-one"]
    text = plugin.audit.model_text(a)
    assert CANARY not in text and CANARY not in json.dumps(plugin.audit.as_json(a))
    assert "quiet 90 days" in text and "never used" in text
    assert any("quiet" in line for line in plugin.audit.summary_lines(a))


def test_audit_state_db_is_opened_read_only(plugin, rich_root):
    db = rich_root / "profiles" / "ads-a" / "state.db"
    before = db.read_bytes()
    u = plugin.audit.usage(db.parent)
    assert u["sessions"] == {"discord": 1} and db.read_bytes() == before
    assert "title" not in plugin.audit.USAGE_SQL and "prompt" not in plugin.audit.USAGE_SQL


def test_designer_gets_the_audit_and_nothing_else(plugin, rich_root, saves):
    calls = []
    rc, _, err = _run(plugin, ["--saves-dir", str(saves), "--yes", "a", "forest"], llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0, err
    text = calls[0]["input"][0]["text"]
    assert "Setup: 9 profiles" in text and "skills: meta-ads" in text and "talks on discord" in text
    assert CANARY not in json.dumps(calls)


def test_consent_no_means_the_model_is_never_called(plugin, rich_root, saves):
    calls = []
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["n", "", "", "n"], interactive=True,
                      llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0 and calls == [] and "designing without the model" in out
    assert "Your setup" in out and len(list(saves.iterdir())) == 1


def test_consent_quit_writes_nothing(plugin, rich_root, saves):
    calls = []
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves)], ask=["q"], interactive=True, llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0 and calls == [] and list(saves.iterdir()) == []


def test_audit_flag_prints_and_writes_nothing(plugin, rich_root, saves):
    calls = []
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--audit"], llm=fake_llm(GOOD_PLAN, calls))
    assert rc == 0 and calls == [] and list(saves.iterdir()) == []
    assert "Never sent" in out and "- ads-a: Paid ads" in out
    rc, out, _ = _run(plugin, ["--saves-dir", str(saves), "--audit", "--json"], llm=None)
    assert json.loads(out)["shape"] == "team"


def test_fallback_adapts_to_the_setup(plugin, rich_root):
    from importlib import import_module
    inv = import_module(plugin.__name__ + ".worldgen.inventory")
    data = inv.collect(rich_root)
    a = plugin.audit.run(data["profiles"], rich_root)
    plan = plugin.design.validate(plugin.design.fallback_plan(data["profiles"], "a treehouse in the woods", audit=a),
                                  data["profiles"])
    rooms = {r["name"]: r for r in plan["rooms"]}
    assert sorted(rooms["Quiet Loft"]["profiles"]) == ["never", "old-one"]
    skill_rooms = [r for r in plan["rooms"] if r["why"].startswith("3 share")]
    assert sorted(sorted(r["profiles"]) for r in skill_rooms) == [["ads-a", "ads-b", "ads-c"], ["dev-a", "dev-b", "dev-c"]]
    assert plan["style"] == "grove" and plan["ground"] == "forest"


def test_forest_ground_makes_a_world(plugin, saves):
    from importlib import import_module
    nw = import_module(plugin.__name__ + ".worldgen.newworld")
    w = nw.create_world(saves, "Woods", ground="forest")
    gen = _read_nbt(w / "data/minecraft/world_gen_settings.dat")["data"]
    assert gen["dimensions"]["minecraft:overworld"]["generator"]["settings"]["layers"][-1]["block"] == "minecraft:podzol"


def test_solo_setup_is_one_room(plugin, tmp_path, monkeypatch):
    root = tmp_path / "solo"
    _profile(root, "default", label="Just me", sessions=[("cli", 1)])
    monkeypatch.setenv("HERMES_ROOT", str(root))
    from importlib import import_module
    inv = import_module(plugin.__name__ + ".worldgen.inventory")
    data = inv.collect(root)
    a = plugin.audit.run(data["profiles"], root)
    assert a["shape"] == "solo" and a["quiet"] == []
    plan = plugin.design.validate(plugin.design.fallback_plan(data["profiles"], "", audit=a), data["profiles"])
    assert [r["profiles"] for r in plan["rooms"]] == [["default"]]


def test_plan_lists_every_agent_and_why(plugin, saves, rich_root):
    plan = dict(GOOD_PLAN, rooms=[{"name": "Stacks", "theme": "library", "about": "a", "why": "all share ads",
                                   "profiles": [f"agent-number-{i}" for i in range(9)]}])
    lines = []
    plugin.create.show_plan(plugin.design.validate(plan, [{"name": f"agent-number-{i}"} for i in range(9)]),
                            lines.append, plugin.create.Paint(False), width=80)
    text = "\n".join(lines)
    assert "why: all share ads" in text and "…" not in text.replace("… and", "")
    assert all(f"agent-number-{i}" in text for i in range(9))


def test_light_up_enables_dark_desks_after_yes(plugin, rich_root, saves, monkeypatch):
    ran = []
    monkeypatch.setattr(plugin.onboarding.shutil, "which", lambda _: "/usr/bin/hermes")
    args = _parse(plugin, ["--saves-dir", str(saves)])
    answers = iter(["", "", "", "y"])
    out = io.StringIO()
    rc = plugin.create.run(args, board_port=8790, llm=fake_llm(GOOD_PLAN), out=out, err=io.StringIO(),
                           ask=lambda _q: next(answers), interactive=True,
                           runner=lambda c: ran.append(c) or type("R", (), {"returncode": 0})())
    assert rc == 0 and len(ran) == 9 and all("omo-space" in " ".join(c) for c in ran)
    assert "9 desks lit" in out.getvalue()


def test_light_up_no_runs_nothing(plugin, rich_root, saves):
    ran = []
    args = _parse(plugin, ["--saves-dir", str(saves)])
    answers = iter(["", "", "", "n"])
    rc = plugin.create.run(args, board_port=8790, llm=fake_llm(GOOD_PLAN), out=io.StringIO(), err=io.StringIO(),
                           ask=lambda _q: next(answers), interactive=True, runner=lambda c: ran.append(c))
    assert rc == 0 and ran == []


def test_unattended_never_prompts_or_enables(plugin, rich_root, saves):
    ran = []
    args = _parse(plugin, ["--saves-dir", str(saves), "--yes"])
    def no_ask(_q):
        raise AssertionError("prompted without a terminal")
    rc = plugin.create.run(args, board_port=8790, llm=fake_llm(GOOD_PLAN), out=io.StringIO(), err=io.StringIO(),
                           ask=no_ask, interactive=False, runner=lambda c: ran.append(c))
    assert rc == 0 and ran == []


def test_audit_tool_returns_summary_and_sends_nothing(plugin, rich_root):
    res = json.loads(plugin.create.tool_audit(rich_root))
    assert res["shape"] == "team" and res["summary"] and "never_sent" in res
    assert CANARY not in json.dumps(res)


def test_audit_connects_read_only(plugin, rich_root, monkeypatch):
    import sqlite3
    seen = []
    real = sqlite3.connect
    def spy(database, **k):
        seen.append((database, k))
        return real(database, **k)
    monkeypatch.setattr(plugin.audit.sqlite3, "connect", spy)
    plugin.audit.usage(rich_root / "profiles" / "ads-a")
    assert seen and all(u.endswith("?mode=ro") and k.get("uri") is True for u, k in seen)


@pytest.mark.parametrize("wish", ["a forest office", "a treehouse village", "cosy woods"])
def test_forest_wishes_pick_the_grove(plugin, wish):
    plan = plugin.design.fallback_plan([{"name": "a"}], wish, seed=1)
    assert (plan["style"], plan["ground"]) == ("grove", "forest")
