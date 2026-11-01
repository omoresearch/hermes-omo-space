"""Metadata-only activity feed (contract v1) and the hook callbacks that write it.

Layout under ``<plugin data dir>/feed/`` (dir 0700, files 0600):

- ``omo-feed.json``           marker, rewritten atomically on register
- ``sessions/<sid>.json``     one presence snapshot per session (atomic replace)
- ``events.jsonl``            append-only event log, rotated to ``events.1.jsonl`` at 512 KiB

Privacy: no hook ever records message text, tool arguments, tool results, error messages, kanban
summaries/reasons or subagent goals. Only names, counters, timestamps and the three ``OMO_*`` ids.
``notify`` text (from the ``omo_notify`` tool) is the single exception: control characters stripped,
secrets masked, capped at 140 characters.

Cost: every hook is a dict update plus at most one small atomic write and one O_APPEND write. No
network, no threads, no locks held across calls. Every hook swallows every exception and returns
``None`` (``pre_tool_call`` in particular must never return a directive: Hermes would treat a
raise or a dict as a block/approve decision). After a filesystem error the writer backs off for
``BACKOFF_S`` instead of retrying on every call.

Session identity in tool hooks: Hermes passes ``task_id`` (always) and ``session_id`` (the owning
agent's session; empty when unknown). For the interactive CLI and the gateway ``task_id`` equals the
session id; for a ``delegate_task`` child ``task_id`` is the subagent id and ``session_id`` is the
child agent's session; one-shot runs without a task id get a per-turn uuid. So: ``session_id`` kwarg
first, ``task_id`` as fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

FEED_VERSION = 1
PLUGIN_NAME = "omo-space"
ROTATE_BYTES = 512 * 1024
MAX_SESSION_FILES = 200
ENDED_TTL_S = 24 * 3600
TMP_TTL_S = 3600
THROTTLE_S = 0.25          # <= 4 presence writes / s / session unless the state changes
PRUNE_EVERY_S = 60.0
BACKOFF_S = 30.0
LONG_TURN_S = 60.0
SETTINGS_TTL_S = 5.0       # hooks re-read the feed/notify_on_finish settings at most this often
NOTIFY_MAX = 140
LEVELS = ("info", "success", "warn")
KINDS = frozenset({
    "session_start", "session_end", "turn_start", "turn_end", "tool_start", "tool_end", "kanban_claimed",
    "kanban_completed", "kanban_blocked", "compaction", "api_error", "notify",
})
_SID_OK = re.compile(r"[A-Za-z0-9._-]{1,128}")
_SID_BAD = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SESSIONS_IN_MEMORY = 512


# --------------------------------------------------------------------------- small helpers

def sanitize_session_id(raw: Any) -> str:
    """Map any value to a safe file stem matching ``[A-Za-z0-9._-]{1,128}``.

    Ids that are already safe pass through unchanged; anything else is scrubbed and suffixed with a
    short digest of the original so distinct ids never collide (``a/b`` vs ``a_b``)."""
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    if _SID_OK.fullmatch(text) and not text.startswith("."):
        return text
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]
    stem = _SID_BAD.sub("_", text).lstrip(".")[:100] or "session"
    return f"{stem}-{digest}"


def _str(value: Any, limit: int = 128) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value) if isinstance(value, (int, float)) else ""
    return _CTRL.sub("", value)[:limit]


def _opt_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def omo_env() -> Optional[dict]:
    """The ``omo`` presence field: which Omo Space workspace/tab launched this process, if any."""
    ws, tab, title = (os.environ.get(k, "") for k in ("OMO_WORKSPACE", "OMO_TAB", "OMO_HERMES_TITLE"))
    if not (ws or tab or title):
        return None
    return {"workspace": _str(ws), "tab": _str(tab), "title": _str(title)}


# --------------------------------------------------------------------------- redaction

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069]")
_SECRET_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{10,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b[A-Fa-f0-9]{33,}\b"),
    re.compile(r"[A-Za-z0-9+/_-]{33,}={0,2}"),
]


def redact_text(text: Any, limit: int = NOTIFY_MAX) -> str:
    """Notify text policy: strip control chars, mask obvious secrets, cap at ``limit`` chars.

    Self-contained on purpose: Hermes's redactor (``agent.redact``) is a private core module, which
    catalog rule 9 asks plugins not to import, so behaviour never depends on the Hermes version."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = _CTRL.sub(" ", text[: limit * 8])
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = " ".join(text.split())
    return text[:limit]


