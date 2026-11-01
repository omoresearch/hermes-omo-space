"""omo_status / omo_notify tools and bridge URL policy."""

from __future__ import annotations

import json
import socket
import time

import pytest


@pytest.fixture
def feed(plugin, tmp_path):
    return plugin.feed.Feed(tmp_path / "feed", profile="default", plugin_version="0.1.0")


# ---------------------------------------------------------------- bridge_url policy

def test_bridge_url_default(plugin):
    assert plugin.tools.resolve_bridge_url("") == "http://127.0.0.1:8780"
    assert plugin.tools.resolve_bridge_url(None) == "http://127.0.0.1:8780"


def test_bridge_url_from_omo_port(plugin, monkeypatch):
    monkeypatch.setenv("OMO_PORT", "9123")
    assert plugin.tools.resolve_bridge_url("") == "http://127.0.0.1:9123"


def test_bad_omo_port_falls_back(plugin, monkeypatch):
    monkeypatch.setenv("OMO_PORT", "not-a-port")
    assert plugin.tools.resolve_bridge_url("") == "http://127.0.0.1:8780"


def test_bridge_url_setting_wins(plugin, monkeypatch):
    monkeypatch.setenv("OMO_PORT", "9123")
    assert plugin.tools.resolve_bridge_url("http://localhost:7000/") == "http://localhost:7000"
    assert plugin.tools.resolve_bridge_url("http://[::1]:7000") == "http://[::1]:7000"
    assert plugin.tools.resolve_bridge_url("http://127.0.0.2:7000") == "http://127.0.0.2:7000"


@pytest.mark.parametrize("url", [
    "http://example.com:8780", "http://10.0.0.5:8780", "http://192.168.1.2", "http://0.0.0.0:8780",
    "http://127.0.0.1.evil.com:8780", "http://127.0.0.1@evil.com", "http://user:pw@127.0.0.1:8780",
    "https://127.0.0.1:8780", "file:///etc/passwd", "ftp://127.0.0.1", "127.0.0.1:8780", "http://:8780",
    "http://localhost.evil.com", "http://[::ffff:8.8.8.8]:80", "http://127.0.0.1:99999",
])
def test_non_loopback_bridge_url_refused(plugin, url):
    with pytest.raises(ValueError):
        plugin.tools.resolve_bridge_url(url)


# ---------------------------------------------------------------- omo_status

def test_status_with_bridge_up(plugin, feed, fake_bridge, monkeypatch):
    monkeypatch.setenv("OMO_WORKSPACE", "ws1")
    monkeypatch.setenv("OMO_TAB", "tab1")
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url, session_id="s-main"))
    assert out["bridge"]["up"] is True and out["bridge"]["version"] == "9.9.9"
    assert out["bridge"]["url"] == fake_bridge.url
    assert out["board"]["workspaces"] == 2 and out["board"]["live"] == 1 and out["board"]["busy"] == 1
    assert out["this_session"]["workspace"] == "Lab" and out["this_session"]["omo_workspace"] == "ws1"
    assert out["feed"]["enabled"] is True and out["feed"]["dir"] == str(feed.root)
    assert set(fake_bridge.hits) == {"/health", "/board.json"}


def test_status_without_workspace_env(plugin, feed, fake_bridge):
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))
    assert out["this_session"]["omo_workspace"] is None
    assert out["this_session"]["where"] == "elsewhere"


def test_status_bridge_down_is_fast_and_clean(plugin, feed):
    with socket.socket() as s:  # a free loopback port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    start = time.monotonic()
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=f"http://127.0.0.1:{port}"))
    assert time.monotonic() - start < 4
    assert out["bridge"]["up"] is False and "error" in out["bridge"]
    assert out["feed"]["dir"] == str(feed.root)


def test_status_refuses_non_loopback_without_network(plugin, feed, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network attempted")

    monkeypatch.setattr(plugin.tools, "_open", boom)
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url="http://example.com"))
    assert out["bridge"]["up"] is False and "loopback" in out["bridge"]["error"]


