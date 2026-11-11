"""``hermes omo create``: a few questions in the terminal, then a new Omo Space world with your Hermes
HQ already standing in it.

Feels like ``hermes setup``: numbered steps, Hermes' own arrow-key menus (``hermes_cli.curses_ui``)
when Hermes is importable at a terminal, plain numbered prompts otherwise, and a flag for every
question so it also runs unattended (``--yes`` / ``--json``; no TTY implies the defaults).

It writes exactly two things: a NEW world folder under the saves dir (an existing one is never
touched) and the Hermes HQ datapack inside it. The pack carries a first-join hook, so the hall builds
itself around you the first time you open the world: nothing to type in game.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import onboarding

# key, label, blurb, bay theme order (worldgen.layout.THEMES numbers)
STYLES = [
    ("falls", "Omo Falls", "glowing cavern, prismarine and cherry bays", [4, 2, 1, 5, 6, 8, 3, 7]),
    ("blossom", "Cherry Blossom", "soft pinks, purpur and white quartz", [2, 8, 7, 1, 4, 6, 3, 5]),
    ("library", "Old Library", "dark oak, bookshelves, warm copper", [3, 5, 1, 6, 2, 4, 8, 7]),
    ("studio", "Clean Studio", "quartz, birch and glass, bright and calm", [7, 1, 4, 8, 2, 6, 3, 5]),
    ("forge", "Copper Forge", "deepslate, copper and tuff, moody", [5, 6, 3, 4, 1, 2, 7, 8]),
]
GROUNDS = [("grass", "Meadow", "green grass to the horizon"), ("snow", "Snowfield", "quiet and white"),
           ("sand", "Desert", "warm sand"), ("stone", "Stone plaza", "smooth stone, very tidy")]
TIMES = [("morning", "Morning", 1000), ("noon", "Noon", 6000), ("sunset", "Golden hour", 12000),
         ("night", "Night", 18000)]
ORIGIN = (0, -60, 0)            # build origin on the flat ground (feet y -60)
SPAWN = (0, -60, -10)           # 10 blocks in front of the arch, facing it
MAX_DESKS = 96
NS = onboarding.DEFAULT_NAMESPACE


# --------------------------------------------------------------------------- colour
def _use_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


class Paint:
    GOLD, DIM, BOLD, GREEN, CYAN, RED, RESET = "\033[38;5;179m", "\033[2m", "\033[1m", "\033[32m", "\033[36m", \
        "\033[31m", "\033[0m"

    def __init__(self, on: bool):
        self.on = on

    def __call__(self, text: str, *codes: str) -> str:
        return "".join(codes) + text + self.RESET if self.on and codes else text


# --------------------------------------------------------------------------- argparse
def add_parser(subs: Any) -> argparse.ArgumentParser:
    p = subs.add_parser(
        "create", help="Create a new Omo Space world with your Hermes HQ built in (guided)",
        description="Answer a few questions; get a new Omo Space world where every Hermes profile is a desk "
                    "in a building. Open the world and walk in: it builds itself around you.")
    p.add_argument("--name", help="world name (default: Hermes HQ)")
    p.add_argument("--style", choices=[s[0] for s in STYLES], help="building style")
    p.add_argument("--ground", choices=[g[0] for g in GROUNDS], help="the land around it")
    p.add_argument("--time", choices=[t[0] for t in TIMES], help="time of day (it stays there)")
    p.add_argument("--profiles", help="only these profiles (comma separated; default: all)")
    p.add_argument("--exclude", help="leave these profiles out (comma separated)")
    p.add_argument("--saves-dir", help="Minecraft saves dir (default: Omo Space's)")
    p.add_argument("--bridge-port", type=int, default=None, help="Omo bridge port for the Board wall")
    p.add_argument("--hermes-root", default=None, help="Hermes root (default $HERMES_ROOT or ~/.hermes)")
    p.add_argument("-y", "--yes", action="store_true", help="ask nothing: use the flags and defaults")
    p.add_argument("--json", action="store_true", help="machine-readable result (implies --yes)")
    return p


# --------------------------------------------------------------------------- prompts
class Prompter:
    """Hermes' arrow-key menus when available at a terminal, numbered input otherwise."""

    def __init__(self, out, paint: Paint, ask: Callable[[str], str] = input, use_curses: bool = False):
        self.out, self.p, self.ask = out, paint, ask
        self._radio = self._check = None
        if use_curses:
            try:
                from hermes_cli.curses_ui import curses_checklist, curses_radiolist  # type: ignore
                self._radio, self._check = curses_radiolist, curses_checklist
            except Exception:  # noqa: BLE001
                pass

    def _echo(self, label: str) -> None:
        print(f"    {self.p('›', self.p.GOLD)} {label}", file=self.out)

    def choose(self, title: str, items: list[tuple[str, str]], default: int = 0) -> int:
        """items: (label, blurb)."""
        if self._radio is not None:
            rows: list[Any] = [[(f"{a}", None), (f"  {b}", "dim")] if b else a for a, b in items]
            i = self._radio(title, rows, selected=default, cancel_returns=-1)
            if i is None or i < 0:
                raise KeyboardInterrupt
            self._echo(items[i][0])
            return i
        w = max(len(a) for a, _ in items)
        for n, (a, b) in enumerate(items, 1):
            mark = self.p(" (default)", self.p.DIM) if n - 1 == default else ""
            print(f"    {n}. {a.ljust(w)}  {self.p(b, self.p.DIM)}{mark}", file=self.out)
        while True:
            raw = self.ask(f"    Choose 1-{len(items)} [{default + 1}]: ").strip()
            if not raw:
                self._echo(items[default][0])
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(items):
                self._echo(items[int(raw) - 1][0])
                return int(raw) - 1
            print(self.p("    Type one of the numbers above.", self.p.DIM), file=self.out)

    def pick_many(self, title: str, items: list[str], selected: set[int]) -> set[int]:
        if self._check is not None:
            return set(self._check(title, items, set(selected), cancel_returns=set(selected)))
        for n, it in enumerate(items, 1):
            print(f"    {n}. {it}", file=self.out)
        raw = self.ask("    Enter keeps all; or type the numbers to keep (e.g. 1,3): ").strip()
        if not raw:
            return set(selected)
        keep = {int(x) - 1 for x in raw.replace(" ", ",").split(",") if x.isdigit() and 0 < int(x) <= len(items)}
        return keep or set(selected)

    def text(self, question: str, default: str) -> str:
        raw = self.ask(f"    {question} [{default}]: ").strip()
        val = raw or default
        self._echo(val)
        return val

    def confirm(self, question: str) -> bool:
        raw = self.ask(f"\n    {question} [Y/n]: ").strip().lower()
        return raw in ("", "y", "yes")


