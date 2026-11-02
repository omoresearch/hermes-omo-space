"""`hermes omo setup` and `hermes omo world --install` (fake saves dir, fake Hermes root)."""

from __future__ import annotations

import argparse
import io
import json
import os
import plistlib
import types
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- helpers

def _runtime(plugin, tmp_path, bridge_url="", profile="default"):
    feed = plugin.feed.Feed(tmp_path / "pd" / "feed", profile=profile, plugin_version="0.1.0")
    return plugin.cli.Runtime(feed=feed, bridge_url_setting=bridge_url, profile=profile)


def _parse(plugin, argv):
    parser = argparse.ArgumentParser(prog="hermes")
    subs = parser.add_subparsers(dest="command")
    plugin.cli.setup_parser(subs.add_parser("omo"))
    return parser.parse_args(argv)


def _world(saves: Path, name: str, packs=()) -> Path:
    w = saves / name
    (w / "datapacks").mkdir(parents=True)
    (w / "level.dat").write_bytes(b"\x1f\x8b fake")
    for p in packs:
        (w / "datapacks" / p).mkdir()
        (w / "datapacks" / p / "pack.mcmeta").write_text('{"pack": {}}')
    return w


def _profile(home: Path, enabled=(), disabled=(), installed=False):
    home.mkdir(parents=True, exist_ok=True)
    lines = ["model:", "  default: test-model", "plugins:"]
    lines.append("  enabled: [" + ", ".join(enabled) + "]")
    if disabled:
        lines.append("  disabled: [" + ", ".join(disabled) + "]")
    (home / "config.yaml").write_text("\n".join(lines) + "\n")
    if installed:
        (home / "plugins" / "omo-space").mkdir(parents=True)
        (home / "plugins" / "omo-space" / "plugin.yaml").write_text("name: omo-space\n")


def _proc_tcp(d: Path, port: int, uid: int, addr="0100007F") -> str:
    d.mkdir(exist_ok=True)
    (d / "tcp").write_text("  sl local_address rem_address st tx rx tr tm retr uid\n"
                           f"   0: {addr}:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
                           f"  {uid}        0 1 1\n")
    return str(d)


@pytest.fixture
def saves(tmp_path):
    s = tmp_path / "saves"
    s.mkdir()
    return s


@pytest.fixture
def world_cli(plugin):
    return plugin.cli._load_world_cli()


# --------------------------------------------------------------------------- Omo locations

def test_omo_data_dir_per_platform(plugin, tmp_path):
    ob = plugin.onboarding
    home = tmp_path / "h"
    assert ob.omo_data_dir({}, "linux", home) == home / ".local" / "share" / "omo"
    assert ob.omo_data_dir({}, "darwin", home) == home / "Library" / "Application Support" / "OmoResearch"
    assert ob.omo_data_dir({"LOCALAPPDATA": str(tmp_path / "LAD")}, "win32", home) == tmp_path / "LAD" / "OmoResearch"
    assert ob.omo_data_dir({}, "win32", home) == home / "AppData" / "Local" / "OmoResearch"
    assert ob.omo_data_dir({"OMO_DATA": str(tmp_path / "x")}, "darwin", home) == tmp_path / "x"
    assert ob.saves_dir({}, "linux", home) == home / ".local" / "share" / "omo" / "runtime" / "game" / "saves"


def test_list_worlds_needs_level_dat(plugin, saves):
    _world(saves, "B World", packs=["hermes_hq", "other"])
    _world(saves, "a world")
    (saves / "not-a-world").mkdir()
    worlds = plugin.onboarding.list_worlds(saves)
    assert [w["name"] for w in worlds] == ["a world", "B World"]
    assert worlds[1]["installed"] and not worlds[0]["installed"]
    assert plugin.onboarding.list_worlds(saves / "missing") == []


def test_pick_world(plugin, saves):
    ob = plugin.onboarding
    with pytest.raises(ob.InstallError, match="no Minecraft worlds"):
        ob.pick_world(saves, None)
    only = _world(saves, "Omo World")
    assert ob.pick_world(saves, "") == only
    _world(saves, "Omo Workshop")
    with pytest.raises(ob.InstallError, match="several worlds"):
        ob.pick_world(saves, "")
    assert ob.pick_world(saves, "Omo Workshop") == saves / "Omo Workshop"
    for bad in ("../Omo World", "a/b", "a\\b", "..", "x\n"):
        with pytest.raises(ob.InstallError):
            ob.pick_world(saves, bad)
    with pytest.raises(ob.InstallError, match="no world named"):
        ob.pick_world(saves, "Nope")


