"""``hermes omo setup | status | feed | world``.

``setup`` is the guided onboarding checklist (see onboarding.py; never prompts without a TTY).

``status`` is a doctor: bridge reachability on the configured loopback URL, feed dir + marker, age of
the last event, which profiles on this machine have an omo-space feed, and one clear next step.
``feed`` prints (or follows) this profile's event log. ``world`` is provided by the optional
``worldgen`` package (the Hermes HQ datapack generator) and is wired in only when it is present.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from . import onboarding, tools
from .feed import Feed

EVENT_FILES = ("events.1.jsonl", "events.jsonl")


@dataclass
class Runtime:
    feed: Feed
    bridge_url_setting: Any = ""
    profile: str = "default"


# --------------------------------------------------------------------------- argparse

def _load_world_cli():
    """The world generator lives in ``worldgen/`` (optional, shipped separately in development)."""
    try:
        from .worldgen import cli as world_cli  # type: ignore
    except Exception:
        return None
    if not (callable(getattr(world_cli, "add_parser", None)) and callable(getattr(world_cli, "run", None))):
        return None
    return world_cli


def add_install_arguments(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """``--install [WORLD]``, ``--list-worlds`` and ``--saves-dir`` on top of worldgen's ``world`` parser."""
    p.add_argument("--install", nargs="?", const="", default=None, metavar="WORLD",
                   help="write the datapack straight into <saves>/WORLD/datapacks/<namespace>/ (WORLD may be "
                        "omitted when there is only one world); refuses to replace it without --force")
    p.add_argument("--list-worlds", action="store_true", help="list the Omo Space worlds and exit")
    p.add_argument("--saves-dir", default=None,
                   help="Minecraft saves dir (default: Omo Space's, e.g. ~/.local/share/omo/runtime/game/saves)")
    return p


def setup_parser(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="omo_command", metavar="{setup,status,feed,world}")
    se = subs.add_parser("setup", help="Guided checklist: Omo Space, bridge port, profiles, Hermes HQ world")
    se.add_argument("--enable-all", action="store_true",
                    help="offer to enable omo-space in every other profile (asks first; needs a terminal)")
    se.add_argument("--non-interactive", action="store_true", help="print the checklist only, never prompt")
    se.add_argument("--json", action="store_true", help="print the checklist data as JSON")
    st = subs.add_parser("status", help="Doctor: is Omo Space reachable and is this profile's feed healthy?")
    st.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    fd = subs.add_parser("feed", help="Print this profile's Omo activity feed (metadata only)")
    fd.add_argument("-f", "--follow", action="store_true", help="Keep printing new events (Ctrl+C to stop)")
    fd.add_argument("--json", action="store_true", help="Print raw JSON lines")
    fd.add_argument("-n", "--lines", type=int, default=20, metavar="N", help="How many recent events (default 20)")
    world_cli = _load_world_cli()
    if world_cli is not None:
        world = world_cli.add_parser(subs)
        if isinstance(world, argparse.ArgumentParser):
            add_install_arguments(world)
    else:
        subs.add_parser("world", help="Generate the Hermes HQ datapack (world generator not installed)")


def board_port(runtime: Runtime, detect: Any = None) -> tuple[Optional[int], str]:
    """The port the Hermes HQ Board wall should load: this profile's bridge_url setting, else
    OMO_PORT, else the user's own bridge found the way `setup` finds it. (None, "") = leave
    worldgen's default."""
    setting = runtime.bridge_url_setting
    if setting:
        try:
            port = urllib.parse.urlsplit(tools.resolve_bridge_url(setting)).port
            if port:
                return port, "this profile's bridge_url"
        except ValueError:
            pass
    raw = str(os.environ.get("OMO_PORT", "")).strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw), "OMO_PORT"
    try:
        found = (detect or onboarding.detect_bridge)()
    except Exception:  # noqa: BLE001 - detection is best effort
        return None, ""
    if found and found.get("port"):
        return int(found["port"]), "your Omo bridge"
    return None, ""


