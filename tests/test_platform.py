"""Cross-platform degradation: macOS / Windows code paths without those OSes.

The plugin's only POSIX-specific behaviour is (1) the /proc/net port-ownership check (Linux only),
(2) ``os.getuid`` (absent on Windows), (3) chmod 0700 on the feed dir and (4) ``os.replace`` failing on
Windows while a reader holds the file open. Each must degrade to "skip" or "retry", never raise.
"""

from __future__ import annotations

import os
import sys

import pytest


def test_listener_uids_skips_off_linux(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert plugin.tools.listener_uids("127.0.0.1", 8780) is None
    monkeypatch.setattr(sys, "platform", "win32")
    assert plugin.tools.listener_uids("127.0.0.1", 8780) is None


def test_foreign_listener_without_getuid(plugin, tmp_path, monkeypatch):
    d = tmp_path / "pn"
    d.mkdir()
    (d / "tcp").write_text("hdr\n   0: 0100007F:224C 00000000:0000 0A 0:0 00:0 0  12345 0 1 1\n")
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(d))
    monkeypatch.delattr(os, "getuid", raising=False)
    assert plugin.tools.foreign_listener("http://127.0.0.1:8780") is None


def test_probe_bridge_works_without_getuid_or_proc(plugin, fake_bridge, monkeypatch):
    # (sys.platform is not faked here: ssl would then try to load the Windows cert store.)
    monkeypatch.delattr(os, "getuid", raising=False)
    probe = plugin.tools.probe_bridge(fake_bridge.url)
    assert probe["up"] is True and probe["version"] == "9.9.9" and "foreign_uid" not in probe


def test_onboarding_owner_unknown_off_linux(plugin, monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    assert plugin.onboarding._owner(8780, "win32") is None
    assert plugin.onboarding._owner(8780, "darwin") is None


def test_detect_bridge_scans_by_http_off_linux(plugin, fake_bridge, tmp_path, monkeypatch):
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.delattr(os, "getuid", raising=False)
    r = plugin.onboarding.detect_bridge(env={}, platform="win32", home=tmp_path, scan=[port])
    assert r["port"] == port and r["owner"] is None


def test_feed_skips_chmod_on_windows(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.feed, "_windows", lambda: True)

    def no_chmod(*a, **k):
        raise AssertionError("chmod must not run on Windows")

    monkeypatch.setattr(plugin.feed.os, "chmod", no_chmod)
    root = tmp_path / "feed"
    root.mkdir(mode=0o755)
    f = plugin.feed.Feed(root, profile="default", plugin_version="0.1.0")
    assert f.write_marker() is True and (root / "omo-feed.json").is_file()


def test_feed_still_tightens_mode_on_posix(plugin, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX only")
    root = tmp_path / "feed"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    f = plugin.feed.Feed(root, profile="default", plugin_version="0.1.0")
    assert f.write_marker()
    assert (root.stat().st_mode & 0o777) == 0o700


def _flaky_replace(real, fails):
    state = {"left": fails, "calls": 0}

    def replace(src, dst):
        state["calls"] += 1
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real(src, dst)

    return replace, state


def test_replace_retries_sharing_violation_on_windows(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.feed, "_windows", lambda: True)
    monkeypatch.setattr(plugin.feed, "REPLACE_RETRY_S", 0)
    replace, state = _flaky_replace(os.replace, 2)
    monkeypatch.setattr(plugin.feed.os, "replace", replace)
    f = plugin.feed.Feed(tmp_path / "feed", profile="default", plugin_version="0.1.0")
    assert f.write_marker() is True and f.last_error is None
    assert state["calls"] == 3


def test_replace_gives_up_and_backs_off(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.feed, "_windows", lambda: True)
    monkeypatch.setattr(plugin.feed, "REPLACE_RETRY_S", 0)
    replace, state = _flaky_replace(os.replace, 99)
    monkeypatch.setattr(plugin.feed.os, "replace", replace)
    f = plugin.feed.Feed(tmp_path / "feed", profile="default", plugin_version="0.1.0")
    assert f.write_marker() is False and "PermissionError" in f.last_error
    assert state["calls"] == plugin.feed.REPLACE_RETRIES + 1
    assert not [p for p in (tmp_path / "feed").iterdir() if p.name.endswith(".tmp")]   # tmp cleaned up


def test_replace_does_not_retry_on_posix(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.feed, "_windows", lambda: False)
    replace, state = _flaky_replace(os.replace, 1)
    monkeypatch.setattr(plugin.feed.os, "replace", replace)
    f = plugin.feed.Feed(tmp_path / "feed", profile="default", plugin_version="0.1.0")
    assert f.write_marker() is False and state["calls"] == 1


def test_hermes_root_fallback_handles_windows_style_profile_home(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.cli, "hermes_root", plugin.cli.hermes_root)  # no-op: real function
    home = tmp_path / "Hermes" / "profiles" / "work"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import builtins
    real_import = builtins.__import__

    def no_hermes(name, *a, **k):
        if name == "hermes_constants":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_hermes)
    assert plugin.cli.hermes_root() == tmp_path / "Hermes"


def test_register_on_a_hermes_without_command_apis(plugin, fake_ctx_cls, tmp_path):
    """An old Hermes (before v0.10 / v0.8) has no register_command / register_cli_command; it also
    ignores requires_hermes (honoured from v0.21.2). The hooks and tools must still register."""

    class OldCtx(fake_ctx_cls):
        register_command = None
        register_cli_command = None

    ctx = OldCtx(minimal=True)
    plugin.register(ctx)
    assert {t["name"] for t in ctx.tools} == {"omo_status", "omo_notify"}
    assert len(ctx.hooks) == 13 and ctx.commands == [] and ctx.cli == []