# --------------------------------------------------------------------------- helpers
def _unique_name(saves: Path, base: str, newworld: Any) -> str:
    name, n = base, 2
    while (saves / newworld.folder_name(name)).exists():
        name, n = f"{base} {n}", n + 1
    return name


def neighbourhoods(names: list[str]) -> list[tuple[str, str, list[int]]]:
    """[(key, label, desk indices)] the way the hall groups them into bays."""
    from .worldgen import layout as lay
    return lay._groups(names)


def _hood_line(names: list[str]) -> str:
    return " · ".join(f"{label} {len(idx)}" for _, label, idx in neighbourhoods(names))


def _bay_line(bays: list[dict]) -> str:
    """'omo-* 5 bays, purpur · lf-* cherry': one entry per neighbourhood, in hall order."""
    seen: dict[str, list] = {}
    for b in bays:
        seen.setdefault(b["label"], [0, b["theme_name"]])[0] += 1
    return " · ".join(f"{k} ({t}{f', {n} bays' if n > 1 else ''})" for k, (n, t) in seen.items())


def _icon_png(style_i: int) -> bytes:
    """A 64x64 world icon: the style's two bay colours as a soft diagonal with a gold H mark."""
    import struct
    import zlib
    pal = {1: (226, 196, 120), 2: (233, 160, 190), 3: (140, 96, 60), 4: (70, 140, 140), 5: (190, 110, 70),
           6: (120, 150, 90), 7: (220, 225, 230), 8: (170, 120, 190)}
    a, b = pal[STYLES[style_i][3][0]], pal[STYLES[style_i][3][1]]
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