# --------------------------------------------------------------------------- world --install

def test_world_install_writes_only_its_datapack(plugin, saves, world_cli, hermes_home, tmp_path, monkeypatch, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    w = _world(saves, "Omo World", packs=["someone_else"])
    other = (w / "datapacks" / "someone_else" / "pack.mcmeta").read_text()
    monkeypatch.chdir(tmp_path)
    args = _parse(plugin, ["omo", "world", "--install", "--saves-dir", str(saves)])
    rc = plugin.cli.run(args, _runtime(plugin, tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    pack = w / "datapacks" / "hermes_hq"
    assert (pack / "pack.mcmeta").is_file() and (pack / "data").is_dir()
    assert (w / "datapacks" / "someone_else" / "pack.mcmeta").read_text() == other
    assert sorted(p.name for p in (w / "datapacks").iterdir()) == ["hermes_hq", "someone_else"]
    assert "/reload" in out and "/function hermes_hq:build" in out and str(pack) in out
    assert not (tmp_path / "hermes-hq-datapack").exists()   # nothing in the cwd


def test_world_install_refuses_overwrite_without_force(plugin, saves, world_cli, hermes_home, tmp_path, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    w = _world(saves, "W", packs=["keep"])
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves)]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 0
    marker = w / "datapacks" / "hermes_hq" / "pack.mcmeta"
    marker.write_text("old")
    capsys.readouterr()
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 1
    assert "already exists" in capsys.readouterr().err and marker.read_text() == "old"
    assert plugin.cli.run(_parse(plugin, argv + ["--force"]), _runtime(plugin, tmp_path)) == 0
    assert marker.read_text() != "old" and (w / "datapacks" / "keep" / "pack.mcmeta").is_file()


def test_world_install_force_never_replaces_a_non_datapack(plugin, saves, world_cli, hermes_home, tmp_path, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    w = _world(saves, "W")
    (w / "datapacks" / "hermes_hq").mkdir()
    (w / "datapacks" / "hermes_hq" / "notes.txt").write_text("mine")
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves), "--force"]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 1
    assert (w / "datapacks" / "hermes_hq" / "notes.txt").read_text() == "mine"


def test_world_install_refuses_symlink_and_out(plugin, saves, world_cli, hermes_home, tmp_path, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    w = _world(saves, "W")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (w / "datapacks" / "hermes_hq").symlink_to(elsewhere, target_is_directory=True)
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves), "--force"]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 1
    assert list(elsewhere.iterdir()) == []
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves), "--out", str(tmp_path / "o")]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 2
    assert "--out" in capsys.readouterr().err


def test_world_install_dry_run_writes_nothing(plugin, saves, world_cli, hermes_home, tmp_path, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    w = _world(saves, "W")
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves), "--dry-run"]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 0
    assert list((w / "datapacks").iterdir()) == []
    assert "Would install into" in capsys.readouterr().out


