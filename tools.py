"""Tool handlers for toolset ``omo`` and the loopback-only bridge client.

The only network access in the whole plugin lives here: plain-HTTP GETs of ``/health`` and
``/board.json`` on a loopback address, 1.5 s timeout, environment proxies ignored, redirects refused.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .feed import Feed, sanitize_session_id

DEFAULT_PORT = 8780
TIMEOUT_S = 1.5
MAX_BODY = 2 * 1024 * 1024


# --------------------------------------------------------------------------- bridge URL policy

def _port_from_env() -> int:
    raw = os.environ.get("OMO_PORT", "").strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_bridge_url(setting: Any) -> str:
    """``bridge_url`` setting > ``OMO_PORT`` > 8780. Raises ``ValueError`` unless the result is a
    plain ``http://`` URL whose host is loopback (``localhost``, ``127.0.0.0/8``, ``::1``)."""
    if setting is None or setting == "":
        return f"http://127.0.0.1:{_port_from_env()}"
    if not isinstance(setting, str):
        raise ValueError("bridge_url must be a string like http://127.0.0.1:8780")
    parts = urllib.parse.urlsplit(setting.strip())
    if parts.scheme != "http":
        raise ValueError("bridge_url must use http:// on a loopback address")
    if "@" in parts.netloc:
        raise ValueError("bridge_url must not contain credentials")
    host = parts.hostname or ""
    if not host or not _is_loopback_host(host):
        raise ValueError(f"bridge_url host {host!r} is not loopback; only 127.0.0.1/localhost/::1 are allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"bridge_url port is invalid: {exc}") from None
    if port is not None and not 0 < port < 65536:
        raise ValueError("bridge_url port is out of range")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("bridge_url must be just scheme://host:port")
    return f"http://{parts.netloc}"


# --------------------------------------------------------------------------- HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # never follow: a redirect could point off the machine


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _open(req: urllib.request.Request, timeout: float = TIMEOUT_S):
    return _build_opener().open(req, timeout=timeout)


def fetch_json(base: str, path: str) -> Any:
    """GET ``base + path`` (base must already be validated loopback) and decode JSON."""
    req = urllib.request.Request(base + path, headers={"Accept": "application/json",
                                                       "User-Agent": "hermes-omo-space"}, method="GET")
    with _open(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read(MAX_BODY).decode("utf-8", "replace"))


def probe_bridge(setting: Any) -> dict:
    """Return ``{"url", "up", "version", "health", "board", "error"}``; never raises."""
    out: dict = {"url": None, "up": False, "version": None, "health": None, "board": None}
    try:
        base = resolve_bridge_url(setting)
    except ValueError as exc:
        out["error"] = f"refused (loopback only): {exc}"
        return out
    out["url"] = base
    try:
        health = fetch_json(base, "/health")
    except Exception as exc:  # noqa: BLE001
        out["error"] = _short_error(exc)
        return out
    out["up"] = isinstance(health, dict) and bool(health.get("ok", True))
    out["health"] = health if isinstance(health, dict) else None
    out["version"] = (health or {}).get("version") if isinstance(health, dict) else None
    try:
        board = fetch_json(base, "/board.json")
        out["board"] = board if isinstance(board, dict) else None
    except Exception as exc:  # noqa: BLE001
        out["board_error"] = _short_error(exc)
    return out


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError) and getattr(exc, "reason", None) is not None:
        exc = exc.reason if isinstance(exc.reason, BaseException) else Exception(str(exc.reason))
    return f"{type(exc).__name__}: {exc}"[:200]


# --------------------------------------------------------------------------- summaries

def board_counts(board: Optional[dict]) -> Optional[dict]:
    if not isinstance(board, dict):
        return None
    workspaces = [w for w in board.get("workspaces") or [] if isinstance(w, dict)]
    tabs = [t for w in workspaces for t in (w.get("tabs") or []) if isinstance(t, dict)]
    hermes = board.get("hermes") if isinstance(board.get("hermes"), dict) else {}
    sessions = [s for s in hermes.get("sessions") or [] if isinstance(s, dict)]
    return {"workspaces": len(workspaces), "live": sum(1 for w in workspaces if w.get("state") == "live"),
            "busy": sum(1 for t in tabs if t.get("busy")), "hermes_sessions": len(sessions),
            "elsewhere": sum(1 for s in sessions if s.get("elsewhere"))}


