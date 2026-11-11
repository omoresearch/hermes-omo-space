"""``hermes omo`` / ``hermes omo create``: describe the world you want (or let Hermes be creative),
Hermes designs it from your profiles, you look at the plan, and it builds a new Omo Space world with
every profile at a desk in its own room.

    1. The setup audit              what this install looks like (read-only metadata), in plain words
       "Use your model to design it?" — nothing is sent before you say yes (n = design without the model)
    2. "What kind of world?"        free text; Enter = be creative
    3. Hermes designs it             your active model via ctx.llm, from the audit summary
    4. The plan                      name, look, rooms, who sits where and WHY
       Build it / Another idea / Change something
    5. Built                         a NEW world folder + the Hermes HQ datapack inside it
    6. "Light up every desk?"        enable omo-space in the other profiles so their desks go live

The pack builds the hall around you the first time you open the world: nothing to type in game.
Without a model (old Hermes, offline, --no-ai) the plan comes from your profile names instead.
Every step has a flag (wish words, --yes, --json) so it also runs unattended; no TTY implies --yes.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import audit as setup_audit
from . import design, firsttime, onboarding

ORIGIN = (0, -60, 0)            # build origin on the flat ground (feet y -60)
SPAWN = (0, -60, -10)           # 10 blocks in front of the arch, facing it
MAX_DESKS = 96
NS = onboarding.DEFAULT_NAMESPACE


# --------------------------------------------------------------------------- colour
class Paint:
    GOLD, DIM, BOLD, GREEN, RESET = "\033[38;5;179m", "\033[2m", "\033[1m", "\033[32m", "\033[0m"

    def __init__(self, on: bool):
        self.on = on

    def __call__(self, text: str, *codes: str) -> str:
        return "".join(codes) + text + self.RESET if self.on and codes else text


def _tty(stream) -> bool:
    try:
        return bool(stream.isatty()) and not os.environ.get("NO_COLOR")
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- argparse
def add_parser(subs: Any) -> argparse.ArgumentParser:
    p = subs.add_parser(
        "create", help="Design and build a world with your Hermes agents in it (start here)",
        description="Say what kind of world you want (or nothing, and Hermes gets creative). Hermes designs it "
                    "from your profiles, shows you the plan, and builds a new Omo Space world where every "
                    "profile has a desk with screens in its own room.")
    p.add_argument("wish", nargs="*", help="what kind of world, e.g. a cosy library in the snow")
    p.add_argument("--name", help="world name (default: the one Hermes picks)")
    p.add_argument("--profiles", help="only these profiles (comma separated; default: all)")
    p.add_argument("--exclude", help="leave these profiles out (comma separated)")
    p.add_argument("--no-ai", action="store_true", help="plan rooms from profile names, without the model")
    p.add_argument("--for", dest="target", choices=["auto", "omo", "minecraft"], default="auto",
                   help="omo = Omo Space (live screens and agents), minecraft = plain Minecraft 26.2 "
                        "(the building only); auto picks Omo Space when it is installed")
    p.add_argument("--saves-dir", help="Minecraft saves dir (default: Omo Space's, or the Minecraft launcher's)")
    p.add_argument("--bridge-port", type=int, default=None, help="Omo bridge port for the Board wall")
    p.add_argument("--hermes-root", default=None, help="Hermes root (default $HERMES_ROOT or ~/.hermes)")
    p.add_argument("--audit", action="store_true", help="only audit this setup and print what would be sent, then stop")
    p.add_argument("-y", "--yes", action="store_true", help="build the first plan without asking")
    p.add_argument("--json", action="store_true", help="machine-readable result (implies --yes)")
    return p


def default_args() -> argparse.Namespace:
    """Args for a bare `hermes omo`."""
    parser = argparse.ArgumentParser(prog="hermes omo")
    add_parser(parser.add_subparsers(dest="omo_command"))
    return parser.parse_args(["create"])


# --------------------------------------------------------------------------- bits
def _unique_name(saves: Path, base: str, newworld: Any) -> str:
    name, n = base, 2
    while (saves / newworld.folder_name(name)).exists():
        name, n = f"{base} {n}", n + 1
    return name


def _spin(out, paint: Paint, text: str, fn: Callable[[], Any], live: bool) -> Any:
    """Run fn() while a small spinner line ticks (only at a terminal)."""
    if not live:
        return fn()
    done = threading.Event()
    box: dict = {}

    def work():
        try:
            box["v"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["e"] = exc
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    t0 = time.time()
    for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
        if done.wait(0.1):
            break
        out.write(f"\r    {paint(ch, paint.GOLD)} {text} {paint(f'{time.time() - t0:.0f}s', paint.DIM)}  ")
        out.flush()
    out.write("\r\033[K")
    out.flush()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _icon_png(themes: list[str]) -> bytes:
    """64x64 world icon: the first two room colours as a soft diagonal, ink panel, gold H."""
    import struct
    import zlib
    pal = {"birch": (226, 196, 120), "cherry": (233, 160, 190), "library": (140, 96, 60),
           "prismarine": (70, 140, 140), "copper": (190, 110, 70), "tuff": (120, 150, 90),
           "quartz": (220, 225, 230), "purpur": (170, 120, 190)}
    a = pal.get(themes[0] if themes else "", pal["prismarine"])
    b = pal.get(themes[1] if len(themes) > 1 else "", pal["cherry"])
    ink, gold = (28, 26, 24), (222, 178, 76)
    H = ["#..#", "#..#", "####", "#..#", "#..#"]
    rows = []
    for y in range(64):
        row = bytearray([0])
        for x in range(64):
            t = min(1.0, max(0.0, (x + y) / 126))
            c = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
            if 8 <= x < 56 and 8 <= y < 56:
                c = tuple(int(v * 0.25 + ink[i] * 0.75) for i, v in enumerate(c))
            gx, gy = (x - 20) // 6, (y - 17) // 6
            if 0 <= gx < 4 and 0 <= gy < 5 and H[gy][gx] == "#" and 20 <= x < 44 and 17 <= y < 47:
                c = gold
            row += bytes(c)
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def _term_width(out, default: int = 100) -> int:
    try:
        return max(60, min(160, os.get_terminal_size(out.fileno()).columns))
    except (OSError, AttributeError, ValueError):
        return default


def show_plan(plan: dict, say: Callable[[str], None], p: Paint, width: int = 100) -> None:
    look = (f"{design.STYLES[plan['style']][0]} · {design.GROUNDS[plan['ground']].lower()} · "
            f"{design.TIMES[plan['time']][0].lower()}")
    say("")
    say(f"  {p('◆', p.GOLD)} {p(plan['name'], p.BOLD)}"
        + (f"   {p(plan['tagline'], p.DIM)}" if plan.get("tagline") else ""))
    say(f"    {p(look, p.DIM)}")
    say("")
    nw = max(len(r["name"]) for r in plan["rooms"])
    pad = " " * (4 + nw + 6)
    room_w = max(30, width - len(pad) - 2)
    for r in plan["rooms"]:
        head = r.get("about") or ""
        why = r.get("why") or ""
        say(f"    {p(r['name'].ljust(nw), p.BOLD)}  {p(str(len(r['profiles'])).rjust(2), p.GOLD)}  {head}")
        if why:
            for wl in _wrap("why: " + why, width, pad, pad + "     "):
                say(p(wl, p.DIM))
        line, rows = "", []
        for n in r["profiles"]:
            add = (", " if line else "") + n
            if len(line) + len(add) > room_w:
                rows.append(line + ",")
                line = n
            else:
                line += add
        if line:
            rows.append(line)
        for row in rows[:3]:
            say(f"{pad}{p(row, p.DIM)}")
        if len(rows) > 3:
            more = sum(len(x.split(", ")) for x in rows[3:])
            say(f"{pad}{p(f'… and {more} more', p.DIM)}")


def _wrap(text: str, width: int, first: str, rest: str) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=max(40, width - 1), initial_indent=first, subsequent_indent=rest,
                         break_on_hyphens=False, break_long_words=False) or [first]


def show_audit(a: dict, say: Callable[[str], None], p: Paint, width: int = 100) -> None:
    say("")
    say(f"  {p('◆', p.GOLD)} {p('Your setup', p.BOLD)}   {p('(read-only: names and counts, no chats or keys)', p.DIM)}")
    for line in setup_audit.summary_lines(a):
        rows = _wrap(line, width - 7, "", "")
        say(f"    {p('·', p.GOLD)} {rows[0]}")
        for r in rows[1:]:
            say(f"      {r}")


# --------------------------------------------------------------------------- the command
def light_up(root: Path, names: list[str], say: Callable[[str], None], p: Paint, ask: Callable[[str], str],
             runner: Optional[Callable[..., Any]] = None, width: int = 100) -> Optional[bool]:
    """After a build: offer to enable omo-space in the profiles that sit at a desk but have it off, so their
    desks go live. One y/N for the whole batch; never without a terminal. Returns how many failed."""
    try:
        report = {r["name"]: r for r in onboarding.profiles_report(root)}
    except Exception:  # noqa: BLE001
        return 0
    todo = [report[n] for n in names if n in report and not report[n]["enabled"]]
    if not todo:
        return None
    cmds = onboarding.enable_commands(todo)
    say("")
    say(f"    {p('Light up every desk?', p.BOLD)}")
    for r in _wrap(f"{len(todo)} of the {len(names)} agents don't have omo-space yet, so their desks stay dark. "
                   f"This runs `hermes -p <profile> plugins enable omo-space` for each of them "
                   f"({', '.join(t['name'] for t in todo[:3])}{', …' if len(todo) > 3 else ''}).",
                   width, "    ", "    "):
        say(p(r, p.DIM))
    try:
        yes = ask(f"    {p('›', p.GOLD)} [Y/n] ").strip().lower() in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        yes = False
    if not yes:
        say(p("    Skipped. Later: `hermes omo setup --enable-all`.", p.DIM))
        return False
    import io
    import subprocess
    buf = io.StringIO()
    failed = onboarding._run_cmds(cmds, buf, runner or (lambda c: subprocess.run(
        c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)))
    ok = len(cmds) - failed
    say(f"    {p('✓', p.GREEN)} {ok} desk{'s' if ok != 1 else ''} lit"
        + (f"; {failed} failed (see `hermes omo setup`)" if failed else ""))
    return failed == 0


def run(args: Any, *, board_port: Optional[int] = None, llm: Optional[Callable[..., Any]] = None,
        out=None, err=None, ask: Callable[[str], str] = input, interactive: Optional[bool] = None,
        runner: Optional[Callable[..., Any]] = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        from .worldgen import datapack as dp
        from .worldgen import inventory as inv
        from .worldgen import newworld
    except Exception as exc:  # noqa: BLE001
        print(f"error: the world generator is not installed ({exc})", file=err)
        return 1
    quiet = bool(args.json)
    if interactive is None:
        interactive = onboarding.is_interactive()
    interactive = bool(interactive) and not (args.yes or quiet)
    live = _tty(out) and not quiet
    p = Paint(live)

    def say(line: str = "") -> None:
        if not quiet:
            print(line, file=out)

    saves, vanilla = onboarding.pick_target(args.saves_dir, getattr(args, "target", "auto") or "auto")
    if not args.saves_dir and vanilla and not saves.parent.is_dir():
        print(f"error: Minecraft isn't installed here ({saves.parent} not found). Leave out --for minecraft and the "
              "world is saved for Omo Space instead, which brings its own Minecraft.", file=err)
        return 1
    no_game = not args.saves_dir and not vanilla and not onboarding.omo_data_dir().is_dir()
    root = Path(args.hermes_root).expanduser() if args.hermes_root else inv.hermes_root()
    data = inv.collect(root, only=args.profiles, exclude=args.exclude)
    profiles = data["profiles"][:MAX_DESKS]
    if not profiles:
        print(f"error: no Hermes profiles found under {root}"
              + (" matching --profiles/--exclude" if args.profiles or args.exclude else ""), file=err)
        return 1
    for m in data.get("missing") or []:
        print(f"warning: no profile named {m!r}", file=err)
    if args.no_ai:
        llm = None
    try:
        audit = setup_audit.run(profiles, Path(data["root"]))
    except Exception:  # noqa: BLE001 - an audit problem must never stop a world being made
        audit = None
    if getattr(args, "audit", False):
        if audit is None:
            print("error: could not audit this setup", file=err)
            return 1
        if args.json:
            print(json.dumps(setup_audit.as_json(audit), indent=1), file=out)
            return 0
        width = _term_width(out) if live else 100
        show_audit(audit, say, p, width)
        say("")
        for r in _wrap(f"Sent to your model when you design a world: {setup_audit.SENT}.", width, "    ", "    "):
            say(p(r, p.DIM))
        for r in _wrap(f"Never sent: {setup_audit.NEVER}.", width, "    ", "    "):
            say(p(r, p.DIM))
        say("")
        for line in setup_audit.model_text(audit).splitlines():
            say("    " + line)
        return 0
    width = _term_width(out) if live else 100

    try:
        n = len(profiles)
        say("")
        say(f"  {p('◆', p.GOLD)} {p('Omo Space', p.BOLD)}   {p('a world for your Hermes agents', p.DIM)}")
        if n == 1:
            say("    Hermes will build you a small Minecraft world with a desk for your agent.")
        else:
            say(f"    Hermes will build you a Minecraft world where each of your {n} agents (profiles) has a desk,")
            say("    grouped into rooms by what they do.")
        say(p("    You'll see the plan before anything is made. Ctrl+C stops at any time.", p.DIM))
        if vanilla:
            say(p("    (For plain Minecraft: the full building, screens as dark glass. In Omo Space every screen", p.DIM))
            say(p("    is a live agent you can talk to.)", p.DIM))

        if audit is not None:
            show_audit(audit, say, p, width)
        if interactive and llm is not None:
            say("")
            say(f"    {p('Use your Hermes model to design it from this?', p.BOLD)}")
            for r in _wrap("Your own model and login, nothing new to set up. Nothing is sent until you say yes. "
                           f"It gets: {setup_audit.SENT}. Never {setup_audit.NEVER}.", width, "    ", "    "):
                say(p(r, p.DIM))
            for r in _wrap("n = design it here without the model.  `hermes omo create --audit` shows the exact text.",
                           width, "    ", "    "):
                say(p(r, p.DIM))
            okd = ask(f"    {p('›', p.GOLD)} [Y/n] ").strip().lower()
            if okd in ("q", "quit", "exit"):
                say("    Nothing written.")
                return 0
            if okd not in ("", "y", "yes"):
                llm = None
                say(p("    OK: designing without the model.", p.DIM))

        wish = " ".join(args.wish or []).strip()
        if interactive and not wish:
            say("")
            say(f"    {p('What kind of world would you like?', p.BOLD)}  {p('Describe it in your own words,', p.DIM)}")
            say(p("    or just press Enter and Hermes will surprise you.", p.DIM))
            say(p("    e.g.  a cosy library in the snow  ·  a sunny beach office  ·  a quiet forest studio", p.DIM))
            wish = ask(f"    {p('›', p.GOLD)} ").strip()

        def make(prev=None, change=""):
            label = "Reworking the plan…" if change else "Hermes is designing your world…"
            return _spin(out, p, label, lambda: design.design(profiles, wish, llm, previous=prev, change=change,
                                                              audit=audit), live)

        plan, source = make()
        while True:
            if args.name:
                plan["name"] = args.name
            show_plan(plan, say, p, width)
            if source == "fallback" and llm is not None:
                say(p("    (No answer from the model, so rooms follow your profile names.)", p.DIM))
            if not interactive:
                break
            say("")
            say(f"    Happy with it? {p('Enter', p.BOLD)} builds it.  {p('a', p.BOLD)} = a different idea,  "
                f"{p('q', p.BOLD)} = quit,")
            say(p("    or type what to change, e.g. \"make it night\" or \"put the editors together\".", p.DIM))
            choice = ask(f"    {p('›', p.GOLD)} ").strip()
            low = choice.lower()
            if low in ("", "y", "yes", "b", "build"):
                break
            if low in ("q", "quit", "n", "no", "exit"):
                say("    Nothing written.")
                return 0
            if low in ("a", "another", "again"):
                plan, source = make()
            else:
                plan, source = make(plan, choice)
    except (KeyboardInterrupt, EOFError):
        print("\n    Stopped; nothing written.", file=out)
        return 130

    name = args.name or _unique_name(saves, plan["name"], newworld)
    index = {pr["name"]: i for i, pr in enumerate(profiles)}
    order = [index[n] for r in plan["rooms"] for n in r["profiles"]]
    ordered = [profiles[i] for i in order]
    rooms, k = [], 0
    for r in plan["rooms"]:
        rooms.append({"name": r["name"], "theme": design.THEMES[r["theme"]],
                      "about": r.get("why") or r.get("about", ""),
                      "desks": list(range(k, k + len(r["profiles"])))})
        k += len(r["profiles"])
    tod = design.TIMES[plan["time"]][1]
    port = args.bridge_port or board_port or 8780

    say("")
    t0 = time.time()
    try:
        files = dp.build_files(dict(data, profiles=ordered), namespace=NS, bridge_port=port, max_desks=MAX_DESKS,
                               rooms=rooms, vanilla=vanilla,
                               arrival={"origin": list(ORIGIN), "title": name, "time": tod,
                                        "tagline": plan.get("tagline", "")})
        world = newworld.create_world(saves, name, ground=plan["ground"], time=tod, spawn=SPAWN)
    except (ValueError, newworld.WorldError, OSError) as exc:
        print(f"error: {exc}", file=err)
        return 1
    try:
        (world / "icon.png").write_bytes(_icon_png([r["theme"] for r in plan["rooms"]]))
        dp.write_output(files, world / "datapacks" / NS)
        (world / "omo-plan.json").write_text(json.dumps(dict(plan, name=name), indent=1) + "\n")
    except (dp.OutputError, OSError) as exc:
        print(f"error: could not finish the world at {world}: {exc}", file=err)
        return 1
    say(f"    {p('✓', p.GREEN)} {len(rooms)} room{'s' if len(rooms) != 1 else ''}, {len(ordered)} "
        f"desk{'s' if len(ordered) != 1 else ''} with screens, the Hermes Board")
    say(f"    {p('✓', p.GREEN)} New world \"{name}\" ({time.time() - t0:.1f}s)")

    result = {"world": world.name, "path": str(world), "plan": dict(plan, name=name), "designed_by": source,
              "game": "minecraft" if vanilla else "omo",
              "desks": len(ordered), "board_url": f"http://127.0.0.1:{port}/board"}
    if quiet:
        print(json.dumps(result, indent=1), file=out)
        return 0
    say("")
    remember_last(world, vanilla)
    if no_game:
        say(f"  {p('◆', p.GOLD)} {p('Your world is ready.', p.BOLD)}")
        for line in firsttime.no_game_text(saves)[:-1]:
            say("    " + line)
        return 0
    lit = light_up(root, [pr["name"] for pr in ordered], say, p, ask, runner, width) if interactive else None
    if lit is not None:
        say("")
    say(f"  {p('◆', p.GOLD)} {p('Your world is ready. To go in:', p.BOLD)}")
    arrival = plan.get("tagline", "") if lit is not False else ""
    for line in firsttime.open_steps(name, vanilla, arrival=arrival):
        for r in _wrap(line, width, "    ", "       "):
            say(r)
    say("")
    say(f"    {p('Getting around', p.BOLD)} {p('(first time in Minecraft? this is all you need)', p.DIM)}")
    for line in firsttime.controls_lines():
        say("  " + line)
    if interactive and firsttime.launcher_command(vanilla):
        game = "the Minecraft Launcher" if vanilla else "Omo Space"
        try:
            say("")
            say(f"    {p(f'Open {game} now?', p.BOLD)}")
            yes = ask(f"    {p('›', p.GOLD)} [Y/n] ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            yes = False
        if yes:
            say(f"    {p('✓', p.GREEN)} Opening {game}…" if firsttime.open_game(vanilla)
                else f"    Couldn't open {game}; start it from your apps menu.")
    else:
        say("")
        say(p("    Later: `hermes omo open` shows these steps again and opens the game.", p.DIM))
    return 0


LAST_FILE = "last-world.json"


def _state_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        base = Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return base / "plugin-data" / "omo-space"


def remember_last(world: Path, vanilla: bool) -> None:
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / LAST_FILE).write_text(json.dumps({"path": str(world), "name": world.name, "vanilla": vanilla}))
    except OSError:
        pass


def open_last(out=None, ask: Callable[[str], str] = input, interactive: Optional[bool] = None) -> int:
    """``hermes omo open``: how to get into the last world you made, and (if you say yes) open the game."""
    out = out or sys.stdout
    p = Paint(_tty(out))
    try:
        last = json.loads((_state_dir() / LAST_FILE).read_text())
    except (OSError, ValueError):
        print("No world yet. Make one with: hermes omo", file=out)
        return 1
    name, vanilla = last.get("name", "your world"), bool(last.get("vanilla"))
    print(f"\n  {p('◆', p.GOLD)} {p(name, p.BOLD)}", file=out)
    if not vanilla and not onboarding.omo_data_dir().is_dir():
        for line in firsttime.no_game_text(Path(last.get("path", "")).parent):
            print("    " + line, file=out)
        return 0
    for line in firsttime.open_steps(name, vanilla):
        print("    " + line, file=out)
    print("", file=out)
    for line in firsttime.controls_lines():
        print("  " + line, file=out)
    if interactive is None:
        interactive = onboarding.is_interactive()
    game = "the Minecraft Launcher" if vanilla else "Omo Space"
    if interactive and firsttime.launcher_command(vanilla):
        try:
            yes = ask(f"\n    Open {game} now? [Y/n] ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            yes = False
        if yes:
            print(f"    Opening {game}…" if firsttime.open_game(vanilla) else
                  f"    Couldn't open {game}; start it from your apps menu.", file=out)
    return 0


def tool_audit(root: Optional[Path] = None) -> str:
    """The ``omo_audit_setup`` tool: the read-only audit as JSON. Sends nothing to a model."""
    try:
        from .worldgen import inventory as inv
        data = inv.collect(root)
        a = setup_audit.run(data["profiles"][:MAX_DESKS], Path(data["root"]))
        return json.dumps(setup_audit.as_json(a))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"omo_audit_setup failed: {type(exc).__name__}"})


def tool_create(args: Any, *, llm: Optional[Callable[..., Any]] = None, board_port: Optional[int] = None) -> str:
    """The ``omo_create_world`` tool: the same designer and builder, unattended, JSON back."""
    import io
    try:
        a = args if isinstance(args, dict) else {}
        wish = str(a.get("wish") or "")[:300]
        argv = ["create", "--json", "--for", a.get("game") if a.get("game") in ("auto", "omo", "minecraft") else "auto"]
        if a.get("name"):
            argv += ["--name", str(a["name"])[:48]]
        argv += ["--", *wish.split()]
        parser = argparse.ArgumentParser(prog="hermes omo")
        add_parser(parser.add_subparsers(dest="omo_command"))
        ns = parser.parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        rc = run(ns, board_port=board_port, llm=llm, out=out, err=err, interactive=False)
        if rc != 0:
            return json.dumps({"created": False, "error": (err.getvalue().strip() or f"exit {rc}")[-300:]})
        res = json.loads(out.getvalue())
        plan = res["plan"]
        game = "Minecraft 26.2" if res.get("game") == "minecraft" else "Omo Space"
        return json.dumps({
            "created": True, "world": res["world"], "path": res["path"], "game": game,
            "tagline": plan.get("tagline", ""), "look": [plan["style"], plan["ground"], plan["time"]],
            "rooms": [{"name": r["name"], "about": r.get("about", ""), "why": r.get("why", ""),
                       "desks": len(r["profiles"])}
                      for r in plan["rooms"]],
            "designed_by": res["designed_by"],
            "next": f"Open {game} -> Singleplayer -> {res['world']}; the building rises around the player on arrival.",
        })
    except SystemExit:
        return json.dumps({"created": False, "error": "bad arguments"})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"created": False, "error": f"omo_create_world failed: {type(exc).__name__}"})