def test_world_install_json(plugin, saves, world_cli, hermes_home, tmp_path, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    _world(saves, "W")
    argv = ["omo", "world", "--install", "W", "--saves-dir", str(saves), "--json"]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["world"] == "W" and data["installed"].endswith("hermes_hq") and data["desks"] == 1


def test_list_worlds_cli(plugin, saves, world_cli, tmp_path, capsys):
    _world(saves, "Omo World", packs=["hermes_hq"])
    _world(saves, "Omo Workshop")
    argv = ["omo", "world", "--list-worlds", "--saves-dir", str(saves)]
    assert plugin.cli.run(_parse(plugin, argv), _runtime(plugin, tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Omo World  [hermes_hq installed]" in out and "Omo Workshop" in out


def test_world_install_default_saves_dir_is_omo(plugin, world_cli, hermes_home, tmp_path, monkeypatch, capsys):
    (hermes_home / "config.yaml").write_text("model:\n  default: m\n")
    monkeypatch.setenv("OMO_DATA", str(tmp_path / "omo"))
    w = _world(tmp_path / "omo" / "runtime" / "game" / "saves", "Omo World")
    assert plugin.cli.run(_parse(plugin, ["omo", "world", "--install"]), _runtime(plugin, tmp_path)) == 0
    assert (w / "datapacks" / "hermes_hq" / "pack.mcmeta").is_file()


# --------------------------------------------------------------------------- profiles

def test_plugin_state_reads_enabled_and_disabled(plugin, tmp_path):
    ob = plugin.onboarding
    a, b, c, d = (tmp_path / n for n in "abcd")
    _profile(a, enabled=["omo-space"], installed=True)
    _profile(b, enabled=["omo-space"], disabled=["omo-space"], installed=True)
    _profile(c, installed=True)
    _profile(d)
    assert ob.plugin_state(a) == {"installed": True, "enabled": True}
    assert ob.plugin_state(b) == {"installed": True, "enabled": False}
    assert ob.plugin_state(c) == {"installed": True, "enabled": False}
    assert ob.plugin_state(d) == {"installed": False, "enabled": False}


def test_plugin_installed_under_another_dir_name(plugin, tmp_path):
    home = tmp_path / "p"
    _profile(home)
    (home / "plugins" / "hermes-omo-space").mkdir(parents=True)
    (home / "plugins" / "hermes-omo-space" / "plugin.yaml").write_text('name: "omo-space"\nversion: 1\n')
    assert plugin.onboarding.plugin_state(home)["installed"] is True


def test_profiles_report_and_enable_commands(plugin, hermes_home):
    ob = plugin.onboarding
    _profile(hermes_home, enabled=["omo-space"], installed=True)
    _profile(hermes_home / "profiles" / "work", installed=True)
    _profile(hermes_home / "profiles" / "fresh")
    (hermes_home / "profiles" / "junk").mkdir()          # not a profile (no config.yaml/state.db)
    report = ob.profiles_report(hermes_home)
    assert [p["name"] for p in report] == ["default", "fresh", "work"]
    cmds = ob.enable_commands(report)
    assert ["hermes", "-p", "work", "plugins", "enable", "omo-space"] in cmds
    assert ["hermes", "-p", "fresh", "plugins", "install", ob.REPO_URL, "--enable"] in cmds
    assert not any("default" in c for c in cmds)


def test_profile_names_cannot_inject_shell(plugin):
    assert plugin.onboarding._shell(["hermes", "-p", "a b;rm -rf ~", "x"]) == 'hermes -p "a b;rm -rf ~" x'


# --------------------------------------------------------------------------- bridge detection

def test_detect_bridge_skips_foreign_port_without_connecting(plugin, fake_bridge, tmp_path, monkeypatch):
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.setattr(plugin.tools, "PROC_NET", _proc_tcp(tmp_path / "pn", port, os.getuid() + 1))
    r = plugin.onboarding.detect_bridge(env={"OMO_PORT": str(port)}, platform="linux", home=tmp_path, scan=[])
    assert r["port"] is None and r["foreign"] == [{"port": port, "source": "OMO_PORT"}]
    assert fake_bridge.hits == []


def test_detect_bridge_scan_finds_own_port(plugin, fake_bridge, tmp_path, monkeypatch):
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.setattr(plugin.tools, "PROC_NET", _proc_tcp(tmp_path / "pn", port, os.getuid()))
    r = plugin.onboarding.detect_bridge(env={}, platform="linux", home=tmp_path, scan=[1, port])
    assert r["port"] == port and r["source"] == "scan" and r["owner"] == "mine" and r["version"] == "9.9.9"
    assert r["checked"] == [port]    # port 1 has no LISTEN socket of ours: never connected


def test_detect_bridge_systemd_dropin(plugin, fake_bridge, tmp_path):
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    unit = tmp_path / ".config" / "systemd" / "user"
    (unit / "omo-bridge.service.d").mkdir(parents=True)
    (unit / "omo-bridge.service").write_text('[Service]\nEnvironment="OMO_DATA=/x"\n')
    (unit / "omo-bridge.service.d" / "local.conf").write_text(f"[Service]\nEnvironment=OMO_PORT={port}\n")
    r = plugin.onboarding.detect_bridge(env={}, platform="linux", home=tmp_path, scan=[])
    assert r["port"] == port and r["source"].endswith("local.conf")


def test_detect_bridge_launchd_plist(plugin, fake_bridge, tmp_path):
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    la = tmp_path / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    with open(la / "space.omo.research.bridge.plist", "wb") as fh:
        plistlib.dump({"Label": "space.omo.research.bridge", "EnvironmentVariables": {"OMO_PORT": str(port)}}, fh)
    r = plugin.onboarding.detect_bridge(env={}, platform="darwin", home=tmp_path, scan=[])
    assert r["port"] == port and r["owner"] is None


def test_detect_bridge_nothing(plugin, tmp_path):
    r = plugin.onboarding.detect_bridge(env={}, platform="linux", home=tmp_path)
    assert r["port"] is None and r["checked"] == []


# --------------------------------------------------------------------------- setup

def _no_prompt(_q):
    raise AssertionError("setup must not prompt here")


def _no_run(*_a, **_k):
    raise AssertionError("setup must not run commands here")


def _setup_env(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge=None):
    _profile(hermes_home, enabled=["omo-space"], installed=True)
    _profile(hermes_home / "profiles" / "work", installed=True)
    monkeypatch.setenv("OMO_DATA", str(tmp_path / "omo"))
    saves = tmp_path / "omo" / "runtime" / "game" / "saves"
    saves.mkdir(parents=True)
    _world(saves, "Omo World")
    if fake_bridge is not None:
        monkeypatch.setenv("OMO_PORT", fake_bridge.url.rsplit(":", 1)[1])
    return saves


def test_setup_non_interactive_prints_checklist_and_never_prompts(plugin, hermes_home, tmp_path, monkeypatch,
                                                                 fake_bridge):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge)
    out = io.StringIO()
    args = types.SimpleNamespace(enable_all=True, non_interactive=False, json=False)
    rc = plugin.onboarding.setup(args, _runtime(plugin, tmp_path, bridge_url="http://127.0.0.1:1"), out=out,
                                 ask=_no_prompt, interactive=False, runner=_no_run)
    text = out.getvalue()
    assert rc == 0
    assert "Omo Space setup" in text and "1 of 2 Hermes profiles" in text
    assert "hermes -p work plugins enable omo-space" in text
    assert f"plugins.entries.omo-space.settings.bridge_url {fake_bridge.url}" in text
    assert 'hermes omo world --install "Omo World"' in text
    assert "needs an interactive terminal" in text
    assert "right-click each desk once" in text


def test_setup_without_tty_is_the_default_under_pytest(plugin, hermes_home, tmp_path, monkeypatch):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", _no_prompt)
    monkeypatch.setattr(plugin.onboarding.subprocess, "run", _no_run)
    rc = plugin.cli.run(_parse(plugin, ["omo", "setup", "--enable-all"]), _runtime(plugin, tmp_path))
    assert rc == 0


def test_setup_non_interactive_flag_wins_over_tty(plugin, hermes_home, tmp_path, monkeypatch):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    args = types.SimpleNamespace(enable_all=True, non_interactive=True, json=False)
    assert plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=io.StringIO(), ask=_no_prompt,
                                   interactive=True, runner=_no_run) == 0


def test_setup_enable_all_runs_only_after_yes(plugin, hermes_home, tmp_path, monkeypatch, world_cli):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    monkeypatch.setattr(plugin.onboarding.shutil, "which", lambda name: "/opt/bin/hermes")
    ran = []

    def runner(cmd, **kw):
        ran.append(cmd)
        return types.SimpleNamespace(returncode=0)

    args = types.SimpleNamespace(enable_all=True, non_interactive=False, json=False)
    answers = iter(["n", "n"])     # enable? no. world? no.
    assert plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=io.StringIO(),
                                   ask=lambda q: next(answers), interactive=True, runner=runner,
                                   world_cli=world_cli) == 0
    assert ran == []
    answers = iter(["y", "n"])
    out = io.StringIO()
    assert plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=out, ask=lambda q: next(answers),
                                   interactive=True, runner=runner, world_cli=world_cli) == 0
    assert ran == [["/opt/bin/hermes", "-p", "work", "plugins", "enable", "omo-space"]]


