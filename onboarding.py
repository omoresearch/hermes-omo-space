"""``hermes omo setup`` (guided checklist) and ``hermes omo world --install`` (copy Hermes HQ into a save).

Everything here is read-only except three explicit, user-confirmed actions:

- ``world --install`` writes ``<world>/datapacks/<namespace>/`` (refuses to replace it without
  ``--force``, never touches any other datapack);
- ``setup`` at an interactive terminal may run ``hermes -p <profile> config set ...bridge_url ...``
  and, with ``--enable-all``, ``hermes -p <profile> plugins enable omo-space`` for other profiles,
  each only after a y/N prompt that defaults to No;
- ``setup`` at an interactive terminal may generate + install the world (same y/N prompt).

Without a TTY (cron, the gateway, pipes, CI) ``setup`` prints the checklist and the exact commands
and exits 0: it never prompts, never blocks and never runs anything.

Profile state is read the way Hermes decides it (``hermes_cli.plugins_discovery.gate_manifest``):
a plugin is active when its key is in ``plugins.enabled`` and not in ``plugins.disabled`` of that
profile's ``config.yaml``. config.yaml is only parsed, never written: changes go through the
``hermes`` CLI.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from . import tools

PLUGIN_KEY = "omo-space"
REPO_URL = "https://github.com/omoresearch/hermes-omo-space"
PORT_SCAN = range(8780, 8790)
DEFAULT_NAMESPACE = "hermes_hq"
MAX_LISTED = 6               # enable commands printed in the checklist; --enable-all lists every one
_WORLD_NAME_BAD = re.compile(r"[\\/\x00-\x1f]")


# --------------------------------------------------------------------------- Omo locations

def omo_data_dir(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None, home: Optional[Path] = None) -> Path:
    """Omo's data dir, as documented in omo-space docs/PLATFORMS.md (``OMO_DATA`` overrides):
    Linux ``~/.local/share/omo``, macOS ``~/Library/Application Support/OmoResearch``,
    Windows ``%LOCALAPPDATA%\\OmoResearch``."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if env.get("OMO_DATA"):
        return Path(env["OMO_DATA"]).expanduser()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "OmoResearch"
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "OmoResearch"
    return home / ".local" / "share" / "omo"


def saves_dir(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None, home: Optional[Path] = None) -> Path:
    """``<omo data>/runtime/game/saves`` (the game dir the Omo launcher owns, on every OS)."""
    return omo_data_dir(env, platform, home) / "runtime" / "game" / "saves"


