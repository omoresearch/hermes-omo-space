"""Make a brand-new Minecraft 26.2 world folder (creative superflat, cheats on) for `hermes omo create`.

Only writes inside the new world dir it creates under the saves folder; refuses an existing one.
The level.dat / game_rules.dat / world_gen_settings.dat skeletons come from _world_templates.py
(typed NBT of a fresh test world); Minecraft fills in everything else on first load.
"""
import copy
import gzip
import random
import re
import struct
from pathlib import Path

from ._world_templates import TEMPLATES

GROUNDS = {
    "grass": ["minecraft:dirt", "minecraft:grass_block"],
    "snow": ["minecraft:dirt", "minecraft:snow_block"],
    "sand": ["minecraft:sandstone", "minecraft:sand"],
    "stone": ["minecraft:stone", "minecraft:smooth_stone"],
}
BIOMES = {"grass": "minecraft:plains", "snow": "minecraft:snowy_plains", "sand": "minecraft:desert",
          "stone": "minecraft:plains"}
SURFACE_Y = -61            # bedrock -64, 2 fill layers, top layer -61: feet at -60
_BAD_DIR = re.compile(r"[^A-Za-z0-9 _.()'-]")


class WorldError(Exception):
    pass


def folder_name(name):
    """A safe folder name for a world title."""
    n = _BAD_DIR.sub("", name).strip(" .") or "Hermes HQ"
    return n[:48]


# --------------------------------------------------------------------------- typed NBT writer
def _w(out, t, v):
    if t == 1:
        out.append(struct.pack(">b", v))
    elif t == 2:
        out.append(struct.pack(">h", v))
    elif t == 3:
        out.append(struct.pack(">i", v))
    elif t == 4:
        out.append(struct.pack(">q", v))
    elif t == 5:
        out.append(struct.pack(">f", v))
    elif t == 6:
        out.append(struct.pack(">d", v))
    elif t == 8:
        b = v.encode("utf-8")
        out.append(struct.pack(">H", len(b)) + b)
    elif t == 9:
        et, items = v
        out.append(struct.pack(">bi", et if items else 0, len(items)))
        for x in items:
            _w(out, et, x)
    elif t == 10:
        for k, tv in v.items():
            ct, cv = _tv(tv)
            b = k.encode("utf-8")
            out.append(struct.pack(">bH", ct, len(b)) + b)
            _w(out, ct, cv)
        out.append(b"\x00")
    elif t == 11:
        out.append(struct.pack(">i", len(v)) + struct.pack(f">{len(v)}i", *v))
    else:
        raise ValueError(t)


def _tv(node):
    """Template node -> (tag, payload) with lists/compounds unwrapped for _w."""
    if isinstance(node, dict):
        return 10, node
    if node and node[0] == "L":
        et = node[1]
        return 9, (et, [_tv(x)[1] for x in node[2]])
    return node[0], node[1]


def nbt_file(root):
    out = [struct.pack(">bH", 10, 0)]
    _w(out, 10, root)
    return gzip.compress(b"".join(out), mtime=0)


# --------------------------------------------------------------------------- world
def create_world(saves, title, *, ground="grass", time=6000, spawn=(0, -60, -10), seed=None):
    """Create saves/<folder>/ with level.dat, game rules and a flat generator. Returns the path."""
    saves = Path(saves).expanduser()
    if ground not in GROUNDS:
        raise WorldError(f"unknown ground {ground!r}")
    saves.mkdir(parents=True, exist_ok=True)
    path = saves / folder_name(title)
    if path.exists():
        raise WorldError(f"a world folder named {path.name!r} already exists in {saves}")
    t = copy.deepcopy(TEMPLATES)

    lvl = t["level.dat"]["Data"]
    lvl["LevelName"] = [8, title[:64]]
    lvl["Time"] = [4, int(time)]
    lvl["GameType"] = [3, 1]
    lvl["allowCommands"] = [1, 1]
    lvl["spawn"]["pos"] = [11, [int(c) for c in spawn]]
    lvl["spawn"]["yaw"] = [5, 0.0]

    rules = t["data/minecraft/game_rules.dat"]["data"]
    for k in ("minecraft:advance_time", "minecraft:advance_weather", "minecraft:spawn_mobs",
              "minecraft:spawn_monsters", "minecraft:spawn_phantoms", "minecraft:spawn_patrols",
              "minecraft:spawn_wandering_traders", "minecraft:spawn_wardens", "minecraft:mob_griefing"):
        rules[k] = [1, 0]
    rules["minecraft:respawn_radius"] = [3, 0]

    gen = t["data/minecraft/world_gen_settings.dat"]["data"]
    gen["seed"] = [4, seed if seed is not None else random.randint(-2**62, 2**62)]
    ow = gen["dimensions"]["minecraft:overworld"]["generator"]["settings"]
    fill, top = GROUNDS[ground]
    ow["layers"] = ["L", 10, [{"block": [8, "minecraft:bedrock"], "height": [3, 1]},
                              {"block": [8, fill], "height": [3, 2]},
                              {"block": [8, top], "height": [3, 1]}]]
    ow["biome"] = [8, BIOMES[ground]]

    (path / "data" / "minecraft").mkdir(parents=True)
    (path / "datapacks").mkdir()
    for rel, root in t.items():
        (path / rel).write_bytes(nbt_file(root))
    return path