# --------------------------------------------------------------------------- the writer

class Feed:
    """Profile-scoped feed writer. One instance per loaded plugin (per process)."""

    def __init__(self, root: Path, *, profile: str = "default", plugin_version: str = "0.0.0",
                 enabled: bool = True, notify_on_finish: bool = False,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.profile = _str(profile) or "default"
        self.plugin_version = plugin_version
        self.enabled = bool(enabled)
        self.notify_on_finish = bool(notify_on_finish)
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._meta: dict[str, dict] = {}          # sid -> {"last_write", "last_state", "turn_start", ...}
        self._parents: dict[str, str] = {}        # child session id -> parent session id
        self._current: Optional[str] = None       # most recently active session in this process
        self._broken_until = 0.0
        self._dirs_ok = False
        self._last_prune = 0.0
        self.presence_writes = 0
        self.events_written = 0
        self.last_error: Optional[str] = None
        # Optional live settings source: () -> {"feed": bool, "notify_on_finish": bool}. The plugin
        # host can outlive a `hermes config set`, so settings are re-read (throttled) instead of
        # being frozen at register time.
        self.settings_fn: Optional[Callable[[], dict]] = None
        self._settings_at: Optional[float] = None

    # ------------------------------------------------------------------ live settings
    def refresh_settings(self, force: bool = False) -> None:
        """Re-read ``feed`` / ``notify_on_finish`` from ``settings_fn`` (at most every
        ``SETTINGS_TTL_S`` unless ``force``). Never raises; a failing source keeps the old values."""
        fn = self.settings_fn
        if fn is None:
            return
        now = time.monotonic()
        if not force and self._settings_at is not None and now - self._settings_at < SETTINGS_TTL_S:
            return
        self._settings_at = now
        try:
            values = fn()
        except Exception:  # noqa: BLE001 - unreadable config must not change behaviour
            return
        if not isinstance(values, dict):
            return
        was = self.enabled
        if isinstance(values.get("feed"), bool):
            self.enabled = values["feed"]
        if isinstance(values.get("notify_on_finish"), bool):
            self.notify_on_finish = values["notify_on_finish"]
        if self.enabled and not was:
            self._broken_until = 0.0
            self.write_marker()  # switched on at runtime: make the feed discoverable now

    # ------------------------------------------------------------------ filesystem primitives
    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def _usable(self) -> bool:
        return self.enabled and time.monotonic() >= self._broken_until

    def _fail(self, exc: BaseException) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self._broken_until = time.monotonic() + BACKOFF_S
        self._dirs_ok = False

    def _ensure_dirs(self) -> None:
        if self._dirs_ok:
            return
        for d in (self.root, self.sessions_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (d.stat().st_mode & 0o777) != 0o700:
                os.chmod(d, 0o700)
        self._dirs_ok = True

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        try:
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ marker / events
    def write_marker(self) -> bool:
        if not self._usable():
            return False
        try:
            self._ensure_dirs()
            self._atomic_write(self.root / "omo-feed.json", {
                "v": FEED_VERSION, "plugin": PLUGIN_NAME, "plugin_version": self.plugin_version,
                "profile": self.profile, "updated": float(self._clock()),
            })
            self.prune(force=True)
            return True
        except Exception as exc:  # noqa: BLE001 - never raise into Hermes
            self._fail(exc)
            return False

    def build_event(self, kind: str, session_id: Any, **fields: Any) -> dict:
        event = {"v": FEED_VERSION, "ts": float(self._clock()), "profile": self.profile,
                 "session_id": _str(session_id), "kind": kind}
        event.update({k: v for k, v in fields.items() if v is not None})
        return event

    def emit(self, kind: str, session_id: Any, **fields: Any) -> dict:
        event = self.build_event(kind, session_id, **fields)
        if kind not in KINDS or not self._usable():
            return event
        try:
            self._ensure_dirs()
            line = (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
            path = self.root / "events.jsonl"
            try:
                if os.stat(path).st_size + len(line) > ROTATE_BYTES:
                    os.replace(path, self.root / "events.1.jsonl")
            except FileNotFoundError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            self.events_written += 1
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
        return event

    def notify(self, text: Any, level: Any = "info", session_id: Any = "") -> dict:
        level = level if level in LEVELS else "info"
        return self.emit("notify", session_id, text=redact_text(text), level=level)

    # ------------------------------------------------------------------ presence
    def _presence(self, sid: str, *, platform: Any = None, model: Any = None) -> dict:
        p = self._sessions.get(sid)
        now = float(self._clock())
        if p is None:
            if len(self._sessions) >= _MAX_SESSIONS_IN_MEMORY:  # bound memory in long-lived gateways
                oldest = min(self._sessions, key=lambda k: self._sessions[k]["updated"])
                self._sessions.pop(oldest, None)
                self._meta.pop(oldest, None)
            p = {"v": FEED_VERSION, "session_id": sid, "profile": self.profile, "platform": "", "model": "",
                 "state": "idle", "tool": None, "tool_since": None, "turns": 0, "tool_calls": 0,
                 "last_tools": [], "started": now, "updated": now, "pid": os.getpid(),
                 "parent_session_id": self._parents.get(sid), "omo": omo_env(), "kanban": None}
            self._sessions[sid] = p
            self._meta[sid] = {"last_write": None, "last_state": None, "turn_start": None}
        if platform:
            p["platform"] = _str(platform, 64)
        if model:
            p["model"] = _str(model)
        return p

    def _save(self, sid: str, *, force: bool = False) -> None:
        p, meta = self._sessions.get(sid), self._meta.get(sid)
        if p is None or meta is None or not self._usable():
            return
        mono = time.monotonic() if self._clock is time.time else float(self._clock())
        state_changed = p["state"] != meta["last_state"]
        if not (force or state_changed) and meta["last_write"] is not None \
                and mono - meta["last_write"] < THROTTLE_S:
            return
        try:
            self._ensure_dirs()
            self._atomic_write(self.sessions_dir / f"{sid}.json", p)
            meta["last_write"], meta["last_state"] = mono, p["state"]
            self.presence_writes += 1
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def _session_id(self, kwargs: dict, key: str = "session_id", fallback: str = "task_id") -> str:
        raw = kwargs.get(key) or (kwargs.get(fallback) if fallback else None) or ""
        if not isinstance(raw, str):
            raw = str(raw) if isinstance(raw, (int, float)) else ""
        return sanitize_session_id(raw) if raw else ""

    def _touch(self, sid: str, state: Optional[str] = None, **kw: Any) -> dict:
        p = self._presence(sid, **kw)
        if state is not None:
            p["state"] = state
        p["updated"] = float(self._clock())
        self._current = sid
        return p

    # ------------------------------------------------------------------ pruning
    def prune(self, force: bool = False) -> None:
        """Drop ``ended`` snapshots older than 24 h, stale temp files, and cap the dir at 200 files."""
        now_mono = time.monotonic()
        if not force and now_mono - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = now_mono
        if not self._usable():
            return
        try:
            now = float(self._clock())
            entries = []
            with os.scandir(self.sessions_dir) as it:
                for entry in it:
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if entry.name.endswith(".tmp"):
                            if now - entry.stat().st_mtime > TMP_TTL_S:
                                os.unlink(entry.path)
                            continue
                        if not entry.name.endswith(".json"):
                            continue
                        updated, state = entry.stat().st_mtime, None
                        try:
                            if entry.stat().st_size <= 64 * 1024:
                                with open(entry.path, encoding="utf-8") as fh:
                                    data = json.load(fh)
                                state = data.get("state")
                                if isinstance(data.get("updated"), (int, float)):
                                    updated = float(data["updated"])
                        except (OSError, ValueError, AttributeError):
                            pass
                        if state == "ended" and now - updated > ENDED_TTL_S:
                            os.unlink(entry.path)
                            continue
                        entries.append((updated, entry.path))
                    except OSError:
                        continue
            if len(entries) > MAX_SESSION_FILES:
                entries.sort()
                for _, path in entries[: len(entries) - MAX_SESSION_FILES]:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ hook bodies
    def on_session_start(self, **kw: Any) -> None:
        sid = self._session_id(kw, fallback="")
        if not sid:
            return
        self._touch(sid, "idle", platform=kw.get("platform"), model=kw.get("model"))
        self._save(sid, force=True)
        self.emit("session_start", sid, platform=_str(kw.get("platform"), 64) or None,
                  model=_str(kw.get("model")) or None)
        self.prune()

    def pre_llm_call(self, **kw: Any) -> None:
        sid = self._session_id(kw, fallback="")
        if not sid:
            return
        p = self._touch(sid, "thinking", platform=kw.get("platform"), model=kw.get("model"))
        p["turns"] += 1
        p["tool"], p["tool_since"] = None, None
        self._meta[sid]["turn_start"] = float(self._clock())
        self._save(sid)
        self.emit("turn_start", sid)

    def on_session_end(self, **kw: Any) -> None:
        """Hermes fires this at the end of EVERY run_conversation() (i.e. per turn), so it is our
        ``turn_end``; the real session end is ``on_session_finalize``."""
        sid = self._session_id(kw, fallback="")
        if not sid:
            return
        completed = kw.get("completed") is True
        interrupted = kw.get("interrupted") is True
        p = self._touch(sid, "idle" if (completed or interrupted) else "error",
                        platform=kw.get("platform"), model=kw.get("model"))
        p["tool"], p["tool_since"] = None, None
        meta = self._meta[sid]
        meta["interrupted"] = interrupted
        started = meta.pop("turn_start", None)
        meta["turn_start"] = None
        duration_ms = int(round((float(self._clock()) - started) * 1000)) if started is not None else None
        self._save(sid)
        self.emit("turn_end", sid, ok=completed, interrupted=interrupted, duration_ms=duration_ms)
        if self.notify_on_finish and duration_ms is not None and duration_ms > LONG_TURN_S * 1000:
            secs = duration_ms // 1000
            label = "finished" if completed else ("was interrupted" if interrupted else "failed")
            self.notify(f"Hermes ({self.profile}) turn {label} after {secs // 60}m {secs % 60:02d}s",
                        level="success" if completed else "warn", session_id=sid)

    def on_session_finalize(self, **kw: Any) -> None:
        sid = self._session_id(kw, fallback="")
        if not sid:
            return
        p = self._touch(sid, "ended", platform=kw.get("platform"))
        p["tool"], p["tool_since"] = None, None
        self._save(sid, force=True)
        self.emit("session_end", sid, interrupted=bool(self._meta[sid].get("interrupted", False)),
                  platform=p["platform"] or None)
        if self._current == sid:
            self._current = None

    def pre_tool_call(self, **kw: Any) -> None:
        sid = self._session_id(kw)
        if not sid:
            return
        name = _str(kw.get("tool_name"), 64) or "tool"
        p = self._touch(sid, "tool")
        p["tool"], p["tool_since"] = name, float(self._clock())
        self._save(sid)
        self.emit("tool_start", sid, tool=name)

    def post_tool_call(self, **kw: Any) -> None:
        sid = self._session_id(kw)
        if not sid:
            return
        name = _str(kw.get("tool_name"), 64) or "tool"
        result = kw.get("result")
        # Read (never stored) only a short prefix to classify success; O(1) regardless of result size.
        head = result[:256] if isinstance(result, str) else ""
        ok = not re.match(r'\s*\{\s*"error"\s*:', head)
        p = self._touch(sid, "thinking")
        p["tool"], p["tool_since"] = None, None
        p["tool_calls"] += 1
        p["last_tools"] = (p["last_tools"] + [name])[-5:]
        self._save(sid)
        self.emit("tool_end", sid, tool=name, duration_ms=_opt_int(kw.get("duration_ms")), ok=ok)

    def post_compaction(self, **kw: Any) -> None:
        sid = self._session_id(kw, fallback="")
        if sid and sid in self._sessions:
            self._touch(sid)
            self._save(sid)
        self.emit("compaction", sid)

    def api_request_error(self, **kw: Any) -> None:
        sid = self._session_id(kw)
        self.emit("api_error", sid, status_code=_opt_int(kw.get("status_code")),
                  model=_str(kw.get("model")) or None)

    def subagent_start(self, **kw: Any) -> None:
        child = self._session_id(kw, key="child_session_id", fallback="child_subagent_id")
        parent = self._session_id(kw, key="parent_session_id", fallback="")
        if not child:
            return
        if parent:
            if len(self._parents) > _MAX_SESSIONS_IN_MEMORY:
                self._parents.clear()
            self._parents[child] = parent
        p = self._presence(child)
        p["parent_session_id"] = parent or None
        p["platform"] = p["platform"] or "subagent"
        self._save(child, force=True)

    def subagent_stop(self, **kw: Any) -> None:
        child = self._session_id(kw, key="child_session_id", fallback="child_subagent_id")
        if not child or child not in self._sessions:
            return
        p = self._touch(child, "ended")
        p["tool"], p["tool_since"] = None, None
        self._save(child, force=True)
        self.emit("session_end", child, interrupted=kw.get("child_status") == "interrupted")
        if self._current == child:
            self._current = None

    def _kanban(self, state: str, kw: dict) -> None:
        task_id, board = _str(kw.get("task_id")), _str(kw.get("board"))
        sid = self._current if self._current in self._sessions else ""
        if sid:
            p = self._touch(sid)
            p["kanban"] = {"task_id": task_id, "board": board, "state": state}
            self._save(sid, force=True)
        self.emit(f"kanban_{state}", sid, task_id=task_id or None, board=board or None)

    def kanban_task_claimed(self, **kw: Any) -> None:
        self._kanban("claimed", kw)

    def kanban_task_completed(self, **kw: Any) -> None:
        self._kanban("completed", kw)

    def kanban_task_blocked(self, **kw: Any) -> None:
        self._kanban("blocked", kw)

    # ------------------------------------------------------------------ hook table
    HOOK_NAMES = ("on_session_start", "on_session_end", "on_session_finalize", "pre_llm_call", "pre_tool_call",
                  "post_tool_call", "post_compaction", "api_request_error", "subagent_start", "subagent_stop",
                  "kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked")

    def hooks(self) -> dict[str, Callable[..., None]]:
        """Hook name -> callback. Every callback accepts ``**kwargs``, never raises, returns None."""
        return {name: self._safe(name, getattr(self, name)) for name in self.HOOK_NAMES}

    def _safe(self, name: str, fn: Callable[..., None]) -> Callable[..., None]:
        feed = self

        def hook(*_args: Any, **kwargs: Any) -> None:
            try:
                feed.refresh_settings()
            except Exception:  # noqa: BLE001
                pass
            if not feed.enabled:
                return None
            try:
                with feed._lock:
                    fn(**kwargs)
            except Exception as exc:  # noqa: BLE001 - a feed bug must never touch the agent
                feed.last_error = f"{name}: {type(exc).__name__}: {exc}"
            return None

        hook.__name__ = f"omo_space_{name}"
        hook.__qualname__ = hook.__name__
        return hook

    # ------------------------------------------------------------------ readers (CLI, slash, tools)
    def read_presence(self, session_id: Any) -> Optional[dict]:
        sid = sanitize_session_id(session_id) if session_id else ""
        if not sid:
            return None
        if sid in self._sessions:
            return dict(self._sessions[sid])
        try:
            with open(self.sessions_dir / f"{sid}.json", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None
