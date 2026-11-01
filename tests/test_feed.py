"""Feed writer + hooks: schema, privacy, throttling, rotation, pruning, robustness."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

CANARY = "CANARY-7f3a-do-not-persist"

MARKER_KEYS = {"v", "plugin", "plugin_version", "profile", "updated"}
PRESENCE_KEYS = {"v", "session_id", "profile", "platform", "model", "state", "tool", "tool_since", "turns",
                 "tool_calls", "last_tools", "started", "updated", "pid", "parent_session_id", "omo", "kanban"}
EVENT_BASE = {"v", "ts", "profile", "session_id", "kind"}
EVENT_EXTRA = {"tool", "duration_ms", "ok", "platform", "model", "interrupted", "status_code", "text", "level",
               # documented additive extras (readers ignore unknown fields)
               "task_id", "board", "parent_session_id"}
KINDS = {"session_start", "session_end", "turn_start", "turn_end", "tool_start", "tool_end", "kanban_claimed",
         "kanban_completed", "kanban_blocked", "compaction", "api_error", "notify"}
STATES = {"idle", "thinking", "tool", "ended", "error"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def feed(plugin, tmp_path, clock):
    return plugin.feed.Feed(tmp_path / "data" / "feed", profile="default", plugin_version="0.1.0", clock=clock)


def _events(feed):
    path = feed.root / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _presence(feed, sid):
    return json.loads((feed.root / "sessions" / f"{sid}.json").read_text())


def _run_full_session(feed, sid="sess-1", text=CANARY):
    h = feed.hooks()
    h["on_session_start"](session_id=sid, model="m1", platform="cli")
    h["pre_llm_call"](session_id=sid, user_message=text, conversation_history=[{"role": "user", "content": text}],
                      is_first_turn=True, model="m1", platform="cli")
    h["pre_tool_call"](tool_name="terminal", args={"command": text, "path": f"/tmp/{text}"}, task_id=sid,
                       session_id=sid, tool_call_id="c1", turn_id="t1")
    h["post_tool_call"](tool_name="terminal", args={"command": text}, result=json.dumps({"output": text}),
                        task_id=sid, session_id=sid, duration_ms=42)
    h["post_tool_call"](tool_name="read_file", args={"path": text}, result=json.dumps({"error": text}),
                        task_id=sid, session_id=sid, duration_ms=3)
    h["post_compaction"](session_id=sid, old_session_id="", kind="summarize", reason="manual",
                         before_tokens=10, after_tokens=5, messages_removed=3)
    h["api_request_error"](task_id=sid, session_id=sid, platform="cli", model="m1", status_code=429,
                           error={"type": "rate", "message": text}, request={"messages": [text]})
    h["subagent_start"](parent_session_id=sid, parent_turn_id="t1", parent_subagent_id=None,
                        child_session_id="child-1", child_subagent_id="sa-1", child_role="leaf", child_goal=text)
    h["subagent_stop"](parent_session_id=sid, child_role="leaf", child_summary=text, child_status="completed",
                       tool_call_history=[{"tool_name": "x", "tool_input": text}], duration_ms=5,
                       child_session_id="child-1")
    h["kanban_task_claimed"](task_id="k1", board="main", assignee="me", run_id="r1", profile_name="default")
    h["kanban_task_completed"](task_id="k1", board="main", assignee="me", run_id="r1", profile_name="default",
                               summary=text)
    h["kanban_task_blocked"](task_id="k2", board="main", assignee="me", run_id="r2", profile_name="default",
                             reason=text)
    h["on_session_end"](session_id=sid, completed=True, interrupted=False, model="m1", platform="cli")
    h["on_session_finalize"](session_id=sid, platform="cli")


# ---------------------------------------------------------------- schema

def test_marker_schema_and_permissions(feed):
    feed.write_marker()
    marker = json.loads((feed.root / "omo-feed.json").read_text())
    assert set(marker) == MARKER_KEYS
    assert marker["v"] == 1 and marker["plugin"] == "omo-space" and marker["plugin_version"] == "0.1.0"
    assert marker["profile"] == "default" and isinstance(marker["updated"], float)
    assert stat.S_IMODE(feed.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((feed.root / "omo-feed.json").stat().st_mode) == 0o600


def test_presence_schema_through_a_turn(feed, clock):
    h = feed.hooks()
    h["on_session_start"](session_id="s1", model="m1", platform="tui")
    p = _presence(feed, "s1")
    assert set(p) == PRESENCE_KEYS
    assert (p["v"], p["session_id"], p["profile"], p["platform"], p["model"]) == (1, "s1", "default", "tui", "m1")
    assert p["state"] == "idle" and p["tool"] is None and p["tool_since"] is None
    assert p["turns"] == 0 and p["tool_calls"] == 0 and p["last_tools"] == []
    assert p["pid"] == os.getpid() and p["parent_session_id"] is None and p["omo"] is None and p["kanban"] is None

    clock.t += 1
    h["pre_llm_call"](session_id="s1", user_message="hi", conversation_history=[], is_first_turn=True,
                      model="m1", platform="tui")
    p = _presence(feed, "s1")
    assert p["state"] == "thinking" and p["turns"] == 1

    clock.t += 1
    h["pre_tool_call"](tool_name="web_search", args={}, task_id="s1")
    p = _presence(feed, "s1")
    assert p["state"] == "tool" and p["tool"] == "web_search" and p["tool_since"] == clock.t

    clock.t += 1
    h["post_tool_call"](tool_name="web_search", args={}, result="{}", task_id="s1", duration_ms=9)
    p = _presence(feed, "s1")
    assert p["state"] == "thinking" and p["tool"] is None and p["tool_since"] is None
    assert p["tool_calls"] == 1 and p["last_tools"] == ["web_search"]

    clock.t += 1
    h["on_session_end"](session_id="s1", completed=True, interrupted=False, model="m1", platform="tui")
    assert _presence(feed, "s1")["state"] == "idle"
    h["on_session_finalize"](session_id="s1", platform="tui")
    p = _presence(feed, "s1")
    assert p["state"] == "ended" and p["updated"] == clock.t and p["started"] == 1_000_000.0
    assert stat.S_IMODE((feed.root / "sessions" / "s1.json").stat().st_mode) == 0o600


def test_failed_turn_sets_error_state(feed):
    h = feed.hooks()
    h["pre_llm_call"](session_id="s1", user_message="x", conversation_history=[], is_first_turn=False,
                      model="m", platform="cli")
    h["on_session_end"](session_id="s1", completed=False, interrupted=False, model="m", platform="cli")
    assert _presence(feed, "s1")["state"] == "error"
    ev = [e for e in _events(feed) if e["kind"] == "turn_end"][-1]
    assert ev["ok"] is False and ev["interrupted"] is False


def test_last_tools_capped_at_five(feed, clock):
    h = feed.hooks()
    for i in range(8):
        clock.t += 1
        h["pre_tool_call"](tool_name=f"t{i}", args={}, task_id="s1")
        clock.t += 1
        h["post_tool_call"](tool_name=f"t{i}", args={}, result="{}", task_id="s1", duration_ms=1)
    p = _presence(feed, "s1")
    assert p["last_tools"] == ["t3", "t4", "t5", "t6", "t7"] and p["tool_calls"] == 8


def test_event_schema_for_every_kind(feed):
    _run_full_session(feed)
    feed.notify("hello", level="success", session_id="sess-1")
    events = _events(feed)
    kinds = {e["kind"] for e in events}
    assert kinds == KINDS
    for e in events:
        assert EVENT_BASE <= set(e), e
        assert set(e) - EVENT_BASE <= EVENT_EXTRA, e
        assert e["v"] == 1 and e["kind"] in KINDS and e["profile"] == "default"
        assert isinstance(e["ts"], float) and isinstance(e["session_id"], str)
    tool_end = [e for e in events if e["kind"] == "tool_end"]
    assert [(e["tool"], e["duration_ms"], e["ok"]) for e in tool_end] == [("terminal", 42, True),
                                                                           ("read_file", 3, False)]
    assert [e["status_code"] for e in events if e["kind"] == "api_error"] == [429]
    end = [e for e in events if e["kind"] == "session_end"][0]
    assert end["interrupted"] is False
    start = [e for e in events if e["kind"] == "session_start"][0]
    assert (start["platform"], start["model"]) == ("cli", "m1")
    note = [e for e in events if e["kind"] == "notify"][0]
    assert (note["text"], note["level"]) == ("hello", "success")


def test_presence_states_are_from_the_contract(feed):
    _run_full_session(feed)
    for f in (feed.root / "sessions").glob("*.json"):
        p = json.loads(f.read_text())
        assert set(p) == PRESENCE_KEYS and p["state"] in STATES


# ---------------------------------------------------------------- privacy

def test_no_content_ever_lands_on_disk(feed, monkeypatch):
    monkeypatch.setenv("SOME_SECRET_ENV", CANARY)
    feed.write_marker()
    _run_full_session(feed, text=CANARY)
    files = [p for p in feed.root.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert CANARY not in path.read_text(errors="replace"), path


def test_subagent_presence_links_parent(feed):
    _run_full_session(feed)
    child = _presence(feed, "child-1")
    assert child["parent_session_id"] == "sess-1" and child["state"] == "ended"


def test_kanban_attaches_to_active_session(feed):
    h = feed.hooks()
    h["pre_llm_call"](session_id="w1", user_message="", conversation_history=[], is_first_turn=True,
                      model="m", platform="cron")
    h["kanban_task_claimed"](task_id="k9", board="b", assignee="a", run_id="r", profile_name="default")
    p = _presence(feed, "w1")
    assert p["kanban"] == {"task_id": "k9", "board": "b", "state": "claimed"}
    ev = [e for e in _events(feed) if e["kind"] == "kanban_claimed"][0]
    assert ev["session_id"] == "w1" and ev["task_id"] == "k9" and ev["board"] == "b"


def test_kanban_without_session_is_event_only(feed):
    feed.hooks()["kanban_task_blocked"](task_id="k1", board="b", reason="secret")
    (ev,) = _events(feed)
    assert ev["kind"] == "kanban_blocked" and ev["session_id"] == ""
    assert not (feed.root / "sessions").exists() or not list((feed.root / "sessions").iterdir())


def test_omo_field_from_env(feed, monkeypatch):
    monkeypatch.setenv("OMO_WORKSPACE", "ws-1")
    monkeypatch.setenv("OMO_TAB", "tab-2")
    monkeypatch.setenv("OMO_HERMES_TITLE", "My desk")
    feed.hooks()["on_session_start"](session_id="s1", model="m", platform="cli")
    assert _presence(feed, "s1")["omo"] == {"workspace": "ws-1", "tab": "tab-2", "title": "My desk"}


def test_tool_hooks_prefer_session_id_kwarg_over_task_id(feed):
    h = feed.hooks()
    # subagent: task_id is the subagent id, session_id is the child agent's session
    h["pre_tool_call"](tool_name="x", args={}, task_id="subagent-0-abc", session_id="child-sess")
    assert (feed.root / "sessions" / "child-sess.json").exists()
    # older payloads (or empty session_id): fall back to task_id
    h["pre_tool_call"](tool_name="x", args={}, task_id="gw-sess", session_id="")
    assert (feed.root / "sessions" / "gw-sess.json").exists()
    assert not (feed.root / "sessions" / "subagent-0-abc.json").exists()


# ---------------------------------------------------------------- notify / redaction

@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnopqrstuvwx1234", "sk-ant-api03-abcdefghijklmnop", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-123456789012-abcdefghij", "AKIAIOSFODNN7EXAMPLE", "Bearer abc.def.ghi-123",
    "0123456789abcdef0123456789abcdef0123", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0123456789+/==",
])
def test_redact_masks_secrets(plugin, secret):
    out = plugin.feed.redact_text(f"deploy done with {secret} ok")
    assert secret not in out
    assert out.startswith("deploy done with") and out.endswith("ok")


def test_redact_strips_control_chars_and_caps(plugin):
    out = plugin.feed.redact_text("a\x00b\x1b[31mc\nd\te" + "x" * 500)
    assert "\x00" not in out and "\x1b" not in out and "\n" not in out
    assert len(out) <= 140


def test_notify_queues_redacted_event(feed):
    queued = feed.notify("build ok sk-abcdefghijklmnopqrstuvwx1234", level="warn", session_id="s1")
    assert queued["kind"] == "notify" and queued["level"] == "warn"
    assert "sk-abcdefghijklmnopqrstuvwx" not in queued["text"]
    (ev,) = _events(feed)
    assert ev == queued


def test_notify_level_defaults_to_info(feed):
    assert feed.notify("x", level="shout")["level"] == "info"
    assert feed.notify("x")["level"] == "info"


def test_notify_on_finish_only_for_long_turns(plugin, tmp_path, clock):
    f = plugin.feed.Feed(tmp_path / "feed", profile="p", plugin_version="0.1.0", clock=clock,
                         notify_on_finish=True)
    h = f.hooks()
    h["pre_llm_call"](session_id="s", user_message="", conversation_history=[], is_first_turn=True, model="m",
                      platform="cli")
    clock.t += 10
    h["on_session_end"](session_id="s", completed=True, interrupted=False, model="m", platform="cli")
    assert not [e for e in _events(f) if e["kind"] == "notify"]
    h["pre_llm_call"](session_id="s", user_message="", conversation_history=[], is_first_turn=False, model="m",
                      platform="cli")
    clock.t += 61
    h["on_session_end"](session_id="s", completed=True, interrupted=False, model="m", platform="cli")
    notes = [e for e in _events(f) if e["kind"] == "notify"]
    assert len(notes) == 1 and notes[0]["level"] == "success" and len(notes[0]["text"]) <= 140
    turn_end = [e for e in _events(f) if e["kind"] == "turn_end"][-1]
    assert turn_end["duration_ms"] == 61000


def test_notify_on_finish_off_by_default(feed, clock):
    h = feed.hooks()
    h["pre_llm_call"](session_id="s", user_message="", conversation_history=[], is_first_turn=True, model="m",
                      platform="cli")
    clock.t += 120
    h["on_session_end"](session_id="s", completed=True, interrupted=False, model="m", platform="cli")
    assert not [e for e in _events(feed) if e["kind"] == "notify"]


# ---------------------------------------------------------------- throttling

def test_presence_writes_throttled_except_state_changes(feed, clock):
    h = feed.hooks()
    h["pre_tool_call"](tool_name="a", args={}, task_id="s1")
    base = feed.presence_writes
    for i in range(20):  # a burst of parallel tool starts: same state, same instant
        h["pre_tool_call"](tool_name=f"b{i}", args={}, task_id="s1")
    assert feed.presence_writes == base  # throttled: <= 4/s
    clock.t += 0.3
    h["pre_tool_call"](tool_name="c", args={}, task_id="s1")
    assert feed.presence_writes == base + 1
    h["post_tool_call"](tool_name="c", args={}, result="{}", task_id="s1", duration_ms=1)  # tool -> thinking
    assert feed.presence_writes == base + 2
    assert _presence(feed, "s1")["state"] == "thinking"


def test_hooks_are_cheap(feed):
    h = feed.hooks()
    start = time.perf_counter()
    for i in range(100):
        h["pre_tool_call"](tool_name="t", args={"x": "y" * 1000}, task_id="s1")
        h["post_tool_call"](tool_name="t", args={}, result="z" * 100_000, task_id="s1", duration_ms=1)
    # 400 hook calls incl. ~200 atomic writes; generous bound for a loaded CI box
    assert time.perf_counter() - start < 10


# ---------------------------------------------------------------- rotation / pruning

def test_events_rotate_at_512_kib_keeping_one_generation(feed):
    feed.root.mkdir(parents=True)
    events = feed.root / "events.jsonl"
    events.write_text(("{\"v\":1,\"kind\":\"filler\"}\n" * 30000)[: 512 * 1024])
    feed.emit("compaction", "s1")
    rotated = feed.root / "events.1.jsonl"
    assert rotated.exists() and rotated.stat().st_size >= 512 * 1024 - 100
    assert events.stat().st_size < 1024
    events.write_text("x" * (512 * 1024))
    feed.emit("compaction", "s2")
    assert rotated.read_text() == "x" * (512 * 1024)
    assert sorted(p.name for p in feed.root.glob("events*")) == ["events.1.jsonl", "events.jsonl"]


def test_prune_ended_older_than_24h_and_cap_200(feed, clock):
    sessions = feed.root / "sessions"
    sessions.mkdir(parents=True)
    now = clock.t
    for i in range(5):  # stale ended -> pruned
        (sessions / f"old-{i}.json").write_text(json.dumps({"state": "ended", "updated": now - 90000}))
    (sessions / "old-live.json").write_text(json.dumps({"state": "idle", "updated": now - 90000}))
    for i in range(210):
        (sessions / f"s{i:03d}.json").write_text(json.dumps({"state": "idle", "updated": now - 1000 + i}))
        os.utime(sessions / f"s{i:03d}.json", (now - 1000 + i, now - 1000 + i))
    os.utime(sessions / "old-live.json", (now - 90000, now - 90000))
    (sessions / ".x.json.123.tmp").write_text("partial")
    os.utime(sessions / ".x.json.123.tmp", (now - 7200, now - 7200))
    feed.prune(force=True)
    names = sorted(p.name for p in sessions.iterdir())
    assert len(names) == 200
    assert not any(n.startswith("old-") for n in names)
    assert "s209.json" in names and "s000.json" not in names
    assert not any(n.endswith(".tmp") for n in names)


# ---------------------------------------------------------------- sanitizing

@pytest.mark.parametrize("raw", ["../../etc/passwd", "..", ".", "", "a/b\\c", "x" * 400, "sp ace", "\x00evil",
                                 "/abs/path", "ünïcödé", None, 12345])
def test_session_ids_sanitized(plugin, feed, raw):
    sid = plugin.feed.sanitize_session_id(raw)
    import re
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", sid)
    assert sid not in (".", "..") and not sid.startswith(".")
    feed.hooks()["on_session_start"](session_id=raw, model="m", platform="cli")
    for p in feed.root.rglob("*"):
        assert feed.root in p.resolve().parents or p.resolve() == feed.root


def test_distinct_ids_do_not_collide_after_sanitizing(plugin):
    assert plugin.feed.sanitize_session_id("a/b") != plugin.feed.sanitize_session_id("a_b")
    assert plugin.feed.sanitize_session_id("normal-id_1.2") == "normal-id_1.2"


# ---------------------------------------------------------------- robustness

ALL_HOOKS = ["on_session_start", "on_session_end", "on_session_finalize", "pre_llm_call", "pre_tool_call",
             "post_tool_call", "post_compaction", "api_request_error", "subagent_start", "subagent_stop",
             "kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked"]


def test_hook_set(feed):
    assert sorted(feed.hooks()) == sorted(ALL_HOOKS)


def test_hooks_never_raise_when_feed_dir_unwritable(plugin, tmp_path, clock):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a dir")
    f = plugin.feed.Feed(blocker / "feed", profile="p", plugin_version="0.1.0", clock=clock)
    f.write_marker()
    for name, cb in f.hooks().items():
        assert cb(session_id="s", task_id="s", tool_name="t", args={}, result="{}", model="m", platform="cli",
                  completed=True, interrupted=False, user_message="u", conversation_history=[],
                  is_first_turn=True, duration_ms=1) is None, name
    assert f.notify("hi")["kind"] == "notify"  # returns what it tried to queue


def test_hooks_never_raise_on_readonly_dir(plugin, tmp_path, clock):
    root = tmp_path / "ro" / "feed"
    root.mkdir(parents=True)
    root.chmod(0o500)
    try:
        f = plugin.feed.Feed(root, profile="p", plugin_version="0.1.0", clock=clock)
        _run_full_session(f)
    finally:
        root.chmod(0o700)


def test_hooks_never_raise_on_garbage_args(feed):
    for name, cb in feed.hooks().items():
        assert cb() is None, name
        assert cb(None, None, None, None, None, None, None) is None, name
        assert cb(session_id=object(), tool_name=["x"], args="notadict", result=b"\xff", duration_ms="soon",
                  model=3, platform={}, completed="yes", status_code="x", task_id=None) is None, name


def test_pre_tool_call_returns_none(feed):
    assert feed.hooks()["pre_tool_call"](tool_name="terminal", args={"command": "rm -rf /"}, task_id="s") is None


def test_pre_llm_call_returns_none(feed):
    assert feed.hooks()["pre_llm_call"](session_id="s", user_message="hi", conversation_history=[],
                                        is_first_turn=True, model="m", platform="cli") is None


def test_disabled_feed_writes_nothing(plugin, tmp_path, clock):
    f = plugin.feed.Feed(tmp_path / "feed", profile="p", plugin_version="0.1.0", clock=clock, enabled=False)
    f.write_marker()
    _run_full_session(f)
    f.notify("x")
    assert not (tmp_path / "feed").exists()


def test_failure_backoff_keeps_hooks_fast(plugin, tmp_path, clock, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("file")
    f = plugin.feed.Feed(blocker / "feed", profile="p", plugin_version="0.1.0", clock=clock)
    calls = []
    real = plugin.feed.os.open

    def counting_open(*a, **k):
        calls.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(plugin.feed.os, "open", counting_open)
    h = f.hooks()
    for _ in range(50):
        h["pre_tool_call"](tool_name="t", args={}, task_id="s")
    assert len(calls) <= 2  # after the first failure the writer backs off instead of retrying every call
