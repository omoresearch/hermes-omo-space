"""`hermes omo status|feed|world`."""

from __future__ import annotations

import argparse
import io
import json
import types

import pytest


def _runtime(plugin, tmp_path, bridge_url="", enabled=True):
    feed = plugin.feed.Feed(tmp_path / "pd" / "feed", profile="default", plugin_version="0.1.0", enabled=enabled)
    return plugin.cli.Runtime(feed=feed, bridge_url_setting=bridge_url, profile="default")


def _parse(plugin, argv, world=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(plugin.cli, "_load_world_cli", lambda: world)
    parser = argparse.ArgumentParser(prog="hermes")
    subs = parser.add_subparsers(dest="command")
    omo = subs.add_parser("omo")
    plugin.cli.setup_parser(omo)
    return parser.parse_args(argv)


def _run(plugin, runtime, argv, monkeypatch, world=None, capsys=None):
    args = _parse(plugin, argv, world=world, monkeypatch=monkeypatch)
    rc = plugin.cli.run(args, runtime)
    return rc


def test_status_bridge_up(plugin, tmp_path, fake_bridge, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path, bridge_url=fake_bridge.url)
    rt.feed.write_marker()
    rt.feed.emit("tool_end", "s1", tool="terminal")
    rc = _run(plugin, rt, ["omo", "status"], monkeypatch)
    out = capsys.readouterr().out
    assert rc == 0
    assert "reachable" in out and fake_bridge.url in out and "9.9.9" in out
    assert str(rt.feed.root) in out and "omo-feed.json" in out
    assert "last event" in out and "tool_end" in out
    assert "Next step" in out


def test_status_bridge_down_next_step(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path, bridge_url="http://127.0.0.1:1")
    _run(plugin, rt, ["omo", "status"], monkeypatch)
    out = capsys.readouterr().out
    assert "not reachable" in out
    assert "Next step" in out and ("Omo" in out.split("Next step", 1)[1])
    assert "marker" in out and "missing" in out.lower()


def test_status_refuses_non_loopback(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path, bridge_url="http://evil.example:8780")
    _run(plugin, rt, ["omo", "status"], monkeypatch)
    out = capsys.readouterr().out
    assert "loopback" in out


def test_status_lists_profiles_with_feeds(plugin, tmp_path, hermes_home, monkeypatch, capsys):
    for p in (hermes_home / "plugin-data" / "omo-space" / "feed",
              hermes_home / "profiles" / "coder" / "plugin-data" / "plugin-omo-space-ab12" / "feed"):
        p.mkdir(parents=True)
        (p / "omo-feed.json").write_text(json.dumps({"v": 1, "plugin": "omo-space", "profile": "x"}))
    (hermes_home / "profiles" / "nofeed").mkdir(parents=True)
    rt = _runtime(plugin, tmp_path, bridge_url="http://127.0.0.1:1")
    _run(plugin, rt, ["omo", "status", "--json"], monkeypatch)
    data = json.loads(capsys.readouterr().out)
    assert sorted(data["profiles_with_feed"]) == ["coder", "default"]
    assert data["bridge"]["up"] is False and data["next_step"]


def test_status_feed_disabled(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path, enabled=False, bridge_url="http://127.0.0.1:1")
    _run(plugin, rt, ["omo", "status"], monkeypatch)
    out = capsys.readouterr().out
    assert "disabled" in out


def _seed_events(rt, n):
    for i in range(n):
        rt.feed.emit("tool_end", f"s{i}", tool=f"tool{i}", duration_ms=i, ok=True)


def test_feed_json_last_n(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _seed_events(rt, 5)
    _run(plugin, rt, ["omo", "feed", "--json", "-n", "2"], monkeypatch)
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(l)["tool"] for l in lines] == ["tool3", "tool4"]


def test_feed_reads_across_rotation(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _seed_events(rt, 2)
    (rt.feed.root / "events.jsonl").rename(rt.feed.root / "events.1.jsonl")
    rt.feed.emit("compaction", "s9")
    _run(plugin, rt, ["omo", "feed", "--json", "-n", "10"], monkeypatch)
    kinds = [json.loads(l)["kind"] for l in capsys.readouterr().out.strip().splitlines()]
    assert kinds == ["tool_end", "tool_end", "compaction"]


def test_feed_human_format_and_bad_lines(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _seed_events(rt, 1)
    with open(rt.feed.root / "events.jsonl", "a") as fh:
        fh.write("not json\n")
    rt.feed.notify("deploy ok", level="success", session_id="s1")
    _run(plugin, rt, ["omo", "feed"], monkeypatch)
    out = capsys.readouterr().out
    assert "tool_end" in out and "tool0" in out and "deploy ok" in out and "not json" not in out


def test_feed_empty(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _run(plugin, rt, ["omo", "feed"], monkeypatch)
    assert "no events" in capsys.readouterr().out.lower()


def test_follow_prints_new_events(plugin, tmp_path):
    rt = _runtime(plugin, tmp_path)
    _seed_events(rt, 1)
    out = io.StringIO()
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        if ticks["n"] == 1:
            rt.feed.emit("compaction", "late")
        return ticks["n"] > 3

    plugin.cli.follow(rt.feed.root, out, as_json=True, stop=stop, interval=0)
    kinds = [json.loads(l)["kind"] for l in out.getvalue().splitlines()]
    assert kinds == ["compaction"]


def test_world_missing_generator(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _run(plugin, rt, ["omo", "world"], monkeypatch, world=None)
    assert "world generator not installed" in capsys.readouterr().out


def test_world_dispatches_to_worldgen(plugin, tmp_path, monkeypatch):
    calls = []
    fake = types.SimpleNamespace(
        add_parser=lambda subparsers: subparsers.add_parser("world").add_argument("--out", default="x"),
        run=lambda args: calls.append(args.out) or 0,
    )
    rt = _runtime(plugin, tmp_path)
    rc = _run(plugin, rt, ["omo", "world", "--out", "hq"], monkeypatch, world=fake)
    assert calls == ["hq"] and rc == 0


def test_no_subcommand_prints_usage(plugin, tmp_path, monkeypatch, capsys):
    rt = _runtime(plugin, tmp_path)
    _run(plugin, rt, ["omo"], monkeypatch)
    assert "status" in capsys.readouterr().out


def test_status_foreign_bridge_next_step(plugin, tmp_path, fake_bridge, monkeypatch, capsys):
    import os
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    d = tmp_path / "proc-net"
    d.mkdir()
    (d / "tcp").write_text("  sl local_address rem_address st tx rx tr tm retr uid\n"
                           f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
                           f"  {os.getuid() + 1}        0 1 1\n")
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(d))
    monkeypatch.setattr(plugin.tools.sys, "platform", "linux")
    rt = _runtime(plugin, tmp_path, bridge_url=fake_bridge.url)
    assert _run(plugin, rt, ["omo", "status"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "reachable at" not in out and "another user" in out
    assert "Next step:" in out and "OMO_PORT" in out and "bridge_url" in out
    assert fake_bridge.hits == []