def locate_session(feed: Optional[Feed], board: Optional[dict], session_id: Any) -> dict:
    """Where this conversation lives: its OMO_* ids (env, or the session's own presence snapshot
    when we run in a gateway process without them) and the workspace name the bridge matched."""
    sid = sanitize_session_id(session_id) if isinstance(session_id, str) and session_id else None
    omo = None
    if feed is not None and sid:
        presence = feed.read_presence(sid)
        if isinstance(presence, dict) and isinstance(presence.get("omo"), dict):
            omo = presence["omo"]
    if omo is None:
        env = {k: os.environ.get(v, "") for k, v in
               (("workspace", "OMO_WORKSPACE"), ("tab", "OMO_TAB"), ("title", "OMO_HERMES_TITLE"))}
        omo = env if any(env.values()) else None
    name = None
    sessions = []
    if isinstance(board, dict) and isinstance(board.get("hermes"), dict):
        sessions = [s for s in board["hermes"].get("sessions") or [] if isinstance(s, dict)]
    for s in sessions:
        if sid and s.get("session_id") == sid and s.get("workspace"):
            name = s["workspace"]
            break
    if name is None and omo and omo.get("workspace"):
        for s in sessions:
            o = s.get("omo") if isinstance(s.get("omo"), dict) else {}
            if o.get("workspace") == omo.get("workspace") and s.get("workspace") \
                    and (not omo.get("tab") or o.get("tab") == omo.get("tab")):
                name = s["workspace"]
                break
    if name:
        where = "in-world"
    elif omo and omo.get("workspace") and board is None:
        where = "unknown (bridge not reachable)"
    else:
        where = "elsewhere"
    return {"session_id": sid, "omo_workspace": (omo or {}).get("workspace") or None,
            "omo_tab": (omo or {}).get("tab") or None, "title": (omo or {}).get("title") or None,
            "workspace": name, "where": where}


def feed_summary(feed: Optional[Feed]) -> dict:
    if feed is None:
        return {"enabled": False, "dir": None, "marker": False}
    return {"enabled": feed.enabled, "dir": str(feed.root), "profile": feed.profile,
            "marker": (feed.root / "omo-feed.json").is_file()}


def status_summary(feed: Optional[Feed], bridge_url: Any, session_id: Any = None) -> dict:
    probe = probe_bridge(bridge_url)
    bridge = {"url": probe["url"], "up": probe["up"], "version": probe["version"]}
    if probe.get("health"):
        bridge["tmux"] = probe["health"].get("tmux")
    if "error" in probe:
        bridge["error"] = probe["error"]
    return {"bridge": bridge, "board": board_counts(probe["board"]),
            "this_session": locate_session(feed, probe["board"], session_id), "feed": feed_summary(feed)}


# --------------------------------------------------------------------------- tool handlers

def omo_status(args: Any = None, *, feed: Optional[Feed] = None, bridge_url: Any = "", **kwargs: Any) -> str:
    try:
        sid = kwargs.get("session_id") or kwargs.get("task_id")
        return json.dumps(status_summary(feed, bridge_url, sid))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"bridge": {"up": False, "error": f"omo_status failed: {type(exc).__name__}"}})


def omo_notify(args: Any = None, *, feed: Optional[Feed] = None, **kwargs: Any) -> str:
    try:
        if not isinstance(args, dict):
            return json.dumps({"queued": False, "error": "expected {\"text\": ..., \"level\"?: ...}"})
        text = args.get("text")
        text = text if isinstance(text, str) else ("" if text is None else str(text))
        if not text.strip():
            return json.dumps({"queued": False, "error": "text is required"})
        if feed is None or not feed.enabled:
            return json.dumps({"queued": False, "error": "the omo-space feed is disabled (setting feed: false)"})
        raw_sid = kwargs.get("session_id") or kwargs.get("task_id") or ""
        sid = sanitize_session_id(raw_sid) if isinstance(raw_sid, str) and raw_sid else ""
        before = feed.events_written
        event = feed.notify(text, level=args.get("level", "info"), session_id=sid)
        if feed.events_written == before:
            return json.dumps({"queued": False, "error": f"could not write the feed: {feed.last_error}",
                               "event": event})
        return json.dumps({"queued": True, "event": event,
                           "note": "Shown as a toast in Omo Space if the bridge is running."})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"queued": False, "error": f"omo_notify failed: {type(exc).__name__}"})