def run(args: argparse.Namespace, runtime: Runtime) -> int:
    sub = getattr(args, "omo_command", None)
    if sub == "setup":
        return onboarding.setup(args, runtime, world_cli=_load_world_cli())
    if sub == "status":
        return doctor(runtime, as_json=bool(getattr(args, "json", False)))
    if sub == "feed":
        return feed_command(runtime, n=getattr(args, "lines", 20), as_json=bool(getattr(args, "json", False)),
                            follow_=bool(getattr(args, "follow", False)))
    if sub == "world":
        world_cli = _load_world_cli()
        if world_cli is None:
            print("world generator not installed (the omo-space worldgen package is missing)")
            return 1
        if hasattr(args, "bridge_port") and args.bridge_port is None:
            port, source = board_port(runtime)
            if port is not None:
                args.bridge_port = port
                print(f"Board wall -> http://127.0.0.1:{port}/board ({source}; override with --bridge-port)",
                      file=sys.stderr)
        if getattr(args, "install", None) is not None or getattr(args, "list_worlds", False):
            return onboarding.world_install(args, world_cli)
        return world_cli.run(args) or 0
    print("usage: hermes omo {setup,status,feed,world}\n"
          "  setup    guided checklist: Omo Space, bridge port, profiles, Hermes HQ world\n"
          "  status   doctor: bridge, feed, marker, last event, profiles, next step\n"
          "  feed     print this profile's activity feed (-n N, --json, --follow)\n"
          "  world    generate the Hermes HQ Minecraft datapack (--install [WORLD] puts it in a save)")
    return 0


# --------------------------------------------------------------------------- feed reading