def list_worlds(saves: Path, namespace: str = DEFAULT_NAMESPACE) -> list[dict]:
    """Minecraft worlds (dirs with ``level.dat``) directly under ``saves``, sorted by name."""
    worlds = []
    try:
        entries = sorted(Path(saves).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for d in entries:
        try:
            if not d.is_dir() or d.is_symlink() or not (d / "level.dat").is_file():
                continue
            packs_dir = d / "datapacks"
            packs = sorted(p.name for p in packs_dir.iterdir()) if packs_dir.is_dir() else []
        except OSError:
            continue
        worlds.append({"name": d.name, "path": str(d), "datapacks": packs, "installed": namespace in packs})
    return worlds


class InstallError(Exception):
    pass


def pick_world(saves: Path, name: Optional[str]) -> Path:
    """The world dir for ``name`` (exact folder name). Empty/None: the only world, else an error."""
    worlds = list_worlds(saves)
    names = [w["name"] for w in worlds]
    if not worlds:
        raise InstallError(f"no Minecraft worlds found in {saves} (open Omo Space and create a world first, "
                           "or pass --saves-dir)")
    if not name:
        if len(worlds) == 1:
            return Path(worlds[0]["path"])
        raise InstallError("several worlds found; name one: "
                           + ", ".join(json.dumps(n) for n in names))
    if _WORLD_NAME_BAD.search(name) or name in (".", ".."):
        raise InstallError(f"bad world name {name!r}: give the folder name of a world in {saves}")
    for w in worlds:
        if w["name"] == name:
            return Path(w["path"])
    raise InstallError(f"no world named {name!r} in {saves}; worlds: " + (", ".join(json.dumps(n) for n in names)))


def install_target(world: Path, namespace: str, force: bool) -> Path:
    """``<world>/datapacks/<namespace>``; refuses symlinks and (without force) an existing pack."""
    packs = world / "datapacks"
    target = packs / namespace
    if packs.is_symlink() or target.is_symlink():
        raise InstallError(f"{target} (or its datapacks dir) is a symlink; refusing to write through it")
    if packs.exists() and not packs.is_dir():
        raise InstallError(f"{packs} exists and is not a directory")
    if target.exists() and not force:
        raise InstallError(f"{target} already exists (pass --force to replace that one datapack; "
                           "no other datapack is touched)")
    return target


def reload_instructions(namespace: str) -> str:
    return (f"Next, in Minecraft with that world open (Omo Space can stay running):\n"
            f"  /reload                       (Minecraft reads datapacks at /reload or world load;\n"
            f"                                 nothing can tell whether the world is open, so run it anyway)\n"
            f"  stand in an open, flat area, then\n"
            f"  /function {namespace}:preview    (particle outline only)\n"
            f"  /function {namespace}:build\n"
            f"  /function {namespace}:clear      (removes it again)")


def world_install(args: Any, world_cli: Any, out=None, err=None) -> int:
    """``hermes omo world --install [WORLD]`` / ``--list-worlds``: a thin wrapper over worldgen."""
    out = out or sys.stdout
    err = err or sys.stderr
    ns = getattr(args, "namespace", None) or DEFAULT_NAMESPACE
    saves = Path(args.saves_dir).expanduser() if getattr(args, "saves_dir", None) else saves_dir()
    if getattr(args, "list_worlds", False):
        worlds = list_worlds(saves, ns)
        if getattr(args, "json", False):
            print(json.dumps({"saves": str(saves), "worlds": worlds}, indent=1), file=out)
            return 0
        if not worlds:
            print(f"No Minecraft worlds in {saves}.", file=out)
            return 0
        print(f"Worlds in {saves}:", file=out)
        for w in worlds:
            mark = f"  [{ns} installed]" if w["installed"] else ""
            print(f"  {w['name']}{mark}  ({len(w['datapacks'])} datapacks)", file=out)
        return 0
    if getattr(args, "out", None) or getattr(args, "zip", False):
        print("error: --install writes into the world's datapacks folder; do not combine it with --out/--zip",
              file=err)
        return 2
    try:
        world = pick_world(saves, getattr(args, "install", None))
        target = install_target(world, ns, bool(getattr(args, "force", False)))
    except InstallError as exc:
        print(f"error: {exc}", file=err)
        return 1
    if getattr(args, "dry_run", False):
        rc = world_cli.run(args) or 0
        if rc == 0 and not getattr(args, "json", False):
            print(f"\nWould install into {target} (dry run: nothing written).", file=out)
        return rc
    try:
        target.parent.mkdir(exist_ok=True)   # the world's own datapacks/ dir; nothing else is created
    except OSError as exc:
        print(f"error: cannot create {target.parent}: {exc}", file=err)
        return 1
    want_json = bool(getattr(args, "json", False))
    args.out, args.zip, args.json = str(target), False, True
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = world_cli.run(args) or 0
    finally:
        args.json = want_json
    if rc != 0:
        return rc
    try:
        summary = json.loads(buf.getvalue() or "{}")
    except ValueError:
        summary = {}
    summary.update(world=world.name, installed=str(target))
    if want_json:
        print(json.dumps(summary, indent=1), file=out)
        return 0
    desks = summary.get("desks")
    print(f"Installed Hermes HQ ({desks} desks) into {target}" if desks else f"Installed Hermes HQ into {target}",
          file=out)
    print(reload_instructions(ns), file=out)
    return 0


# --------------------------------------------------------------------------- Hermes profiles

def _load_yaml(path: Path) -> Optional[dict]:
    try:
        from .worldgen.inventory import load_yaml  # stdlib mini-parser with PyYAML/ruamel fast paths
    except Exception:  # noqa: BLE001
        return None
    return load_yaml(path)


def _is_profile_dir(p: Path) -> bool:
    try:
        return p.is_dir() and not p.is_symlink() and ((p / "config.yaml").is_file() or (p / "state.db").exists())
    except OSError:
        return False


def profile_homes(root: Path) -> list[tuple[str, Path]]:
    found = []
    if (root / "config.yaml").is_file() or (root / "state.db").exists():
        found.append(("default", root))
    try:
        entries = sorted((root / "profiles").iterdir(), key=lambda p: p.name) if (root / "profiles").is_dir() else []
    except OSError:
        entries = []
    found += [(d.name, d) for d in entries if d.name != "default" and _is_profile_dir(d)]
    return found


def _plugin_installed(home: Path) -> bool:
    pdir = home / "plugins"
    if (pdir / PLUGIN_KEY / "plugin.yaml").is_file():
        return True
    try:
        for d in pdir.iterdir() if pdir.is_dir() else ():
            manifest = d / "plugin.yaml"
            if manifest.is_file() and manifest.stat().st_size < 64 * 1024:
                if re.search(r"^name:\s*['\"]?omo-space['\"]?\s*$", manifest.read_text("utf-8", "replace"), re.M):
                    return True
    except OSError:
        pass
    return False


def plugin_state(home: Path) -> dict:
    """``{"installed", "enabled"}`` for one profile home; ``enabled`` is None when config is unreadable."""
    cfg = _load_yaml(home / "config.yaml")
    enabled: Optional[bool] = None
    if isinstance(cfg, dict):
        plugins: dict = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}  # type: ignore[assignment]
        allow: list = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []  # type: ignore[assignment]
        deny: list = plugins.get("disabled") if isinstance(plugins.get("disabled"), list) else []  # type: ignore[assignment]
        enabled = PLUGIN_KEY in {str(x) for x in allow} and PLUGIN_KEY not in {str(x) for x in deny}
    return {"installed": _plugin_installed(home), "enabled": enabled}


