"""Help for people who have never played Minecraft: what they need, how to get in, how to move.

Everything here is text plus one optional action: opening the Omo Space app (or the Minecraft
launcher) after the user says yes at a terminal. Nothing is downloaded or installed by the plugin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

OMO_INSTALL_URL = "https://github.com/omoresearch/omo-space#install"
MINECRAFT_URL = "https://www.minecraft.net/en-us/store/minecraft-java-bedrock-edition-pc"

# (keys, what they do) shown after a build and on arrival in game
CONTROLS = [
    ("W A S D", "walk"),
    ("mouse", "look around"),
    ("Space", "jump (press twice to fly, you're in creative mode)"),
    ("right-click", "use a desk's computer: talk to that agent"),
    ("Esc", "pause, settings, or let go of the mouse"),
    ("T", "open chat"),
]


def no_game_text(saves: Path) -> list[str]:
    """What to say when neither Omo Space nor Minecraft is installed."""
    return [
        "Your world is saved and waiting. To walk around it you need the game:",
        "",
        "  You need   a Minecraft Java Edition account (one-off purchase, about US$30;",
        f"             {MINECRAFT_URL})",
        "  Then       install Omo Space, free: it brings its own Minecraft, so there's",
        f"             nothing else to set up ({OMO_INSTALL_URL})",
        "  Then       run `hermes omo open`, or open Omo Space and pick your world.",
        "",
        f"  (The world is in {saves}; Omo Space finds it there.)",
    ]


def controls_lines() -> list[str]:
    w = max(len(k) for k, _ in CONTROLS)
    return [f"  {k.ljust(w)}  {v}" for k, v in CONTROLS]


def open_steps(name: str, vanilla: bool) -> list[str]:
    game = "the Minecraft Launcher" if vanilla else "Omo Space"
    first = ("Pick Minecraft: Java Edition, choose version 26.2, press Play" if vanilla
             else "Press Play (sign in with Microsoft the first time)")
    return [
        f"1. Open {game}. {first}.",
        "2. Click Singleplayer.",
        f"3. Double-click \"{name}\".",
        "4. You arrive at the front door and the building rises around you.",
    ]


# --------------------------------------------------------------------------- launching
def _omo_launcher(env=None, platform: Optional[str] = None, home: Optional[Path] = None) -> Optional[list[str]]:
    """A command that opens the Omo Space app, or None if it is not installed."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        app = home / "Applications" / "Omo Space.app"
        for a in (app, Path("/Applications/Omo Space.app")):
            if a.exists():
                return ["open", str(a)]
        return None
    if platform.startswith("win"):
        programs = Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "Microsoft" / "Windows" / \
            "Start Menu" / "Programs"
        lnk = programs / "Omo Space.lnk"
        return ["cmd", "/c", "start", "", str(lnk)] if lnk.exists() else None
    for name in ("omo-space", "omo-research"):
        exe = shutil.which(name) or (str(home / ".local" / "bin" / name)
                                     if (home / ".local" / "bin" / name).exists() else None)
        if exe:
            return [exe]
    return None


def _mc_launcher(platform: Optional[str] = None) -> Optional[list[str]]:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return ["open", "-a", "Minecraft"] if Path("/Applications/Minecraft.app").exists() else None
    if platform.startswith("win"):
        return None                                   # Store app: no stable command; we print the steps
    exe = shutil.which("minecraft-launcher")
    return [exe] if exe else None


def launcher_command(vanilla: bool, **kw) -> Optional[list[str]]:
    return _mc_launcher(kw.get("platform")) if vanilla else _omo_launcher(**kw)


def open_game(vanilla: bool) -> bool:
    """Start the game app detached. True if something was started."""
    cmd = launcher_command(vanilla)
    if not cmd:
        return False
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                             stderr=subprocess.DEVNULL, creationflags=0x00000008)          # DETACHED_PROCESS
        else:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                             stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False