def read_events(root: Path, n: int = 20) -> list[dict]:
    events: list[dict] = []
    for name in EVENT_FILES:
        try:
            with open(Path(root) / name, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        events.append(item)
        except OSError:
            continue
    return events[-n:] if n > 0 else []


def format_event(e: dict) -> str:
    ts = e.get("ts")
    clock = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "--:--:--"
    detail = []
    for key in ("tool", "duration_ms", "ok", "status_code", "interrupted", "task_id", "board", "level"):
        if key in e and e[key] is not None:
            detail.append(f"{key}={e[key]}")
    if e.get("text"):
        detail.append(json.dumps(e["text"]))
    sid = str(e.get("session_id") or "-")
    return f"{clock}  {str(e.get('profile', '?')):<10} {sid[:20]:<20} {str(e.get('kind', '?')):<16} {' '.join(detail)}"


def _emit(out: TextIO, e: dict, as_json: bool) -> None:
    out.write((json.dumps(e) if as_json else format_event(e)) + "\n")
    out.flush()


def follow(root: Path, out: TextIO = sys.stdout, *, as_json: bool = False,
           stop: Callable[[], bool] = lambda: False, interval: float = 0.5) -> None:
    path = Path(root) / "events.jsonl"
    try:
        st = os.stat(path)
        ino, pos = st.st_ino, st.st_size
    except OSError:
        ino, pos = None, 0
    buf = b""
    while not stop():
        try:
            st = os.stat(path)
            if st.st_ino != ino or st.st_size < pos:  # rotated or truncated: start the new file
                ino, pos, buf = st.st_ino, 0, b""
            if st.st_size > pos:
                with open(path, "rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read(st.st_size - pos)
                pos += len(chunk)
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        _emit(out, item, as_json)
        except OSError:
            pass
        if interval:
            time.sleep(interval)


def feed_command(runtime: Runtime, *, n: int, as_json: bool, follow_: bool) -> int:
    root = runtime.feed.root
    events = read_events(root, n)
    if not events and not as_json:
        print(f"No events yet in {root}" + ("" if runtime.feed.enabled else " (the feed is disabled)"))
    for e in events:
        _emit(sys.stdout, e, as_json)
    if follow_:
        try:
            follow(root, sys.stdout, as_json=as_json)
        except KeyboardInterrupt:
            pass
    return 0


# --------------------------------------------------------------------------- doctor

def hermes_root() -> Path:
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore

        return Path(get_default_hermes_root())
    except Exception:
        pass
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.parent.parent if home.parent.name == "profiles" else home


def profiles_with_feed(root: Optional[Path] = None) -> list[str]:
    """Profiles whose plugin-data has an omo-space feed marker (the bridge's discovery glob)."""
    root = root or hermes_root()
    found = set()
    try:
        if any(root.glob("plugin-data/*/feed/omo-feed.json")):
            found.add("default")
        for marker in root.glob("profiles/*/plugin-data/*/feed/omo-feed.json"):
            found.add(marker.parents[3].name)
    except OSError:
        pass
    return sorted(found)


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s ago"
    if seconds < 7200:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def doctor_report(runtime: Runtime) -> dict:
    feed = runtime.feed
    probe = tools.probe_bridge(runtime.bridge_url_setting)
    marker_path = feed.root / "omo-feed.json"
    marker: dict = {"path": str(marker_path), "present": marker_path.is_file()}
    if marker["present"]:
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            marker.update(plugin_version=data.get("plugin_version"), updated=data.get("updated"))
        except (OSError, ValueError, AttributeError):
            marker["present"] = False
            marker["error"] = "unreadable"
    last = read_events(feed.root, 1)
    last_event = None
    if last:
        ts = last[-1].get("ts")
        last_event = {"kind": last[-1].get("kind"), "ts": ts,
                      "age_s": round(time.time() - ts, 1) if isinstance(ts, (int, float)) else None}
    profiles = profiles_with_feed()
    bridge = {"url": probe["url"], "up": probe["up"], "version": probe["version"]}
    if "error" in probe:
        bridge["error"] = probe["error"]
    if "foreign_uid" in probe:
        bridge["foreign_uid"] = probe["foreign_uid"]
    counts = tools.board_counts(probe["board"])

    if probe["url"] is None:
        step = ("Fix the bridge_url setting: it must be a loopback URL such as http://127.0.0.1:8780 "
                "(or leave it empty to use OMO_PORT / 8780).")
    elif "foreign_uid" in probe:
        step = (f"Port {probe['url'].rsplit(':', 1)[-1]} is another user's Omo bridge, not yours. Start your own Omo "
                "Space and point this profile at it: set OMO_PORT, or "
                "`hermes config set plugins.entries.omo-space.settings.bridge_url http://127.0.0.1:<port>`.")
    elif not feed.enabled:
        step = ("The feed is off. Set plugins.entries.omo-space.settings.feed: true in this profile's "
                "config.yaml so Omo Space can see this profile.")
    elif not probe["up"]:
        step = (f"Start Omo Space (the Omo bridge) so it can read the feed; it is expected at {probe['url']}. "
                "Set OMO_PORT or the bridge_url setting if it runs on another port.")
    elif not marker["present"]:
        step = (f"The feed marker is missing: check that {feed.root} is writable, then start a Hermes "
                "session in this profile with the plugin enabled.")
    elif isinstance(probe["board"], dict) and "hermes" not in probe["board"]:
        step = (f"Your Omo bridge (v{probe['version']}) predates Hermes feed support: desks, lamps and the "
                "Board wall work, but the Board shows no Hermes activity and no HUD notices arrive, although "
                "this profile's feed is written. Those need the bridge from omoresearch/omo-space PR #40, "
                "coming in the next Omo Space release; update Omo Space then and check again.")
    elif last_event is None:
        step = "All set. Start a chat in this profile and it will appear on the Hermes Board in Omo Space."
    else:
        step = f"All good. Open the Hermes Board in Omo Space ({probe['url']}/board) to see this profile."
    return {"profile": runtime.profile, "bridge": bridge, "board": counts,
            "feed": {"enabled": feed.enabled, "dir": str(feed.root)}, "marker": marker,
            "last_event": last_event, "hermes_root": str(hermes_root()), "profiles_with_feed": profiles,
            "next_step": step}


def doctor(runtime: Runtime, as_json: bool = False) -> int:
    r = doctor_report(runtime)
    if as_json:
        print(json.dumps(r, indent=2))
        return 0
    b = r["bridge"]
    if b["url"] is None:
        bridge = f"refused: {b.get('error', 'bridge_url is not a loopback URL')}"
    elif b["up"]:
        extra = f", {r['board']['workspaces']} workspaces, {r['board']['live']} live" if r["board"] else ""
        bridge = f"reachable at {b['url']} (Omo bridge v{b['version']}{extra})"
    elif "foreign_uid" in b:
        bridge = f"not yours at {b['url']}: {b['error']}"
    else:
        bridge = f"not reachable at {b['url']} ({b.get('error', 'no answer')})"
    feed = f"enabled, writing to {r['feed']['dir']}" if r["feed"]["enabled"] else \
        f"disabled by settings (dir would be {r['feed']['dir']})"
    m = r["marker"]
    marker = (f"omo-feed.json present (plugin {m.get('plugin_version')})" if m["present"]
              else f"omo-feed.json missing ({m['path']})")
    le = r["last_event"]
    last = f"{_age(le['age_s'])} ({le['kind']})" if le and le["age_s"] is not None else "none yet"
    profiles = ", ".join(r["profiles_with_feed"]) or f"none found under {r['hermes_root']}"
    print(f"Omo Space doctor (profile: {r['profile']})")
    for label, value in (("bridge", bridge), ("feed", feed), ("marker", marker), ("last event", last),
                         ("profiles", profiles)):
        print(f"  {label:<11} {value}")
    print(f"Next step: {r['next_step']}")
    return 0