def profiles_report(root: Path) -> list[dict]:
    from .cli import profiles_with_feed
    feeds = set(profiles_with_feed(root))
    return [{"name": name, "home": str(home), **plugin_state(home), "feed": name in feeds}
            for name, home in profile_homes(root)]


def enable_commands(profiles: Iterable[dict]) -> list[list[str]]:
    cmds = []
    for p in profiles:
        if p["enabled"]:
            continue
        if p["installed"]:
            cmds.append(["hermes", "-p", p["name"], "plugins", "enable", PLUGIN_KEY])
        else:
            cmds.append(["hermes", "-p", p["name"], "plugins", "install", REPO_URL, "--enable"])
    return cmds


# --------------------------------------------------------------------------- bridge port

_PORT_LINE = re.compile(r"\bOMO_PORT=\"?(\d{1,5})\b")


def _service_port(platform: str, home: Path) -> Optional[tuple[int, str]]:
    """OMO_PORT from the Omo launcher's service definition (systemd user unit + drop-ins / launchd)."""
    try:
        if platform == "darwin":
            plist = home / "Library" / "LaunchAgents" / "space.omo.research.bridge.plist"
            if plist.is_file():
                with open(plist, "rb") as fh:
                    data = plistlib.load(fh)
                raw = (data.get("EnvironmentVariables") or {}).get("OMO_PORT")
                if raw and str(raw).isdigit():
                    return int(raw), str(plist)
            return None
        if platform.startswith("linux"):
            unit = home / ".config" / "systemd" / "user" / "omo-bridge.service"
            files = [unit] + sorted((unit.parent / "omo-bridge.service.d").glob("*.conf"))
            found = None
            for f in files:  # later drop-ins win, like systemd
                try:
                    text = f.read_text("utf-8", "replace") if f.is_file() else ""
                except OSError:
                    continue
                for m in _PORT_LINE.finditer(text):
                    found = (int(m.group(1)), str(f))
            return found
    except Exception:  # noqa: BLE001 - a hint only
        return None
    return None