def test_setup_interactive_installs_world_after_yes(plugin, hermes_home, tmp_path, monkeypatch, world_cli):
    saves = _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    args = types.SimpleNamespace(enable_all=False, non_interactive=False, json=False)
    out = io.StringIO()
    rc = plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=out, ask=lambda q: "y", interactive=True,
                                 runner=_no_run, world_cli=world_cli)
    assert rc == 0
    assert (saves / "Omo World" / "datapacks" / "hermes_hq" / "pack.mcmeta").is_file()
    assert "/reload" in out.getvalue()


def test_setup_eof_at_prompt_means_no(plugin, hermes_home, tmp_path, monkeypatch, world_cli):
    saves = _setup_env(plugin, hermes_home, tmp_path, monkeypatch)

    def eof(_q):
        raise EOFError

    args = types.SimpleNamespace(enable_all=True, non_interactive=False, json=False)
    assert plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=io.StringIO(), ask=eof, interactive=True,
                                   runner=_no_run, world_cli=world_cli) == 0
    assert list((saves / "Omo World" / "datapacks").iterdir()) == []


def test_setup_json(plugin, hermes_home, tmp_path, monkeypatch, capsys):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    assert plugin.cli.run(_parse(plugin, ["omo", "setup", "--json"]), _runtime(plugin, tmp_path)) == 0
    data = json.loads(capsys.readouterr().out)
    assert {p["name"] for p in data["profiles"]} == {"default", "work"}
    assert data["omo_data_exists"] is True and data["worlds"][0]["name"] == "Omo World"
    assert data["bridge"]["port"] is None


