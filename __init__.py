"""omo-space: see your Hermes system as a place inside Omo Space.

Registers (see plugin.yaml, which must list exactly these):
- hooks that write a metadata-only activity feed into this plugin's own profile-scoped data dir;
- tools ``omo_status`` (read-only bridge check) and ``omo_notify`` (queue an in-game notice), toolset ``omo``;
- the ``/omo`` slash command and the ``hermes omo setup|status|feed|world`` CLI.

Requires Hermes v0.22.3+ (the first release with every hook used here; ``post_compaction`` landed in
v0.22.3). Optional context surfaces (``ctx.state``, ``ctx.get_config``, ``ctx.profile_name``,
``register_command``, ``register_cli_command``) are still probed with fallbacks, and Hermes stores an
unknown hook name with a warning, so an older Hermes that ignores ``requires_hermes`` degrades
instead of crashing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from . import cli, schemas, tools
from .feed import Feed

__version__ = "0.1.0"
PLUGIN_ID = "omo-space"
logger = logging.getLogger(__name__)

_FEED: Optional[Feed] = None  # the live writer of this process (for debugging/tests)


# --------------------------------------------------------------------------- context probing

def _setting(ctx: Any, key: str, default: Any) -> Any:
    getter = getattr(ctx, "get_config", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception:  # noqa: BLE001 - unreadable config must not disable the plugin
        return default
    return default if value is None else value


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
    if isinstance(value, int):
        return bool(value)
    return default


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _data_dir(ctx: Any) -> Path:
    try:
        state = getattr(ctx, "state", None)
        data_dir = getattr(state, "data_dir", None) if state is not None else None
        if data_dir:
            return Path(data_dir)
    except Exception:  # noqa: BLE001
        pass
    return _hermes_home() / "plugin-data" / PLUGIN_ID


def _profile(ctx: Any) -> str:
    try:
        name = getattr(ctx, "profile_name", None)
        if isinstance(name, str) and name:
            return name
    except Exception:  # noqa: BLE001
        pass
    home = _hermes_home()
    return home.name if home.parent.name == "profiles" else "default"


# --------------------------------------------------------------------------- /omo

def _slash_text(feed: Feed, bridge_url: Any, session_id: Any) -> str:
    s = tools.status_summary(feed, bridge_url, session_id)
    b, here, counts = s["bridge"], s["this_session"], s["board"]
    if b["url"] is None:
        bridge = f"Omo Space bridge not checked: {b.get('error', 'bridge_url must be loopback')}."
    elif b["up"]:
        extra = f", {counts['workspaces']} workspaces, {counts['live']} live" if counts else ""
        bridge = f"Omo Space bridge is reachable at {b['url']} (v{b['version']}{extra})."
    elif "foreign_uid" in b:
        bridge = f"The Omo Space bridge at {b['url']} is not yours: {b['error']}."
    else:
        bridge = f"Omo Space bridge is not reachable at {b['url']}."
    if here["where"] == "in-world" and here["workspace"]:
        where = f"This conversation lives at the '{here['workspace']}' desk in the world."
    elif here["where"] == "in-world":
        where = "This conversation lives at a desk in the world (the Board lists more than one for this profile)."
    elif here["omo_workspace"]:
        label = here["title"] or here["omo_workspace"]
        tab = f", tab {here['omo_tab']}" if here["omo_tab"] else ""
        where = (f"This conversation was launched from Omo Space workspace '{label}'{tab}"
                 + (", but the bridge did not list it." if b["up"] else "."))
    else:
        where = ("This conversation lives elsewhere (not inside an Omo Space workspace: a gateway chat, "
                 "cron job or plain terminal), so it shows up in the Board's Elsewhere section.")
    f = s["feed"]
    feed_txt = (f"Activity feed (metadata only): {f['dir']}." if f["enabled"]
                else f"Activity feed is disabled (would be {f['dir']}).")
    return " ".join((bridge, where, feed_txt))


def _llm(ctx: Any) -> Any:
    """``ctx.llm.complete_structured`` (Hermes plugin LLM access: the user's active model, host-owned
    auth), resolved lazily so registering never touches a provider. None on a Hermes without it."""
    def call(**kwargs: Any) -> Any:
        llm = getattr(ctx, "llm", None)
        if llm is None or not callable(getattr(llm, "complete_structured", None)):
            raise RuntimeError("this Hermes has no plugin LLM access")
        return llm.complete_structured(**kwargs)
    return call


# --------------------------------------------------------------------------- register

def register(ctx: Any) -> None:
    global _FEED
    enabled = _as_bool(_setting(ctx, "feed", True), True)
    notify_on_finish = _as_bool(_setting(ctx, "notify_on_finish", False), False)
    profile = _profile(ctx)
    feed = Feed(_data_dir(ctx) / "feed", profile=profile, plugin_version=__version__, enabled=enabled,
                notify_on_finish=notify_on_finish)
    _FEED = feed

    # Settings are read per call, never captured: the plugin host may be long-lived (started at
    # `plugins enable`) and must see a later `hermes config set plugins.entries.omo-space.settings...`.
    def bridge_url() -> Any:
        return _setting(ctx, "bridge_url", "")

    def live_settings() -> dict:
        return {"feed": _as_bool(_setting(ctx, "feed", feed.enabled), feed.enabled),
                "notify_on_finish": _as_bool(_setting(ctx, "notify_on_finish", feed.notify_on_finish),
                                             feed.notify_on_finish)}

    feed.settings_fn = live_settings
    if enabled and not feed.write_marker():
        logger.warning("omo-space: feed dir %s is not writable (%s); hooks will stay silent",
                       feed.root, feed.last_error)

    for name, callback in feed.hooks().items():
        ctx.register_hook(name, callback)

    def omo_status(args: Any = None, **kwargs: Any) -> str:
        feed.refresh_settings(force=True)
        return tools.omo_status(args, feed=feed, bridge_url=bridge_url(), **kwargs)

    def omo_notify(args: Any = None, **kwargs: Any) -> str:
        feed.refresh_settings(force=True)
        return tools.omo_notify(args, feed=feed, **kwargs)

    def notify_available() -> bool:
        feed.refresh_settings()
        return feed.enabled

    ctx.register_tool(name="omo_status", toolset="omo", schema=schemas.OMO_STATUS, handler=omo_status,
                      description="Is Omo Space running, and what is on the Hermes Board", emoji="🧱")
    ctx.register_tool(name="omo_notify", toolset="omo", schema=schemas.OMO_NOTIFY, handler=omo_notify,
                      check_fn=notify_available, description="Show a short notice inside Omo Space", emoji="🔔")

    def slash_omo(raw_args: str = "", **kwargs: Any) -> str:
        try:
            feed.refresh_settings(force=True)
            return _slash_text(feed, bridge_url(), kwargs.get("session_id"))
        except Exception as exc:  # noqa: BLE001
            return f"omo-space: status unavailable ({type(exc).__name__})."

    # register_command (v0.10+) and register_cli_command (v0.8+) are probed so that a Hermes too old to
    # enforce requires_hermes (it is honoured from v0.21.2) still loads the hooks and tools.
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command("omo", handler=slash_omo,
                         description="Where this conversation lives in Omo Space, and whether the bridge is up")

    runtime = cli.Runtime(feed=feed, bridge_url_setting=bridge_url(), profile=profile, llm=_llm(ctx))

    def handle_cli(args: Any) -> int:
        feed.refresh_settings(force=True)
        runtime.bridge_url_setting = bridge_url()
        return cli.run(args, runtime)

    register_cli_command = getattr(ctx, "register_cli_command", None)
    if callable(register_cli_command):
        register_cli_command(name="omo", help="Omo Space: setup, doctor, activity feed, Hermes HQ world",
                             setup_fn=cli.setup_parser, handler_fn=handle_cli,
                             description="See this Hermes setup as a place inside Omo Space.")