def _owner(port: int, platform: Optional[str] = None) -> Optional[str]:
    """"mine" / "foreign" / None (cannot tell: not Linux, or nothing listening per /proc)."""
    platform = sys.platform if platform is None else platform
    uids = tools.listener_uids("127.0.0.1", port, proc_net=tools.PROC_NET) if platform.startswith("linux") else None
    getuid = getattr(os, "getuid", None)
    if uids is None or getuid is None:
        return None
    return "mine" if getuid() in uids else "foreign"


def _health_url(base: str) -> Optional[dict]:
    try:
        data = tools.fetch_json(tools.resolve_bridge_url(base), "/health")
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) and data.get("ok", True) else None


def _health(port: int) -> Optional[dict]:
    return _health_url(f"http://127.0.0.1:{port}")


def detect_bridge(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None, home: Optional[Path] = None,
                  scan: Iterable[int] = PORT_SCAN) -> dict:
    """Find this user's Omo bridge. Candidates: ``OMO_PORT``, the launcher's service file, then
    8780-8789. Ports owned by another uid (Linux /proc check) are reported and never queried.
    ``{"port", "source", "version", "foreign": [{"port", "source"}], "checked": [...]}``."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    candidates: list[tuple[int, str]] = []
    raw = str(env.get("OMO_PORT", "")).strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        candidates.append((int(raw), "OMO_PORT"))
    svc = _service_port(platform, home)
    if svc and 0 < svc[0] < 65536:
        candidates.append((svc[0], svc[1]))
    candidates += [(p, "scan") for p in scan]
    seen, foreign, checked = set(), [], []
    for port, source in candidates:
        if port in seen:
            continue
        seen.add(port)
        owner = _owner(port, platform)
        if owner == "foreign":
            foreign.append({"port": port, "source": source})
            continue
        if owner is None and source == "scan" and platform.startswith("linux"):
            continue  # nothing of ours listens there (per /proc): do not even connect
        checked.append(port)
        health = _health(port)
        if health is not None and health.get("ok", True):
            return {"port": port, "source": source, "version": health.get("version"), "foreign": foreign,
                    "checked": checked, "owner": owner}
    return {"port": None, "source": None, "version": None, "foreign": foreign, "checked": checked, "owner": None}


# --------------------------------------------------------------------------- setup

def is_interactive(stdin=None, stdout=None) -> bool:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _confirm(question: str, ask: Callable[[str], str]) -> bool:
    try:
        return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _shell(cmd: list[str]) -> str:
    return " ".join(c if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", c) else json.dumps(c) for c in cmd)


def _short(path: Any) -> str:
    try:
        return "~/" + str(Path(path).resolve().relative_to(Path.home().resolve()))
    except (ValueError, OSError):
        return str(path)


def setup_report(runtime: Any) -> dict:
    from .cli import hermes_root
    root = hermes_root()
    data = omo_data_dir()
    bridge = detect_bridge()
    try:
        current = tools.resolve_bridge_url(runtime.bridge_url_setting)
    except ValueError:
        current = None
    url = f"http://127.0.0.1:{bridge['port']}" if bridge["port"] else None
    current_ok = False
    if current and url and current != url:
        # The profile already points at another bridge: keep it if that one is ours and answers.
        import urllib.parse
        port = urllib.parse.urlsplit(current).port or 80
        current_ok = _owner(port) != "foreign" and (_health_url(current) is not None)
    set_cmds = []
    if url and url != current and not current_ok:
        set_cmds.append(["hermes", "-p", runtime.profile, "config", "set",
                         f"plugins.entries.{PLUGIN_KEY}.settings.bridge_url", url])
    profiles = profiles_report(root)
    saves = saves_dir()
    return {"profile": runtime.profile, "hermes_root": str(root), "omo_data": str(data),
            "omo_data_exists": data.is_dir(), "bridge": bridge, "bridge_url": url, "current_bridge_url": current,
            "current_bridge_ok": current_ok,
            "set_bridge_cmds": set_cmds, "profiles": profiles, "enable_cmds": enable_commands(profiles),
            "saves": str(saves), "worlds": list_worlds(saves)}


def print_checklist(r: dict, out) -> None:
    def line(ok: Optional[bool], text: str) -> None:
        mark = {True: "ok", False: "!!", None: "--"}[ok]
        print(f"  [{mark}] {text}", file=out)

    print(f"Omo Space setup (profile: {r['profile']})", file=out)
    line(r["omo_data_exists"], f"Omo Space data dir {_short(r['omo_data'])}"
         + ("" if r["omo_data_exists"] else " not found: install Omo Space first (https://github.com/omoresearch/omo-space)"))
    b = r["bridge"]
    if b["port"]:
        who = "yours" if b["owner"] == "mine" else "reachable"
        src = "service file" if b["source"] not in ("OMO_PORT", "scan") else b["source"]
        line(True, f"Omo bridge {who} at {r['bridge_url']} (v{b['version']}, found via {src})")
    else:
        line(False, "Omo bridge not found on OMO_PORT, the launcher's service file or ports 8780-8789: "
                    "open Omo Space (or start its bridge) and run this again")
    for f in b["foreign"]:
        line(None, f"port {f['port']} belongs to another user's Omo bridge; ignored")
    if r.get("current_bridge_ok"):
        line(True, f"this profile uses the bridge at {r['current_bridge_url']} (reachable, yours); "
                   f"the launcher's bridge is {r['bridge_url']}")
    elif r["set_bridge_cmds"]:
        line(False, f"this profile looks for the bridge at {r['current_bridge_url']}, not {r['bridge_url']}")
    elif r["bridge_url"]:
        line(True, f"this profile's bridge_url resolves to {r['current_bridge_url']}")
    prof = r["profiles"]
    on = [p["name"] for p in prof if p["enabled"]]
    line(bool(on), f"{len(on)} of {len(prof)} Hermes profiles have omo-space enabled")
    width = max([len(p["name"]) for p in prof] + [7])
    for p in prof:
        state = "enabled" if p["enabled"] else ("installed, not enabled" if p["installed"] else "not installed")
        if p["enabled"] is None:
            state = "config unreadable"
        print(f"         {p['name']:<{width}}  {state}{'  (feed seen)' if p['feed'] else ''}", file=out)
    worlds = r["worlds"]
    if worlds:
        names = ", ".join(w["name"] + (" (Hermes HQ installed)" if w["installed"] else "") for w in worlds)
        line(any(w["installed"] for w in worlds), f"worlds in {_short(r['saves'])}: {names}")
    else:
        line(None, f"no Minecraft worlds in {_short(r['saves'])} yet")
    steps = []
    steps += [_shell(c) for c in r["set_bridge_cmds"]]
    if r["enable_cmds"]:
        shown = r["enable_cmds"][:MAX_LISTED]
        steps += [_shell(c) for c in shown]
        if len(r["enable_cmds"]) > len(shown):
            steps.append(f"... {len(r['enable_cmds']) - len(shown)} more profiles: `hermes omo setup --enable-all` "
                         "lists them all and runs them after one prompt (or pick the ones you want)")
    if worlds and not any(w["installed"] for w in worlds):
        target = worlds[0]["name"] if len(worlds) == 1 else "<World>"
        steps.append(_shell(["hermes", "omo", "world", "--install", target]))
        steps.append("then in game: /reload, /function hermes_hq:preview, /function hermes_hq:build")
    if steps:
        print("Next steps (copy and run; `hermes omo setup --enable-all` runs the enable commands after a prompt):",
              file=out)
        for i, s in enumerate(steps, 1):
            print(f"  {i}. {s}", file=out)
    else:
        print("All set: chat with Hermes anywhere and watch the Hermes Board in Omo Space.", file=out)
    if r["enable_cmds"]:
        print("Each `hermes plugins enable` prints when the change takes effect for running sessions "
              "and the gateway.", file=out)


def _run_cmds(cmds: list[list[str]], out, runner: Callable[..., Any]) -> int:
    exe = shutil.which("hermes")
    if exe is None:
        print("`hermes` is not on PATH; run the commands above yourself.", file=out)
        return 1
    failures = 0
    for cmd in cmds:
        print(f"$ {_shell(cmd)}", file=out)
        try:
            rc = runner([exe] + cmd[1:]).returncode
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {type(exc).__name__}: {exc}", file=out)
            rc = 1
        failures += rc != 0
    return failures


def setup(args: Any, runtime: Any, *, out=None, ask: Callable[[str], str] = input,
          interactive: Optional[bool] = None, runner: Callable[..., Any] = subprocess.run,
          world_cli: Any = None) -> int:
    """The guided checklist. Never prompts or runs anything without a TTY. Always exits 0 unless an
    action the user confirmed failed."""
    out = out or sys.stdout
    r = setup_report(runtime)
    if getattr(args, "json", False):
        print(json.dumps(r, indent=1, default=str), file=out)
        return 0
    print_checklist(r, out)
    tty = is_interactive() if interactive is None else interactive
    if getattr(args, "non_interactive", False):
        tty = False
    if not tty:
        if getattr(args, "enable_all", False) and r["enable_cmds"]:
            print("--enable-all needs an interactive terminal to confirm; nothing was run.", file=out)
        return 0
    rc = 0
    if r["set_bridge_cmds"] and _confirm(f"\nPoint profile '{runtime.profile}' at {r['bridge_url']} now?", ask):
        rc |= bool(_run_cmds(r["set_bridge_cmds"], out, runner))
    if getattr(args, "enable_all", False) and r["enable_cmds"]:
        print("\nThese commands enable omo-space in the other profiles:", file=out)
        for c in r["enable_cmds"]:
            print(f"  {_shell(c)}", file=out)
        if _confirm(f"Run these {len(r['enable_cmds'])} commands?", ask):
            rc |= bool(_run_cmds(r["enable_cmds"], out, runner))
    worlds = [w for w in r["worlds"] if not w["installed"]]
    if worlds and world_cli is not None:
        world = worlds[0]["name"]
        if len(worlds) > 1:
            print("\nWorlds without Hermes HQ:", file=out)
            for i, w in enumerate(worlds, 1):
                print(f"  {i}. {w['name']}", file=out)
            try:
                choice = ask("Install Hermes HQ into which world? (number, empty = skip) ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            world = worlds[int(choice) - 1]["name"] if choice.isdigit() and 1 <= int(choice) <= len(worlds) else ""
        elif not _confirm(f"\nGenerate Hermes HQ (one desk per profile) and install it into '{world}'?", ask):
            world = ""
        if world:
            wargs = world_args(world_cli, ["--install", world])
            if r["bridge"]["port"]:
                wargs.bridge_port = r["bridge"]["port"]
            rc |= bool(world_install(wargs, world_cli, out=out))
    return 1 if rc else 0


def world_args(world_cli: Any, argv: list[str]):
    """Parse ``hermes omo world`` argv with worldgen's parser plus our install flags."""
    import argparse
    from .cli import add_install_arguments
    parser = argparse.ArgumentParser(prog="hermes omo")
    subs = parser.add_subparsers(dest="omo_command")
    add_install_arguments(world_cli.add_parser(subs))
    return parser.parse_args(["world", *argv])