# --------------------------------------------------------------------------- the command
def run(args: Any, *, board_port: Optional[int] = None, out=None, err=None,
        ask: Callable[[str], str] = input, interactive: Optional[bool] = None,
        use_curses: Optional[bool] = None) -> int:
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
    interactive = interactive and not (args.yes or quiet)
    p = Paint(_use_color(out) and not quiet)
    pr = Prompter(out, p, ask=ask, use_curses=interactive if use_curses is None else use_curses)

    def say(line: str = "") -> None:
        if not quiet:
            print(line, file=out)

    saves = Path(args.saves_dir).expanduser() if args.saves_dir else onboarding.saves_dir()
    root = Path(args.hermes_root).expanduser() if args.hermes_root else inv.hermes_root()
    data = inv.collect(root, only=args.profiles, exclude=args.exclude)
    profiles = data["profiles"]
    if not profiles:
        print(f"error: no Hermes profiles found under {root}"
              + (" matching --profiles/--exclude" if args.profiles or args.exclude else ""), file=err)
        return 1
    for m in data.get("missing") or []:
        print(f"warning: no profile named {m!r}", file=err)

    steps = [s for s, ask_it in (("name", not args.name), ("style", not args.style), ("ground", not args.ground),
                                 ("time", not args.time),
                                 ("desks", not args.profiles and len(profiles) > 1)) if ask_it]
    total = len(steps)

    def header(key: str, title: str) -> None:
        n = steps.index(key) + 1
        say()
        say(f"  {p(f'{n}/{total}', p.DIM)}  {p(title, p.BOLD)}")

    try:
        names = [x["name"] for x in profiles]
        say()
        say(f"  {p('◆', p.GOLD)} {p('Omo Space', p.BOLD)} {p('· build your Hermes HQ', p.DIM)}")
        say(f"    {len(profiles)} Hermes profile{'s' if len(profiles) != 1 else ''} found  "
            f"{p(_hood_line(names), p.DIM)}")
        say("    Each one becomes a desk in a building you can walk around.")
        if interactive and total:
            say(p(f"    {total} quick question{'s' if total != 1 else ''}. Enter keeps the default; Ctrl+C stops "
                  "without writing anything.", p.DIM))

        name = args.name or _unique_name(saves, "Hermes HQ", newworld)
        if interactive and "name" in steps:
            header("name", "Name your world")
            name = pr.text("World name", name)

        style_i = next((i for i, s in enumerate(STYLES) if s[0] == args.style), 0)
        if interactive and "style" in steps:
            header("style", "Pick a building style")
            style_i = pr.choose("Building style", [(s[1], s[2]) for s in STYLES], style_i)

        ground_i = next((i for i, g in enumerate(GROUNDS) if g[0] == args.ground), 0)
        if interactive and "ground" in steps:
            header("ground", "The land around it")
            ground_i = pr.choose("The land around it", [(g[1], g[2]) for g in GROUNDS], ground_i)

        time_i = next((i for i, t in enumerate(TIMES) if t[0] == args.time), 1)
        if interactive and "time" in steps:
            header("time", "Time of day")
            time_i = pr.choose("Time of day (it stays there)", [(t[1], "") for t in TIMES], time_i)

        if interactive and "desks" in steps:
            header("desks", "Who moves in")
            hoods = neighbourhoods(names)
            items = [f"{label}  ({len(idx)}: {', '.join(names[i] for i in idx[:3])}{'…' if len(idx) > 3 else ''})"
                     for _, label, idx in hoods]
            keep = pr.pick_many("Neighbourhoods that get desks (SPACE toggles, ENTER confirms)", items,
                                set(range(len(hoods))))
            kept_idx = sorted(i for h, (_, _, idx) in enumerate(hoods) if h in keep for i in idx)
            if kept_idx:
                profiles = [profiles[i] for i in kept_idx]
            say(f"    {p('›', p.GOLD)} {len(profiles)} desk{'s' if len(profiles) != 1 else ''}")

        if len(profiles) > MAX_DESKS:
            say(p(f"    The hall holds {MAX_DESKS} desks; the first {MAX_DESKS} profiles move in.", p.DIM))
            profiles = profiles[:MAX_DESKS]
        names = [x["name"] for x in profiles]
        style, ground, tod = STYLES[style_i], GROUNDS[ground_i], TIMES[time_i]

        from .worldgen import layout as lay
        L = lay.plan_layout(len(profiles), names, theme_order=style[3])
        w, h, d = L["size"]
        say()
        say(f"  {p('Your world', p.BOLD)}")
        rows = [("Name", name), ("Style", f"{style[1]} on {ground[1].lower()}, {tod[1].lower()}"),
                ("Building", f"{len(profiles)} desks in {len(L['bays'])} bays, {w} × {d} blocks"),
                ("Bays", _bay_line(L["bays"])),
                ("Saved to", str(saves / newworld.folder_name(name)))]
        for k, v in rows:
            say(f"    {p(k.ljust(9), p.DIM)} {v}")
        if interactive and not pr.confirm("Create it?"):
            say("    Nothing written.")
            return 0
    except (KeyboardInterrupt, EOFError):
        print("\n    Stopped; nothing written.", file=out)
        return 130

    port = args.bridge_port or board_port or 8780
    t0 = time.time()
    say()
    try:
        files = dp.build_files(dict(data, profiles=profiles), namespace=NS, bridge_port=port, max_desks=MAX_DESKS,
                               theme_order=style[3],
                               arrival={"origin": list(ORIGIN), "title": name, "time": tod[2]})
    except ValueError as exc:
        print(f"error: {exc}", file=err)
        return 2
    say(f"    {p('✓', p.GREEN)} Laid out {len(profiles)} desks and the Hermes Board")
    try:
        world = newworld.create_world(saves, name, ground=ground[0], time=tod[2], spawn=SPAWN)
    except (newworld.WorldError, OSError) as exc:
        print(f"error: {exc}", file=err)
        return 1
    try:
        (world / "icon.png").write_bytes(_icon_png(style_i))
        dp.write_output(files, world / "datapacks" / NS)
    except (dp.OutputError, OSError) as exc:
        print(f"error: could not finish the world at {world}: {exc}", file=err)
        return 1
    say(f"    {p('✓', p.GREEN)} Made the world \"{name}\" ({ground[1].lower()}, cheats on, creative)")
    say(f"    {p('✓', p.GREEN)} Packed Hermes HQ into it ({len(files)} files, {time.time() - t0:.1f}s)")

    result = {"world": world.name, "path": str(world), "name": name, "style": style[0], "ground": ground[0],
              "time": tod[0], "desks": len(profiles), "bays": len(L["bays"]), "size": L["size"],
              "profiles": names, "board_url": f"http://127.0.0.1:{port}/board"}
    if quiet:
        print(json.dumps(result, indent=1), file=out)
        return 0
    say()
    say(f"  {p('◆', p.GOLD)} {p('Ready. Go and see it:', p.BOLD)}")
    say(f"    1. Open Omo Space → Singleplayer → {p(name, p.GOLD)}")
    say("    2. You arrive at the arch and the hall rises around you (a few seconds).")
    say("    3. Walk in. Right-click a desk's Omo Computer once to open that profile.")
    say()
    say(p(f"    In game: /function {NS}:info lists every desk · Board wall: {result['board_url']}", p.DIM))
    return 0
