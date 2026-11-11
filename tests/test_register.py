"""register(ctx) and the manifest agree, and registration degrades on a minimal ctx."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _manifest_text():
    return (ROOT / "plugin.yaml").read_text()


def _manifest_list(key):
    """Tiny block-list reader so the tests need no YAML library."""
    lines = _manifest_text().splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(rf"^{key}:\s*$", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s+(\S+)\s*$", line)
            if m:
                out.append(m.group(1))
            elif line.strip() and not line.startswith(" "):
                break
    return out


def _scalar(key):
    m = re.search(rf"^{key}:\s*(.+)$", _manifest_text(), re.M)
    return m.group(1).strip() if m else None


def test_manifest_scalars():
    assert _scalar("name") == "omo-space"
    assert _scalar("label") == "Omo Space"
    assert _scalar("kind") == "standalone"
    assert _scalar("version") == '"0.3.0"'
    assert re.fullmatch(r'">=0\.\d+\.\d+"', _scalar("requires_hermes"))
    assert _scalar("license") == "MIT"
    assert "requires_env" not in _manifest_text()
    assert "python_dependencies" not in _manifest_text()


def test_manifest_description_discloses_behaviour():
    text = _manifest_text().lower()
    assert "loopback" in text and "plugin-data" in text and "metadata" in text


def test_manifest_config_schema_keys():
    text = _manifest_text()
    block = text.split("config_schema:", 1)[1]
    for key, typ in (("feed", "bool"), ("bridge_url", "str"), ("notify_on_finish", "bool")):
        assert re.search(rf"^  {key}:\s*$", block, re.M) or re.search(rf"^  {key}:\s*{{", block, re.M), key
        assert re.search(rf"{key}:[\s\S]*?type:\s*{typ}", block), key


def test_register_matches_manifest(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", profile="work")
    plugin.register(ctx)
    tools = sorted(t["name"] for t in ctx.tools)
    hooks = sorted({n for n, _ in ctx.hooks})
    assert tools == sorted(_manifest_list("provides_tools")) == ["omo_audit_setup", "omo_create_world", "omo_notify", "omo_status"]
    assert hooks == sorted(_manifest_list("provides_hooks"))
    assert len(ctx.hooks) == len(hooks)  # each hook registered once
    assert {t["toolset"] for t in ctx.tools} == {"omo"}
    assert [c["name"] for c in ctx.commands] == ["omo"]
    assert [c["name"] for c in ctx.cli] == ["omo"]


def test_register_uses_ctx_state_data_dir(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", profile="work")
    plugin.register(ctx)
    marker = json.loads((tmp_path / "pd" / "feed" / "omo-feed.json").read_text())
    assert marker["profile"] == "work" and marker["plugin"] == "omo-space"
    assert marker["plugin_version"] == _scalar("version").strip('"')


def test_register_on_minimal_ctx(plugin, fake_ctx_cls, hermes_home):
    ctx = fake_ctx_cls(minimal=True)  # no state, no get_config, no profile_name
    plugin.register(ctx)
    marker = hermes_home / "plugin-data" / "omo-space" / "feed" / "omo-feed.json"
    assert json.loads(marker.read_text())["profile"] == "default"
    assert sorted(t["name"] for t in ctx.tools) == ["omo_audit_setup", "omo_create_world", "omo_notify", "omo_status"]


def test_profile_derived_from_hermes_home(plugin, fake_ctx_cls, tmp_path, monkeypatch):
    home = tmp_path / "root" / "profiles" / "coder"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx = fake_ctx_cls(minimal=True)
    plugin.register(ctx)
    marker = home / "plugin-data" / "omo-space" / "feed" / "omo-feed.json"
    assert json.loads(marker.read_text())["profile"] == "coder"


def test_register_with_feed_disabled(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"feed": False})
    plugin.register(ctx)
    assert not (tmp_path / "pd").exists()
    # hooks stay registered (manifest == reality) but write nothing
    for name, cb in ctx.hooks:
        assert cb(session_id="s", task_id="s", tool_name="t", args={}) is None
    assert not (tmp_path / "pd").exists()
    notify = next(t for t in ctx.tools if t["name"] == "omo_notify")
    assert notify["check_fn"]() is False
    status = next(t for t in ctx.tools if t["name"] == "omo_status")
    assert status.get("check_fn") is None or status["check_fn"]() is True


def test_register_survives_broken_get_config(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd")

    def broken(key, default=None):
        raise RuntimeError("config unreadable")

    ctx.get_config = broken
    plugin.register(ctx)
    assert (tmp_path / "pd" / "feed" / "omo-feed.json").exists()


def test_register_survives_unwritable_data_dir(plugin, fake_ctx_cls, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ctx = fake_ctx_cls(data_dir=blocker / "pd")
    plugin.register(ctx)  # must not raise
    assert len(ctx.tools) == 4


def test_bad_bridge_url_setting_does_not_break_register(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": "http://evil.example"})
    plugin.register(ctx)
    status = next(t for t in ctx.tools if t["name"] == "omo_status")
    out = json.loads(status["handler"]({}))
    assert out["bridge"]["up"] is False and "loopback" in out["bridge"]["error"]


def test_every_callback_accepts_kwargs(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd")
    plugin.register(ctx)
    for name, cb in ctx.hooks:
        params = inspect.signature(cb).parameters.values()
        assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params), name
    for t in ctx.tools:
        params = inspect.signature(t["handler"]).parameters.values()
        assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params), t["name"]
    params = inspect.signature(ctx.commands[0]["handler"]).parameters.values()
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def test_tool_handlers_return_json_strings(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": "http://127.0.0.1:1"})
    plugin.register(ctx)
    for t in ctx.tools:
        assert isinstance(json.loads(t["handler"]({"text": "hi"}, task_id="x")), dict)
        assert t["schema"]["name"] == t["name"] and t["description"]


def test_slash_command_paragraph(plugin, fake_ctx_cls, tmp_path, fake_bridge, monkeypatch):
    monkeypatch.setenv("OMO_WORKSPACE", "ws1")
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": fake_bridge.url})
    plugin.register(ctx)
    text = ctx.commands[0]["handler"]("", session_id="s-main", platform="cli")
    assert isinstance(text, str) and "\n" not in text.strip()
    assert "reachable" in text and "Lab" in text and str(tmp_path / "pd" / "feed") in text


def test_slash_command_bridge_down(plugin, fake_ctx_cls, tmp_path):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": "http://127.0.0.1:1"})
    plugin.register(ctx)
    text = ctx.commands[0]["handler"]("")
    assert "not reachable" in text and "elsewhere" in text


def test_slash_command_reads_workspace_from_session_presence(plugin, fake_ctx_cls, tmp_path, monkeypatch):
    """Slash handlers run in the gateway process: the OMO_* env may be absent there, so the
    session's own presence snapshot (written in the agent's process) is the source of truth."""
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": "http://127.0.0.1:1"})
    plugin.register(ctx)
    sessions = tmp_path / "pd" / "feed" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s7.json").write_text(json.dumps({"session_id": "s7", "omo": {"workspace": "ws-z", "tab": "t",
                                                                               "title": "Desk Z"}}))
    text = ctx.commands[0]["handler"]("", session_id="s7")
    assert "Desk Z" in text or "ws-z" in text


def test_init_does_not_import_worldgen_eagerly(plugin):
    src = (ROOT / "__init__.py").read_text()
    assert "worldgen" not in src


# ---------------------------------------------------------------- settings are live, not frozen

def test_bridge_url_setting_is_read_per_call(plugin, fake_ctx_cls, tmp_path, fake_bridge, monkeypatch):
    """Regression: the plugin host can outlive `hermes config set`; the tool, /omo and the CLI must
    use the bridge_url that is configured NOW, not the one seen at register()."""
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"bridge_url": "http://127.0.0.1:1"})
    plugin.register(ctx)
    status = next(t for t in ctx.tools if t["name"] == "omo_status")
    assert json.loads(status["handler"]({}))["bridge"]["url"] == "http://127.0.0.1:1"
    ctx._settings["bridge_url"] = fake_bridge.url          # `hermes config set` after register
    out = json.loads(status["handler"]({}))
    assert out["bridge"]["url"] == fake_bridge.url and out["bridge"]["up"] is True
    assert fake_bridge.url in ctx.commands[0]["handler"]("")
    seen = []
    monkeypatch.setattr(plugin.cli, "run", lambda args, rt: seen.append(rt.bridge_url_setting) or 0)
    ctx.cli[0]["handler_fn"](object())
    assert seen == [fake_bridge.url]


def test_feed_setting_is_live(plugin, fake_ctx_cls, tmp_path, monkeypatch):
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", settings={"feed": False})
    plugin.register(ctx)
    monkeypatch.setattr(plugin.feed, "SETTINGS_TTL_S", 0.0)
    notify = next(t for t in ctx.tools if t["name"] == "omo_notify")
    assert notify["check_fn"]() is False
    ctx._settings["feed"] = True                            # switched on after register
    start = ctx.hook("on_session_start")[0]
    start(session_id="s1", platform="cli")
    feed_dir = tmp_path / "pd" / "feed"
    assert (feed_dir / "omo-feed.json").exists() and (feed_dir / "sessions" / "s1.json").exists()
    assert notify["check_fn"]() is True
    ctx._settings["feed"] = False                           # and off again
    before = (feed_dir / "events.jsonl").read_text()
    start(session_id="s2", platform="cli")
    assert (feed_dir / "events.jsonl").read_text() == before
    assert json.loads(notify["handler"]({"text": "hi"}))["queued"] is False


def test_hooks_older_hermes_lacks_are_skipped(plugin, fake_ctx_cls, tmp_path, monkeypatch):
    """A Hermes before v0.22.3 has no post_compaction: the plugin skips it instead of storing an
    unknown hook, and registers every other hook as usual."""
    declared = set(_manifest_list("provides_hooks"))
    monkeypatch.setattr(plugin, "_known_hooks", lambda: frozenset(declared - {"post_compaction"}))
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", profile="work")
    plugin.register(ctx)
    hooks = {n for n, _ in ctx.hooks}
    assert "post_compaction" not in hooks
    assert hooks == declared - {"post_compaction"}
    assert sorted(t["name"] for t in ctx.tools) == ["omo_audit_setup", "omo_create_world", "omo_notify", "omo_status"]


def test_all_hooks_registered_when_hermes_knows_them(plugin, fake_ctx_cls, tmp_path, monkeypatch):
    declared = set(_manifest_list("provides_hooks"))
    monkeypatch.setattr(plugin, "_known_hooks", lambda: frozenset(declared | {"some_future_hook"}))
    ctx = fake_ctx_cls(data_dir=tmp_path / "pd", profile="work")
    plugin.register(ctx)
    assert {n for n, _ in ctx.hooks} == declared


def test_manifest_floor_matches_oldest_tested_hermes():
    assert _scalar("requires_hermes").strip('"') == ">=0.21.6"
