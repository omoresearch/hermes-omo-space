"""Feed format v1 against the Omo bridge's real reader (omoresearch/omo-space PR #40).

The plugin writes a feed exactly where Hermes puts plugin data, then the bridge's own
``hermesfeed.FeedReader`` (vendored, unmodified, see tests/vendor/README.md) reads it back.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent / "vendor" / "hermesfeed_pr40.py"


@pytest.fixture(scope="module")
def reader_mod():
    spec = importlib.util.spec_from_file_location("hermesfeed_pr40", _VENDOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session(feed, sid, platform="cli"):
    h = feed.hooks()
    h["on_session_start"](session_id=sid, model="m1", platform=platform)
    h["pre_llm_call"](session_id=sid, user_message="SECRET-USER-TEXT", is_first_turn=True,
                      model="m1", platform=platform)
    h["pre_tool_call"](tool_name="terminal", args={"command": "echo SECRET-ARG"}, task_id=sid, session_id=sid)
    return h


@pytest.mark.parametrize("profile_dir", [None, "lf-01-ideas"])
def test_bridge_reader_discovers_and_reads_the_feed(plugin, tmp_path, reader_mod, monkeypatch, profile_dir):
    monkeypatch.setenv("OMO_WORKSPACE", "ws-7")
    monkeypatch.setenv("OMO_TAB", "tab-2")
    monkeypatch.setenv("OMO_HERMES_TITLE", "omo:ws-7:tab-2")
    root = tmp_path / "hermes-root"
    home = root if profile_dir is None else root / "profiles" / profile_dir
    profile = profile_dir or "default"
    # what ctx.state.data_dir looks like: <HERMES_HOME>/plugin-data/<namespace>
    feed = plugin.feed.Feed(home / "plugin-data" / "agent-plugin-omo-space-d135e42c" / "feed",
                            profile=profile, plugin_version="0.1.0")
    assert feed.write_marker()
    h = _session(feed, "sess-a")
    feed.notify("build finished", level="success", session_id="sess-a")

    reader = reader_mod.FeedReader(str(root), started=0)
    snap = reader.snapshot()
    assert [s["session_id"] for s in snap["sessions"]] == ["sess-a"]
    s = snap["sessions"][0]
    assert s["profile"] == profile and s["platform"] == "cli" and s["state"] == "tool"
    assert s["tool"] == "terminal" and s["tool_for"] is not None and s["turns"] == 1
    assert s["omo"] == {"workspace": "ws-7", "tab": "tab-2", "title": "omo:ws-7:tab-2"}
    assert snap["tabs"][("ws-7", "tab-2")]["state"] == "tool"       # lights that tab on the Board

    kinds = [e["kind"] for e in snap["events"]]
    assert kinds == ["session_start", "turn_start", "tool_start", "notify"]
    notes = reader.new_notifies()
    assert [(n["text"], n["level"], n["profile"]) for n in notes] == [("build finished", "success", profile)]
    assert reader.new_notifies() == []                              # forwarded once

    h["post_tool_call"](tool_name="terminal", result="SECRET-RESULT", task_id="sess-a", session_id="sess-a",
                        duration_ms=12)
    h["on_session_end"](session_id="sess-a", completed=True, interrupted=False, platform="cli")
    h["on_session_finalize"](session_id="sess-a", platform="cli")
    snap = reader.snapshot()
    assert snap["sessions"][0]["state"] == "ended"
    assert [e["kind"] for e in snap["events"]][-3:] == ["tool_end", "turn_end", "session_end"]
    for path in (home / "plugin-data").rglob("*"):
        if path.is_file():
            assert "SECRET" not in path.read_text()


def test_every_written_field_survives_the_reader(plugin, tmp_path, reader_mod):
    """Each kind the plugin writes is one the reader keeps, and each presence parses."""
    root = tmp_path / "root"
    feed = plugin.feed.Feed(root / "plugin-data" / "ns" / "feed", profile="default", plugin_version="0.1.0")
    feed.write_marker()
    for kind in sorted(plugin.feed.KINDS):
        feed.emit(kind, "s1", tool="t" if kind.startswith("tool") else None,
                  text="hi" if kind == "notify" else None, level="info" if kind == "notify" else None)
    lines = (feed.root / "events.jsonl").read_text().splitlines()
    parsed = reader_mod.parse_events([ln.encode() for ln in lines])
    assert {e["kind"] for e in parsed} == set(plugin.feed.KINDS)
    _session(feed, "s2")
    raw = json.loads((feed.root / "sessions" / "s2.json").read_text())
    assert reader_mod.norm_session(raw) is not None
    marker = json.loads((feed.root / "omo-feed.json").read_text())
    assert marker["v"] == 1 and marker["profile"] == "default"


def test_writer_kinds_match_reader_kinds(plugin, reader_mod):
    assert set(plugin.feed.KINDS) == set(reader_mod.KINDS)


def test_marker_globs_match_contract(reader_mod):
    assert reader_mod.MARKER_GLOBS == ("plugin-data/*/feed/omo-feed.json",
                                       "profiles/*/plugin-data/*/feed/omo-feed.json")