def test_status_never_raises(plugin):
    out = json.loads(plugin.tools.omo_status("garbage", feed=None, bridge_url=12))
    assert "bridge" in out


def test_fetch_uses_short_timeout(plugin, monkeypatch):
    seen = {}

    def fake_open(req, timeout=None):
        seen["timeout"] = timeout
        raise OSError("refused")

    monkeypatch.setattr(plugin.tools, "_open", fake_open)
    plugin.tools.omo_status({}, feed=None, bridge_url="http://127.0.0.1:1")
    assert seen["timeout"] == 1.5


# ---------------------------------------------------------------- omo_notify

def test_notify_tool_queues_event(plugin, feed):
    out = json.loads(plugin.tools.omo_notify({"text": "Tests green", "level": "success"}, feed=feed,
                                             session_id="s1"))
    assert out["queued"] is True and out["event"]["text"] == "Tests green" and out["event"]["level"] == "success"
    line = json.loads((feed.root / "events.jsonl").read_text().splitlines()[-1])
    assert line == out["event"] and line["session_id"] == "s1"


def test_notify_tool_uses_task_id_when_no_session(plugin, feed):
    out = json.loads(plugin.tools.omo_notify({"text": "x"}, feed=feed, task_id="t-9"))
    assert out["event"]["session_id"] == "t-9"


def test_notify_tool_rejects_empty(plugin, feed):
    out = json.loads(plugin.tools.omo_notify({"text": "   "}, feed=feed))
    assert out["queued"] is False and "error" in out
    assert not (feed.root / "events.jsonl").exists()


def test_notify_tool_disabled_feed(plugin, tmp_path):
    f = plugin.feed.Feed(tmp_path / "feed", profile="p", plugin_version="0.1.0", enabled=False)
    out = json.loads(plugin.tools.omo_notify({"text": "x"}, feed=f))
    assert out["queued"] is False


def test_notify_tool_never_raises(plugin, feed):
    assert json.loads(plugin.tools.omo_notify(None, feed=feed))["queued"] is False
    assert json.loads(plugin.tools.omo_notify({"text": 5}, feed=feed))["queued"] is True


def test_schemas_are_well_formed(plugin):
    for schema in (plugin.schemas.OMO_STATUS, plugin.schemas.OMO_NOTIFY):
        assert schema["name"].startswith("omo_") and len(schema["description"]) > 60
        assert schema["parameters"]["type"] == "object"
    notify = plugin.schemas.OMO_NOTIFY["parameters"]
    assert notify["required"] == ["text"]
    assert notify["properties"]["text"]["maxLength"] == 140
    assert notify["properties"]["level"]["enum"] == ["info", "success", "warn"]


def test_opener_ignores_proxies_and_redirects(plugin, monkeypatch):
    """A loopback GET must never be routed through an HTTP proxy (that would leave the machine) and
    must never follow a redirect elsewhere."""
    import urllib.request
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    opener = plugin.tools._build_opener()
    proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert all(not h.proxies for h in proxies)  # build_opener drops an empty ProxyHandler: no proxy at all
    redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert redirects and all(isinstance(h, plugin.tools._NoRedirect) for h in redirects)


def test_status_through_env_proxy_still_direct(plugin, feed, fake_bridge, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))
    assert out["bridge"]["up"] is True


# ---------------------------------------------------------------- port owned by another Linux user

_TCP_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n")


def _fake_proc(tmp_path, port, uid, *, v4="0100007F", v6=None, state="0A"):
    d = tmp_path / "proc-net"
    d.mkdir(exist_ok=True)
    row = "  {i}: {a}:{p:04X} 00000000:0000 {st} 00000000:00000000 00:00000000 00000000  {u}        0 4435{i} 1\n"
    (d / "tcp").write_text(_TCP_HEADER + (row.format(i=0, a=v4, p=port, st=state, u=uid) if v4 else "")
                           + row.format(i=1, a="0100007F", p=22, st="0A", u=0))
    (d / "tcp6").write_text(_TCP_HEADER + (row.format(i=2, a=v6, p=port, st=state, u=uid) if v6 else ""))
    return d