def test_setup_reports_missing_omo(plugin, hermes_home, tmp_path, monkeypatch):
    _profile(hermes_home)
    monkeypatch.setenv("OMO_DATA", str(tmp_path / "no-omo"))
    out = io.StringIO()
    args = types.SimpleNamespace(enable_all=False, non_interactive=False, json=False)
    assert plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=out, ask=_no_prompt,
                                   interactive=False, runner=_no_run) == 0
    text = out.getvalue()
    assert "install Omo Space first" in text and "Omo bridge not found" in text


def test_setup_keeps_a_working_configured_bridge(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge):
    """The profile points at a reachable bridge of ours on another port: no `config set` is suggested."""
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch)
    monkeypatch.setattr(plugin.onboarding, "detect_bridge", lambda: {
        "port": 1, "source": "OMO_PORT", "version": "x", "foreign": [], "checked": [1], "owner": None})
    r = plugin.onboarding.setup_report(_runtime(plugin, tmp_path, bridge_url=fake_bridge.url))
    assert r["current_bridge_ok"] is True and r["set_bridge_cmds"] == []
    out = io.StringIO()
    plugin.onboarding.print_checklist(r, out)
    assert "reachable, yours" in out.getvalue() and "config set" not in out.getvalue()


def test_setup_suggests_bridge_url_when_configured_one_is_dead(plugin, hermes_home, tmp_path, monkeypatch,
                                                              fake_bridge):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge)
    r = plugin.onboarding.setup_report(_runtime(plugin, tmp_path, bridge_url="http://127.0.0.1:1"))
    assert r["current_bridge_ok"] is False
    assert r["set_bridge_cmds"] == [["hermes", "-p", "default", "config", "set",
                                     "plugins.entries.omo-space.settings.bridge_url", fake_bridge.url]]


def test_setup_names_omo_space_install_paths_when_missing(plugin, hermes_home, tmp_path, monkeypatch):
    _profile(hermes_home)
    monkeypatch.setenv("OMO_DATA", str(tmp_path / "no-omo"))
    out = io.StringIO()
    args = types.SimpleNamespace(enable_all=False, non_interactive=False, json=False)
    plugin.onboarding.setup(args, _runtime(plugin, tmp_path), out=out, ask=_no_prompt, interactive=False,
                            runner=_no_run)
    text = " ".join(out.getvalue().split())
    assert "install.sh --bridge-only" in text and "CurseForge" in text


def test_setup_flags_a_bridge_without_feed_support(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge)
    fake_bridge.routes["/board.json"] = {"version": "1.2.0", "workspaces": []}       # no "hermes" block
    r = plugin.onboarding.setup_report(_runtime(plugin, tmp_path, bridge_url=fake_bridge.url))
    assert r["bridge"]["feed_support"] is False
    out = io.StringIO()
    plugin.onboarding.print_checklist(r, out)
    text = " ".join(out.getvalue().split())
    assert "omoresearch/omo-space PR #40" in text and "next Omo Space release" in text


def test_setup_quiet_about_feed_support_on_a_feed_aware_bridge(plugin, hermes_home, tmp_path, monkeypatch,
                                                               fake_bridge):
    _setup_env(plugin, hermes_home, tmp_path, monkeypatch, fake_bridge)
    r = plugin.onboarding.setup_report(_runtime(plugin, tmp_path, bridge_url=fake_bridge.url))
    assert r["bridge"]["feed_support"] is True
    out = io.StringIO()
    plugin.onboarding.print_checklist(r, out)
    assert "PR #40" not in out.getvalue()