def test_listener_uids_parses_proc_net(plugin, tmp_path):
    t = plugin.tools
    d = _fake_proc(tmp_path, 8780, 1000)
    assert t.listener_uids("127.0.0.1", 8780, str(d)) == {1000}
    assert t.listener_uids("localhost", 8780, str(d)) == {1000}
    assert t.listener_uids("127.0.0.1", 8781, str(d)) is None       # nothing listening there
    assert t.listener_uids("::1", 8780, str(d)) is None              # v4-only listener
    d6 = _fake_proc(tmp_path, 9000, 1234, v4=None, v6="00000000000000000000000001000000")
    assert t.listener_uids("::1", 9000, str(d6)) == {1234}
    dany = _fake_proc(tmp_path, 9001, 77, v4="00000000")               # 0.0.0.0 also serves loopback
    assert t.listener_uids("127.0.0.1", 9001, str(dany)) == {77}
    dconn = _fake_proc(tmp_path, 9002, 77, state="01")                # ESTABLISHED, not LISTEN
    assert t.listener_uids("127.0.0.1", 9002, str(dconn)) is None
    assert t.listener_uids("127.0.0.1", 8780, str(tmp_path / "missing")) is None


def test_foreign_bridge_is_not_reported_as_reachable(plugin, feed, fake_bridge, tmp_path, monkeypatch):
    import os
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(_fake_proc(tmp_path, port, os.getuid() + 1)))
    monkeypatch.setattr(plugin.tools.sys, "platform", "linux")
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))
    assert out["bridge"]["up"] is False and out["board"] is None
    assert out["bridge"]["foreign_uid"] == os.getuid() + 1
    assert "belongs to another user" in out["bridge"]["error"] and "OMO_PORT" in out["bridge"]["error"]
    assert fake_bridge.hits == []                                     # its board was never fetched


def test_own_bridge_passes_the_owner_check(plugin, feed, fake_bridge, tmp_path, monkeypatch):
    import os
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(_fake_proc(tmp_path, port, os.getuid())))
    monkeypatch.setattr(plugin.tools.sys, "platform", "linux")
    out = json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))
    assert out["bridge"]["up"] is True and "foreign_uid" not in out["bridge"]


def test_owner_check_skipped_off_linux_or_without_proc(plugin, feed, fake_bridge, tmp_path, monkeypatch):
    import os
    port = int(fake_bridge.url.rsplit(":", 1)[1])
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(_fake_proc(tmp_path, port, os.getuid() + 1)))
    monkeypatch.setattr(plugin.tools.sys, "platform", "darwin")
    assert json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))["bridge"]["up"] is True
    monkeypatch.setattr(plugin.tools.sys, "platform", "linux")
    monkeypatch.setattr(plugin.tools, "PROC_NET", str(tmp_path / "unreadable"))
    assert json.loads(plugin.tools.omo_status({}, feed=feed, bridge_url=fake_bridge.url))["bridge"]["up"] is True


def test_notify_is_serialised_with_hooks(plugin, feed):
    """omo_notify runs on a tool thread while hooks run on others (gateway): it must take the feed
    lock so rotation and its written-counter check do not race a concurrent hook."""
    import threading
    feed._lock.acquire()                          # a hook is mid-write on another thread
    done = threading.Event()
    t = threading.Thread(target=lambda: (plugin.tools.omo_notify({"text": "x"}, feed=feed), done.set()))
    t.start()
    try:
        assert not done.wait(0.3)                 # waits for the hook instead of interleaving
    finally:
        feed._lock.release()
    assert done.wait(5)
    t.join(5)
    with feed._lock:                              # re-entrant: a hook may call notify itself
        assert json.loads(plugin.tools.omo_notify({"text": "y"}, feed=feed))["queued"] is True
