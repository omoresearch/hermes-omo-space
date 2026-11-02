"""Hermes HQ v2: the block model of the cavern hall, computed from layout.py.

Hall(layout, profiles).cells is {(x, y, z): block state} relative to the build
origin, every cell the build sets with fill/setblock (templates excluded: they
are in .templates as (name, pos, rotation, kit cells)). .phase says when a cell
is placed: 'solid' (bottom-up, includes the carved air), then 'attach'
(plants, posts, lights: bottom-up), 'hang' (chains, lanterns, vines: top-down),
'water', 'aqua' (lily pads, seagrass) and 'sign' (block entities with text).

The look is the Omo Falls house style: a self-contained glowing cavern (its own
rock shell, so it stands on any flat-ish spot or underground), a level walkway
loop, a pond with a waterfall either side of the Board crag, a glow tree, moss
and amethyst bands, glow berries, spore blossoms, lanterns on posts, gold
chandeliers, glowing signposts and hidden light blocks; every bay is a zone with
its own material theme (the mountain studio's MAT / wall_fn / zone_floor tables)
and Hermes gold is the accent (facade, foyer, Board frame, chandeliers, caps).
"""
import math
from collections import deque

from . import layout as L

NB4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
NB6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
DIR = {(0, 1): "south", (0, -1): "north", (1, 0): "east", (-1, 0): "west"}


def h2(x, z, seed=0):
    n = (x * 374761393 + z * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def h3(x, y, z, seed=0):
    return h2(x * 31 + y * 7919, z, seed)


def vnoise(x, z, scale, seed=0):
    gx, gz = x / scale, z / scale
    x0, z0 = math.floor(gx), math.floor(gz)
    fx, fz = gx - x0, gz - z0
    fx, fz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)
    a, b = h2(x0, z0, seed), h2(x0 + 1, z0, seed)
    c, d = h2(x0, z0 + 1, seed), h2(x0 + 1, z0 + 1, seed)
    return (a * (1 - fx) + b * fx) * (1 - fz) + (c * (1 - fx) + d * fx) * fz


def name(s):
    return s.split("[")[0].split("{")[0].split(":")[-1]


def mc(s):
    return s if ":" in s.split("[")[0] else "minecraft:" + s


# ----------------------------------------------------------------------------- palettes
# bay themes: copied from the mountain studio (shorts_mountain2/gen_studio2.py MAT, wall_fn, zone_floor)
MAT = {
    1: dict(back="birch_planks", post="stripped_birch_log[axis=y]", beam="stripped_birch_log[axis=x]", wood="birch",
            desk="birch_slab[type=top,waterlogged=false]", chair="birch", plants=("potted_dandelion", "potted_flowering_azalea_bush"),
            carpet="yellow", sign="birch"),
    2: dict(back="cherry_planks", post="stripped_cherry_log[axis=y]", beam="stripped_cherry_log[axis=x]", wood="cherry",
            desk="cherry_slab[type=top,waterlogged=false]", chair="cherry", plants=("potted_pink_tulip", "potted_cherry_sapling"),
            carpet="pink", sign="cherry"),
    3: dict(back="dark_oak_planks", post="stripped_dark_oak_log[axis=y]", beam="stripped_dark_oak_log[axis=x]", wood="dark_oak",
            desk="dark_oak_slab[type=top,waterlogged=false]", chair="dark_oak", plants=("potted_flowering_azalea_bush", "potted_azalea_bush"),
            carpet="orange", sign="dark_oak"),
    4: dict(back="dark_prismarine", post="prismarine_bricks", beam="prismarine_bricks", wood="spruce",
            desk="spruce_slab[type=top,waterlogged=false]", chair="spruce", plants=("potted_fern", "potted_blue_orchid"),
            carpet="cyan", sign="spruce"),
    5: dict(back="polished_deepslate", post="waxed_cut_copper", beam="waxed_cut_copper", wood="dark_oak",
            desk="polished_deepslate_slab[type=top,waterlogged=false]", chair="dark_oak", plants=("potted_red_tulip", "potted_fern"),
            carpet="red", sign="dark_oak"),
    6: dict(back="tuff_bricks", post="polished_tuff", beam="chiseled_tuff", wood="mangrove",
            desk="tuff_brick_slab[type=top,waterlogged=false]", chair="mangrove", plants=("potted_bamboo", "potted_cactus"),
            carpet="lime", sign="mangrove"),
    7: dict(back="quartz_bricks", post="quartz_pillar[axis=y]", beam="quartz_pillar[axis=x]", wood="birch",
            desk="smooth_quartz_slab[type=top,waterlogged=false]", chair="birch", plants=("potted_blue_orchid", "potted_fern"),
            carpet="light_blue", sign="birch"),
    8: dict(back="calcite", post="purpur_pillar[axis=y]", beam="purpur_pillar[axis=x]", wood="birch",
            desk="quartz_slab[type=top,waterlogged=false]", chair="purpur", plants=("potted_allium", "potted_azure_bluet"),
            carpet="magenta", sign="birch"),
}
GOLD = "gold_block"
HERMES_WALL = "polished_blackstone_bricks"


def wall_fn(st, x, y, z):
    """Bay wall block for theme st at y 0..8 (light band y 7, top band y 8)."""
    F = -1
    if st == 1:
        if y == F + 9:
            return "birch_planks"
        if y == F + 8:
            return "ochre_froglight[axis=y]" if (x + z) % 3 == 0 else "birch_planks"
        return "stripped_birch_log[axis=y]" if (x + z) % 4 == 0 else "birch_planks"
    if st == 2:
        if y == F + 9:
            return "cherry_planks"
        if y == F + 8:
            return "pearlescent_froglight[axis=y]" if (x + z) % 3 == 0 else "cherry_planks"
        return "stripped_cherry_log[axis=y]" if (x + z) % 4 == 0 else "cherry_planks"
    if st == 3:
        if y in (F + 1, F + 2, F + 9):
            return "dark_oak_planks"
        if y == F + 8:
            return "stripped_dark_oak_log[axis=y]" if (x + z) % 4 == 0 else "dark_oak_planks"
        return "stripped_dark_oak_log[axis=y]" if (x + z) % 6 == 0 else "bookshelf"
    if st == 4:
        if y == F + 9:
            return "dark_prismarine"
        if y == F + 8:
            return "sea_lantern" if (x + z) % 3 == 0 else "prismarine_bricks"
        return "dark_prismarine" if y <= F + 2 else ("stripped_spruce_log[axis=y]" if (x + z) % 4 == 0 else "spruce_planks")
    if st == 5:
        if y == F + 9:
            return "waxed_cut_copper"
        if y == F + 8:
            return "waxed_copper_bulb[lit=true,powered=false]" if (x + z) % 4 == 0 else "polished_deepslate"
        return "deepslate_tiles" if y <= F + 2 else ("polished_deepslate" if (x + z) % 5 else "deepslate_bricks")
    if st == 6:
        if y == F + 9:
            return "polished_tuff"
        if y == F + 8:
            return "verdant_froglight[axis=y]" if (x + z) % 3 == 0 else "chiseled_tuff"
        return "polished_tuff" if y <= F + 2 else ("tuff_bricks" if (x + z) % 5 else "chiseled_tuff_bricks")
    if st == 7:
        if y == F + 9:
            return "quartz_bricks"
        if y == F + 8:
            return "sea_lantern" if (x + z) % 3 == 0 else "light_blue_stained_glass"
        return "smooth_quartz" if y <= F + 2 else "quartz_bricks"
    if st == 8:
        if y == F + 9:
            return "purpur_block"
        if y == F + 8:
            return "pearlescent_froglight[axis=y]" if (x + z) % 4 == 0 else "calcite"
        if y <= F + 2:
            return "purpur_block"
        return "amethyst_block" if (x + z) % 7 == 0 else "calcite"
    raise ValueError(st)


def zone_floor(st, u, v, w, d):
    """Bay floor for theme st at bay-local (u, v) in a w x d bay."""
    edge = u in (0, w - 1) or v in (0, d - 1)
    if st == 1:
        if edge:
            return "yellow_terracotta"
        return "ochre_froglight[axis=y]" if u % 4 == 2 and v % 4 == 2 else "birch_planks"
    if st == 2:
        if edge:
            return "pink_terracotta"
        return "stripped_cherry_wood[axis=y]" if (u + v) % 2 == 0 else "cherry_planks"
    if st == 3:
        return "stripped_dark_oak_wood[axis=y]" if edge else "dark_oak_planks"
    if st == 4:
        if edge:
            return "dark_prismarine"
        return "prismarine_bricks" if u % 5 == 0 or v % 5 == 0 else "spruce_planks"
    if st == 5:
        if edge:
            return "black_concrete" if (u + v) % 2 else "white_concrete"
        if u % 4 == 2 and v % 4 == 2:
            return "waxed_copper_grate[waterlogged=false]"
        return "polished_deepslate" if (u + v) % 2 else "deepslate_tiles"
    if st == 6:
        if edge:
            return "lime_terracotta"
        return "chiseled_tuff" if u % 4 == 2 and v % 4 == 2 else ("polished_tuff" if (u + v) % 2 else "tuff_bricks")
    if st == 7:
        line = v % 6 == 0 or u == 0
        return "light_blue_concrete" if line else ("white_concrete" if (u + v) % 2 else "smooth_quartz")
    if st == 8:
        if edge:
            return "purple_concrete"
        return "polished_diorite" if u % 6 == 0 or v % 5 == 0 else "white_terracotta"
    raise ValueError(st)


def theme_ceiling(st, u, v):
    m = MAT[st]
    beam = m["beam"].replace("[axis=x]", "[axis=z]") if "axis" in m["beam"] else m["beam"]
    return beam if u % 4 == 0 else m["back"]


ROCK = [(0.30, "deepslate"), (0.52, "tuff"), (0.70, "stone"), (0.82, "andesite"), (0.92, "cobbled_deepslate"),
        (1.01, "smooth_basalt")]
FLOORMIX = [(0.62, "moss_block"), (0.72, "mossy_cobblestone"), (0.84, "moss_block"), (0.92, "rooted_dirt"),
            (1.01, "moss_block")]
PLANTMIX = [(0.12, "moss_carpet"), (0.22, "short_grass"), (0.28, "fern"), (0.31, "azalea"), (0.345, "flowering_azalea"),
            (0.36, "firefly_bush"), (0.39, "pink_petals[facing=north,flower_amount=3]"),
            (0.41, "pink_petals[facing=east,flower_amount=4]"), (0.425, "blue_orchid"), (0.44, "lily_of_the_valley")]
TOPMIX = [(0.16, "moss_carpet"), (0.27, "short_grass"), (0.31, "fern"), (0.335, "azalea"), (0.36, "flowering_azalea"),
          (0.37, "firefly_bush")]
FONT = {
    "H": ["#.#", "#.#", "###", "#.#", "#.#"], "E": ["###", "#..", "##.", "#..", "###"],
    "R": ["##.", "#.#", "##.", "#.#", "#.#"], "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#"],
    "S": [".##", "#..", ".#.", "..#", "##."],
}


def rock(x, y, z):
    n = 0.65 * vnoise(x + y * 3, z - y * 2, 4, 11) + 0.35 * h3(x, y, z, 12)
    for t, b in ROCK:
        if n < t:
            return b
    return "stone"


def moss_floor(x, z):
    n = 0.6 * vnoise(x, z, 3, 21) + 0.4 * h2(x, z, 22)
    for t, b in FLOORMIX:
        if n < t:
            return b
    return "moss_block"


def kit_cells(st, sign=None, preset=None):
    """The 7 x 5 x 2 desk kit (drawn facing south): {(x, y, z): (spec, nbt)}.
    Frame row z 0: themed back + posts with gold caps, the 5 x 3 terminal wall, the Omo Computer UNDER the
    screen (touching only the bottom-middle panel); desk row z 1: slabs, name plate + status lamp, the cell in
    front of the computer left open, a glowing sign on the top beam. Monitors carry nbt {'wall': (i, j, w, h)}."""
    m = MAT[st]
    c = {}
    for x in range(L.KIT_W):
        for y in range(L.KIT_H):
            c[(x, y, 0)] = (m["back"], None)
    for y in range(L.KIT_H):
        c[(0, y, 0)] = (m["post"], None)
        c[(L.KIT_W - 1, y, 0)] = (m["post"], None)
    c[(0, L.KIT_H - 1, 0)] = (GOLD, None)
    c[(L.KIT_W - 1, L.KIT_H - 1, 0)] = (GOLD, None)
    for x in range(1, L.KIT_W - 1):
        c[(x, L.KIT_H - 1, 0)] = (m["beam"], None)
        c[(x, 0, 0)] = (m["beam"], None)
    for i in range(L.SCREEN_W):
        for j in range(L.SCREEN_H):
            c[(1 + i, 1 + j, 0)] = ("omolife:monitor", {"wall": (i, j, L.SCREEN_W, L.SCREEN_H)})
    c[(L.KIT_MID, 0, 0)] = ("omolife:hermes_computer[facing=south]", {"station": preset})
    for x in range(1, L.KIT_W - 1):
        if x != L.KIT_MID:
            c[(x, 0, 1)] = (m["desk"], None)
    c[L.PLATE_T] = ("omolife:name_plate[face=floor,facing=south]", None)
    c[L.LAMP_T] = ("omolife:status_lamp", None)
    c[L.SIGN_T] = (f"{m['sign']}_wall_sign[facing=south,waterlogged=false]", {"sign": sign})
    return c


def board_cells(url):
    """11 x 7 x 1 Board frame in Hermes black + gold with the 9 x 5 web wall."""
    c = {}
    for x in range(L.BOARD_W):
        for y in range(L.BOARD_H):
            edge = x in (0, L.BOARD_W - 1) or y in (0, L.BOARD_H - 1)
            corner = x in (0, L.BOARD_W - 1) and y in (0, L.BOARD_H - 1)
            c[(x, y, 0)] = (GOLD if corner else ("gilded_blackstone" if edge and (x + y) % 4 == 0 else
                                                  "polished_blackstone_bricks"), None)
    for i in range(L.BOARD_MON_W):
        for j in range(L.BOARD_MON_H):
            c[(1 + i, 1 + j, 0)] = ("omolife:monitor", {"wall": (i, j, L.BOARD_MON_W, L.BOARD_MON_H), "url": url})
    return c


ATTACH = ("lantern", "moss_carpet", "short_grass", "fern", "azalea", "flowering_azalea", "firefly_bush", "pink_petals",
          "amethyst_cluster", "glow_lichen", "light", "potted_", "_carpet", "_sign", "brown_mushroom", "red_mushroom")
HANG = ("iron_chain", "cave_vines", "spore_blossom")


class Hall:
    def __init__(self, lay, profiles=None, url="http://127.0.0.1:8780/board"):
        self.lay = lay
        self.profiles = profiles or [{"name": d["name"]} for d in lay["desks"]]
        self.url = url
        self.cells, self.phase = {}, {}
        self.signs = {}          # cell -> (color, [4 lines])  (standing signs set by command)
        self.templates = []      # (key, [X, Y, Z], rotation, cells)
        self.tplbox = {}
        self.wet = set()
        self.reserved = set()
        self._plan()
        self._shell()
        self._templates()
        self._decor()

    # ------------------------------------------------------------------ helpers
    def put(self, x, y, z, s, ph="solid"):
        self.cells[(x, y, z)] = mc(s)
        self.phase[(x, y, z)] = ph

    def get(self, c):
        return self.cells.get(c)

    def is_air(self, c):
        return self.cells.get(c) == "minecraft:air"

    def free(self, c):
        return self.is_air(c) and c not in self.tplbox and c not in self.reserved and c not in self.wet

    def deco(self, x, y, z, s, ph=None):
        if ph is None:
            n = name(mc(s))
            ph = "hang" if n.startswith(HANG) else "attach" if any(a in n for a in ATTACH) else "solid"
        self.put(x, y, z, s, ph)

    def tryput(self, x, y, z, s, ph=None):
        if self.free((x, y, z)):
            self.deco(x, y, z, s, ph)
            return True
        return False

    # ------------------------------------------------------------------ geometry
    def _plan(self):
        lay = self.lay
        R = lay["regions"]
        hw, vault = lay["half_width"], lay["vault"]
        self.top = {}            # (x, z) -> highest air y
        self.region = {}         # (x, z) -> 'foyer' | 'walk' | 'garden' | 'bay' | 'arch' | 'bridge'
        self.bayof = {}          # (x, z) -> (bay, u, v)

        def vault_top(x, z):
            t = 9 + round(vault * math.sqrt(max(0.0, 1 - (x / (hw + 2.0)) ** 2)))
            return max(9, t + round(1.8 * (vnoise(x, z, 4, 3) - 0.5)))

        for x in range(L.ARCH["x0"], L.ARCH["x1"] + 1):
            for z in (1, 2):
                self.top[(x, z)] = 4 if abs(x) <= 1 else 3
                self.region[(x, z)] = "arch"
        f = R["foyer"]
        for x in range(f[0], f[1] + 1):
            for z in range(f[2], f[3] + 1):
                self.top[(x, z)] = 6
                self.region[(x, z)] = "foyer"
        for key in ("front_walk", "left_walk", "right_walk", "back_walk", "garden"):
            r = R[key]
            for x in range(r[0], r[1] + 1):
                for z in range(r[2], r[3] + 1):
                    self.top[(x, z)] = vault_top(x, z)
                    self.region[(x, z)] = "garden" if key == "garden" else "walk"
        if "bridge" in R:
            r = R["bridge"]
            for x in range(r[0], r[1] + 1):
                for z in range(r[2], r[3] + 1):
                    self.region[(x, z)] = "bridge"
        # the Board crag: solid rock from floor to vault, flat north face for the Board
        cg = lay["crag"]
        self.crag = set()
        for x in range(cg["x0"], cg["x1"] + 1):
            for z in range(cg["z0"], cg["z1"] + 1):
                dz = z - cg["z0"]
                if dz >= 5 and abs(x) > cg["x1"] - 2 - (1 if h2(x, z, 4) < 0.5 else 0):
                    continue
                self.crag.add((x, z))
                self.top.pop((x, z), None)
                self.region.pop((x, z), None)
        for b in lay["bays"]:
            o, U, V = b["origin"], b["U"], b["V"]
            for u in range(L.BAY_W):
                for v in range(b["depth"]):
                    c = (o[0] + u * U[0] + v * V[0], o[1] + u * U[1] + v * V[1])
                    self.top[c] = L.BAY_TOP
                    self.region[c] = "bay"
                    self.bayof[c] = (b, u, v)
        # water: pond A round the crag and behind it, a stream and pond B in long gardens
        g = R["garden"]
        gx = g[1]
        s1 = lay["s1"]
        za0 = cg["z1"] + 1
        za1 = za0 + max(6, min(9, lay["garden_l"] // 4))
        self.pond = set()
        cz, rz = (za0 + za1) / 2.0, (za1 - za0) / 2.0 + 1.0
        for x in range(-gx, gx + 1):
            for z in range(g[2], za1 + 2):
                if (x, z) in self.crag or self.region.get((x, z)) != "garden":
                    continue
                n = 0.5 * (vnoise(x, z, 3, 41) - 0.5)
                inside = ((x / (gx - 0.6)) ** 2 + ((z - cz) / rz) ** 2 <= 1 + n)
                strip = cg["z0"] < z <= cg["z1"] and abs(x) > cg["x1"] and abs(x) <= gx - 2 + (1 if n > 0 else 0)
                if inside or strip:
                    self.pond.add((x, z))
        for fx, fz in cg["falls"]:
            for dz in (-1, 0, 1):
                self.pond.add((fx, fz + dz))
                self.pond.add((fx + (1 if fx > 0 else -1), fz + dz))
        self.stream = set()
        self.pond_b = set()
        rest = s1 - za1
        self.tree_at = None
        self.tree2_at = None
        if rest >= 18:
            zb0, zb1 = s1 - 7, s1
            cbz, rbz = (zb0 + zb1) / 2.0, (zb1 - zb0) / 2.0 + 0.5
            for x in range(-gx, gx + 1):
                for z in range(zb0 - 1, zb1 + 2):
                    if self.region.get((x, z)) != "garden":
                        continue
                    if ((x / (gx - 1.5)) ** 2 + ((z - cbz) / rbz) ** 2) <= 1 + 0.4 * (vnoise(x, z, 3, 42) - 0.5):
                        self.pond_b.add((x, z))
            for z in range(za1 - 1, zb0 + 2):
                sx = 3 + round(1.5 * math.sin(z / 3.1))
                for x in (sx, sx + 1):
                    if self.region.get((x, z)) in ("garden", "bridge"):
                        self.stream.add((x, z))
            self.tree_at = (-gx // 2 - 1, za1 + 2 + (zb0 - za1) // 4)
            self.tree2_at = (gx // 2 + 2, zb0 - 4 - (zb0 - za1) // 4) if zb0 - za1 >= 26 else None
        else:
            self.tree_at = (0, min(za1 + 5, s1 - 4))
        self.water = (self.pond | self.pond_b | self.stream) - self.crag
        for c in self.water:
            assert self.region.get(c) in ("garden", "bridge"), ("water outside the garden", c)

    def _mound(self):
        """Top of the rock shell per column: vault + 2 over the hall, sloping down outside like a hill."""
        h = self.lay["hall"]
        dist, src = {}, {}
        dq = deque()
        for c in self.top:
            dist[c] = 0
            src[c] = self.top[c]
            dq.append(c)
        for c in self.crag:
            if c not in dist:
                dist[c] = 0
                src[c] = 9 + self.lay["vault"]
                dq.append(c)
        while dq:
            c = dq.popleft()
            if dist[c] >= 9:
                continue
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    n = (c[0] + a, c[1] + b)
                    if n[1] < 1 or not (h["x0"] <= n[0] <= h["x1"] and n[1] <= h["z1"]):
                        continue
                    if n not in dist:
                        dist[n] = dist[c] + 1
                        src[n] = src[c]
                        dq.append(n)
                    elif dist[n] == dist[c] + 1 and src[c] > src[n]:
                        src[n] = src[c]
        mt = {}
        for c, d in dist.items():
            if c in self.top:
                t = self.top[c] + 2
                # the open region's own roof: at least as high as its neighbours' tops (smooth outside)
                t = max(t, max(self.top.get((c[0] + a, c[1] + b), 0) + 2 for a in (-1, 0, 1) for b in (-1, 0, 1)))
            else:
                t = src[c] + 2 - max(0, d - 2) * 3 + round(2.4 * (vnoise(c[0], c[1], 5, 7) - 0.5))
                if c in self.crag:
                    t = src[c] + 2
            # facade: a tall front cliff over the arch for the HERMES letters
            fc = self.lay["facade"]
            if c[1] <= 7 and abs(c[0]) <= fc["half"]:
                flat = (fc["letters_x1"] - fc["letters_x0"]) // 2 + 4
                prof = fc["top"] - max(0, abs(c[0]) - flat)
                t = max(t, prof, min(t + 1, self.top.get(c, -9) + 2) if c in self.top else t)
            if t >= 0:
                mt[c] = min(t, h["y1"])
        return mt

    def _shell(self):
        lay = self.lay
        mt = self._mound()
        self.mt = mt
        water = self.water
        bed = {}
        for (x, z) in water:
            deep = (x, z) not in self.stream and all((x + a, z + b) in water for a in (-1, 0, 1) for b in (-1, 0, 1))
            bed[(x, z)] = -4 if deep else -3
        self.bed = bed
        near_water = {(x + a, z + b) for (x, z) in water for a in (-1, 0, 1) for b in (-1, 0, 1)}
        opencells = set()
        for (x, z), t in self.top.items():
            for y in range(0, t + 1):
                opencells.add((x, y, z))
        self.opencells = opencells
        # bay wall planes (themed y 0..8), bay ceilings, lintels
        self.baywall = {}
        for b in lay["bays"]:
            o, U, V, D = b["origin"], b["U"], b["V"], b["depth"]

            def w(u, v):
                return (o[0] + u * U[0] + v * V[0], o[1] + u * U[1] + v * V[1])
            for v in range(0, D + 1):
                self.baywall.setdefault(w(-1, v), b["theme"])
                self.baywall.setdefault(w(L.BAY_W, v), b["theme"])
            for u in range(-1, L.BAY_W + 1):
                self.baywall.setdefault(w(u, D), b["theme"])
        foyer = lay["regions"]["foyer"]
        for (x, z), t in mt.items():
            lo = -1
            if (x, z) in near_water:
                lo = -4
            for y in range(lo, t + 1):
                c = (x, y, z)
                if c in opencells:
                    self.put(x, y, z, "air")
                    continue
                if (x, z) in water and bed[(x, z)] < y <= -1:
                    self.put(x, y, z, "water[level=0]", "water")
                    continue
                self.put(x, y, z, self._material(x, y, z, t, foyer), "solid")
        # lintels over the bay mouths
        for b in lay["bays"]:
            o, U, V = b["origin"], b["U"], b["V"]
            for u in range(L.BAY_W):
                x, z = o[0] + u * U[0], o[1] + u * U[1]
                self.put(x, L.BAY_TOP, z, MAT[b["theme"]]["beam"].replace("axis=x", "axis=" + ("x" if U[0] else "z")))
        # the arch: dark stone ring with a gold keystone; HERMES letters on the facade
        self._facade()

    def _material(self, x, y, z, t, foyer):
        lay = self.lay
        oc = self.opencells
        if y == -1 and (x, 0, z) in oc:
            return self._floor(x, z)
        if y < -1 or (y == -1 and (x, z) in self.water):
            if y == self.bed.get((x, z)):
                r = h2(x, z, 43)
                return "sea_lantern" if r > 0.86 else ("clay" if h2(x, z, 44) > 0.5 else "mud")
            return "stone"
        if z == 1 and y >= 0:
            return self._facade_block(x, y)
        th = self.baywall.get((x, z))
        if th and 0 <= y <= L.BAY_TOP:
            return wall_fn(th, x, y, z)
        if y == L.BAY_TOP + 1 and (x, z) in self.bayof:
            b, u, v = self.bayof[(x, z)]
            return theme_ceiling(b["theme"], u, v)
        touch = any((x + a, y + b, z + c) in oc for a, b, c in NB6)
        if y == t and (x, y + 1, z) not in oc and z > 1:
            return "moss_block"                      # the top of the mound
        if foyer[0] - 1 <= x <= foyer[1] + 1 and 2 <= z <= foyer[3] and touch:
            if y == 7:
                return "ochre_froglight[axis=y]" if (x + z) % 3 == 0 else HERMES_WALL
            if abs(x) == foyer[1] + 1 and z in (4, 6):
                return GOLD if y in (0, 5) else "gilded_blackstone" if y == 3 else "polished_blackstone"
            if y == 4 and abs(x) == foyer[1] + 1:
                return "ochre_froglight[axis=y]" if z == 5 else "chiseled_polished_blackstone"
            return HERMES_WALL
        if touch:
            if (x, y - 1, z) in oc and (x, y + 1, z) not in oc:      # ceiling
                return "moss_block" if vnoise(x, z, 4, 52) > 0.5 else rock(x, y, z)
            band = 2 + int(2 * vnoise(x, z, 3, 58))
            if y <= band:
                return "moss_block"
            if vnoise(x + y, z - y, 4, 59) > 0.74:
                return "amethyst_block"
            return rock(x, y, z)
        exposed = any((x + a, y + b, z + c) not in self.cells and not self._will_be_set(x + a, y + b, z + c)
                      for a, b, c in NB6)
        if exposed:
            if y == t:
                return "moss_block"
            return rock(x, y, z) if h3(x, y, z, 13) > 0.25 else "mossy_cobblestone"
        return "stone"

    def _will_be_set(self, x, y, z):
        t = self.mt.get((x, z))
        if t is None:
            return False
        lo = -4 if (x, z) in self.water else -1
        return lo <= y <= t

    def _floor(self, x, z):
        reg = self.region.get((x, z))
        if reg == "bay":
            b, u, v = self.bayof[(x, z)]
            return zone_floor(b["theme"], u, v, L.BAY_W, b["depth"])
        if reg in ("walk", "bridge", "arch") or reg == "foyer":
            return self._path(x, z, reg)
        return moss_floor(x, z)

    def _path(self, x, z, reg):
        walk = ("walk", "bridge", "arch", "foyer")
        edge = any(self.region.get((x + a, z + b)) not in walk for a, b in NB4)
        if reg == "foyer" or reg == "arch":
            if abs(x) <= 1:
                return GOLD if z % 2 == 0 and x == 0 else "polished_blackstone_bricks"
            return "polished_deepslate" if edge else "smooth_quartz"
        if edge:
            return "polished_deepslate"
        if (x * 3 + z * 7) % 11 == 0:
            return "pearlescent_froglight[axis=y]"
        return "smooth_quartz"

    def _facade_block(self, x, y):
        """Outer face z 1: Hermes black, a gold-framed panel with glowing HERMES letters over the arch."""
        fc = self.lay["facade"]
        k = fc["scale"]
        lx0, lx1, ly0, ly1 = fc["letters_x0"], fc["letters_x1"], fc["letters_y0"], fc["letters_y1"]
        px0, px1, py0, py1 = lx0 - 2, lx1 + 2, ly0 - 1, ly1 + 1
        if px0 <= x <= px1 and py0 <= y <= py1:
            if x in (px0, px1) or y in (py0, py1):
                return GOLD if (x + y) % 2 == 0 else "gilded_blackstone"
            if lx0 <= x <= lx1 and ly0 <= y <= ly1:
                col, row = (lx1 - x) // k, (ly1 - y) // k     # read from outside (facing +z, +x is left)
                cx = 0
                for ch in "HERMES":
                    wd = len(FONT[ch][0])
                    if cx <= col < cx + wd:
                        return "ochre_froglight[axis=y]" if FONT[ch][row][col - cx] == "#" else "polished_blackstone"
                    cx += wd + 1
            return "polished_blackstone"
        a = L.ARCH
        if a["x0"] - 1 <= x <= a["x1"] + 1 and y <= a["y1"] + 1:
            if (x, y) == (0, a["y1"] + 1):
                return GOLD
            return "chiseled_polished_blackstone" if y % 2 == 0 else HERMES_WALL
        if abs(x) in (a["x1"] + 3, a["x1"] + 7) and y <= py0 - 1:
            return "ochre_froglight[axis=y]" if y == py0 - 2 else "polished_blackstone_bricks"
        if abs(x) <= fc["half"] and y <= py1 + 1:
            return "deepslate_tiles" if (x + y) % 5 else "cracked_deepslate_tiles"
        return rock(x, y, 1)

    def _facade(self):
        pass

    # ------------------------------------------------------------------ templates
    def _templates(self):
        lay = self.lay
        for d, p in zip(lay["desks"], self.profiles):
            key = f"desk_{d['index']}"
            kc = kit_cells(d["theme"])
            self.templates.append((key, d["template_pos"], d["rotation"], kc))
            for (tx, ty, tz) in kc:
                pass
            for c in d["cells"]:
                self.tplbox[tuple(c)] = key
        bd = lay["board"]
        self.templates.append(("board", bd["template_pos"], bd["rotation"], board_cells(self.url)))
        X, _, Z = bd["template_pos"]
        for tx in range(L.BOARD_W):
            for ty in range(L.BOARD_H):
                dx, dz = L.rot_xz(bd["rotation"], tx, 0)
                self.tplbox[(X + dx, ty, Z + dz)] = "board"
        for c in self.tplbox:
            self.cells.pop(c, None)
            self.phase.pop(c, None)

    def world_templates(self):
        """{(x, y, z): spec} of every template cell as placed (rotation applied to facing)."""
        out = {}
        for key, (X, Y, Z), rot, kc in self.templates:
            size = (L.BOARD_W, L.BOARD_H, 1) if key == "board" else (L.KIT_W, L.KIT_H, L.KIT_D)
            full = {(a, b, c): ("minecraft:air", None) for a in range(size[0]) for b in range(size[1])
                    for c in range(size[2])}
            full.update(kc)
            for (tx, ty, tz), (spec, _) in full.items():
                dx, dz = L.rot_xz(rot, tx, tz)
                out[(X + dx, Y + ty, Z + dz)] = rotate_spec(spec, rot)
        return out

    # ------------------------------------------------------------------ decor
    def _decor(self):
        lay = self.lay
        self._bays()
        self._signposts()
        self._waterfalls()
        self._tree(*self.tree_at, big=self.lay["garden_l"] >= 34)
        if self.tree2_at:
            self._tree(*self.tree2_at, big=False)
        self._mushrooms()
        self._water_life()
        self._posts()
        self._chandeliers()
        self._ceiling()
        self._rim()
        self._plants()
        self._topsoil()
        self._lights()

    def _bays(self):
        lay = self.lay
        for b in lay["bays"]:
            st, o, U, V, D = b["theme"], b["origin"], b["U"], b["V"], b["depth"]
            m = MAT[st]

            def w(u, v):
                return (o[0] + u * U[0] + v * V[0], o[1] + u * U[1] + v * V[1])
            # doormat in the mouth: theme colour with a gold centre
            for u in range(6, 11):
                x, z = w(u, 0)
                self.put(x, -1, z, GOLD if u == 8 else f"{m['carpet']}_concrete")
            # chairs in front of each kit (3 in front of the frame, back toward the room)
            for di in b["desks"]:
                d = lay["desks"][di]
                fx, fz = d["facing"]
                sx, _, sz = d["station"]
                cx, cz = sx + 3 * fx, sz + 3 * fz
                self.tryput(cx, 0, cz, f"{m['chair']}_stairs[facing={DIR[(fx, fz)]},half=bottom,shape=straight,waterlogged=false]", "attach")
            if b["spine"]:
                for v in range(0, 15):
                    x, z = w(8, v)
                    for y in range(0, L.KIT_H):
                        self.put(x, y, z, m["back"])
                    self.put(x, L.KIT_H, z, GOLD if v in (0, 7, 14) else m["beam"].replace("axis=x", "axis=" + ("z" if U[0] else "x")))
                for v in (0, 14):
                    x, z = w(8, v)
                    self.put(x, L.KIT_H + 1, z, "lantern[hanging=false,waterlogged=false]", "attach")
                chand = [w(4, D // 2), w(12, D // 2)]
            else:
                chand = [w(8, max(2, D // 2 - 1))]
                # rug + low table + planters
                r0, r1 = (5, 11)
                v0, v1 = 2, max(3, D - 5)
                for u in range(r0, r1 + 1):
                    for v in range(v0, v1 + 1):
                        x, z = w(u, v)
                        edge = u in (r0, r1) or v in (v0, v1)
                        self.tryput(x, 0, z, ("white_carpet" if edge else f"{m['carpet']}_carpet"), "attach")
            for (x, z) in chand:
                self._chandelier(x, z, L.BAY_TOP, low=7, arm="polished_blackstone_bricks", core=GOLD)
            for (u, v) in ((0, D - 1), (L.BAY_W - 1, D - 1)):
                x, z = w(u, v)
                if self.free((x, 0, z)) and self.free((x, 1, z)):
                    self.put(x, 0, z, "moss_block")
                    self.put(x, 1, z, "flowering_azalea", "attach")
            # lanterns hanging under the lintel
            for u in (2, L.BAY_W - 3):
                x, z = w(u, 0)
                self.tryput(x, L.BAY_TOP - 1, z, "lantern[hanging=true,waterlogged=false]", "hang")

    def _chandelier(self, x, z, top, low, arm=GOLD, core="ochre_froglight[axis=y]"):
        """Chain from the ceiling to a gold cross with four hanging lanterns (and one under the middle)."""
        cells = [(x, y, z) for y in range(low + 1, top + 1)] + [(x + a, low, z + b) for a, b in ((0, 0),) + NB4] + \
                [(x + a, low - 1, z + b) for a, b in ((0, 0),) + NB4]
        if not all(self.free(c) for c in cells):
            return False
        for y in range(low + 1, top + 1):
            self.put(x, y, z, "iron_chain[axis=y,waterlogged=false]", "hang")
        self.put(x, low, z, core)
        for a, b in NB4:
            self.put(x + a, low, z + b, arm)
            self.put(x + a, low - 1, z + b, "lantern[hanging=true,waterlogged=false]", "hang")
        self.put(x, low - 1, z, "lantern[hanging=true,waterlogged=false]", "hang")
        return True

    def _signposts(self):
        lay = self.lay
        for b in lay["bays"]:
            x, z = b["signpost"]
            m = MAT[b["theme"]]
            if not (self.free((x, 0, z)) and self.free((x, 1, z))):
                continue
            self.put(x, 0, z, m["post"])
            nb = len([bb for bb in lay["bays"] if bb["group"] == b["group"]])
            k = [bb["index"] for bb in lay["bays"] if bb["group"] == b["group"]].index(b["index"]) + 1
            lines = ["- " + b["theme_name"].upper() + " -", b["label"], f"{len(b['desks'])} agents",
                     f"bay {k} of {nb}" if nb > 1 else ""]
            self.put(x, 1, z, f"{m['sign']}_sign[rotation={b['sign_rotation']},waterlogged=false]", "sign")
            self.signs[(x, 1, z)] = (b["color"], lines)
        # welcome signpost in the foyer
        for (x, z, rot, lines) in ((L.FOYER_HALF - 1, 4, 0, ["~ welcome ~", "HERMES HQ", f"{lay['desk_count']} agents",
                                                               "loop: left ->"]),):
            self.put(x, 0, z, "polished_blackstone_bricks")
            self.put(x, 1, z, f"dark_oak_sign[rotation={rot},waterlogged=false]", "sign")
            self.signs[(x, 1, z)] = ("yellow", lines)
        # lanterns on gold-capped posts flanking the foyer
        for x in (-L.FOYER_HALF, L.FOYER_HALF):
            for z in (3, 7):
                if (x, z) == (L.FOYER_HALF - 1, 4):
                    continue
                self.put(x, 0, z, "polished_blackstone_bricks")
                self.put(x, 1, z, GOLD)
                self.put(x, 2, z, "lantern[hanging=false,waterlogged=false]", "attach")

    def _waterfalls(self):
        cg = self.lay["crag"]
        ft = cg["fall_top"]
        for fx, fz in cg["falls"]:
            sx = fx - (1 if fx > 0 else -1)          # notch cell inside the crag face
            assert (sx, fz) in self.crag
            self.put(sx, ft, fz, "water[level=0]", "water")
            for y in range(0, ft + 1):
                self.wet.add((fx, y, fz))
                for dz in (-1, 1):
                    self.wet.add((fx, y, fz + dz))
            self.wet.add((sx, ft, fz))
            # mossy lip + glow lichen around the slot
            self.put(sx, ft - 1, fz, "mossy_cobblestone")
            self.put(sx, ft + 1, fz, "moss_block")

    def _tree(self, tx, tz, big=True):
        """Glow tree (Omo Falls): mangrove trunk, azalea canopy, shroomlights, glow berries."""
        hgt = 9 if big else 7
        logs = set()
        for y in range(0, hgt):
            for (dx, dz) in ((0, 0), (1, 0), (0, 1), (1, 1)):
                logs.add((tx + dx, y, tz + dz))
        for (dx, dz, y0) in ((-1, 0, 0), (2, 1, 0), (0, -1, 1), (1, 2, 0)):
            logs.add((tx + dx, y0, tz + dz))
        for i in range(1, 4 if big else 3):
            logs.add((tx - i, hgt - 2 + i // 2, tz))
            logs.add((tx + 1 + i, hgt - 1 + i // 3, tz + 1))
            logs.add((tx, hgt - 2 + i // 2, tz - i))
            logs.add((tx + 1, hgt - 1 + i // 3, tz + 1 + i))
        ok = [c for c in logs if self.free(c) or (c[1] == 0 and self.region.get((c[0], c[2])) == "garden"
                                                  and (c[0], c[2]) not in self.water and self.is_air(c))]
        if len(ok) < len(logs):
            return
        for c in logs:
            self.put(*c, "mangrove_log[axis=y]")
        # moss mound round the roots
        for a in range(-2, 4):
            for b in range(-2, 4):
                x, z = tx + a, tz + b
                if self.region.get((x, z)) == "garden" and (x, z) not in self.water:
                    self.put(x, -1, z, "moss_block" if h2(x, z, 5) > 0.3 else "rooted_dirt")
        R = 5.8 if big else 4.3
        cy = hgt + 2
        leaves = set()
        for x in range(tx - 8, tx + 10):
            for z in range(tz - 8, tz + 10):
                t = self.top.get((x, z))
                if t is None:
                    continue
                for y in range(cy - 4, cy + 5):
                    dx, dy, dz = x - (tx + 0.5), (y - cy) * 1.5, z - (tz + 0.5)
                    r = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if r <= R + 1.2 * (vnoise(x + y, z, 2.5, 47) - 0.5) and y <= t - 2 and y >= 4 and \
                            self.free((x, y, z)):
                        leaves.add((x, y, z))
        glows = {c for c in leaves if h3(*c, 48) > 0.88}
        leaves -= glows
        for c in glows:
            self.put(*c, "shroomlight")
        dist = {c: 0 for c in logs}
        dq = deque(logs)
        while dq:
            c = dq.popleft()
            for d in NB6:
                n = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
                if n in leaves and n not in dist and dist[c] + 1 <= 7:
                    dist[n] = dist[c] + 1
                    dq.append(n)
        for c in leaves:
            kind = "flowering_azalea_leaves" if h3(*c, 49) > 0.55 else "azalea_leaves"
            self.put(*c, f"{kind}[distance={dist.get(c, 7)},persistent=true,waterlogged=false]")
        for (x, y, z) in sorted(glows):
            if not self.free((x, y - 1, z)) or h3(x, y, z, 50) > 0.5:
                continue
            n = 1 + int(h3(x, y, z, 51) * 3)
            if any(not self.free((x, y - i, z)) for i in range(1, n + 2)) or y - n <= 2:
                continue
            for i in range(1, n + 1):
                self.put(x, y - i, z, "cave_vines[age=25,berries=true]" if i == n else "cave_vines_plant[berries=true]",
                         "hang")
        # benches facing the tree
        for (x, z, f) in ((tx - 3, tz, "west"), (tx - 3, tz + 1, "west"), (tx + 4, tz, "east"), (tx + 4, tz + 1, "east")):
            if self.region.get((x, z)) == "garden" and (x, z) not in self.water:
                self.tryput(x, 0, z, f"mangrove_stairs[facing={f},half=bottom,shape=straight,waterlogged=false]", "attach")

    def _mushrooms(self):
        g = self.lay["regions"]["garden"]
        gx, s1 = g[1], self.lay["s1"]
        spots = [(gx - 2, s1 - 1, 4, "red_mushroom_block"), (-gx + 2, s1 - 2, 3, "brown_mushroom_block")]
        if self.tree2_at:
            spots += [(-gx + 2, self.tree2_at[1] + 1, 5, "red_mushroom_block")]
        if self.lay["garden_l"] >= 50:
            spots += [(gx - 2, self.tree_at[1] + 3, 4, "brown_mushroom_block")]
        for (x, z, hgt, cap) in spots:
            self._mushroom(x, z, hgt, cap)

    def _mushroom(self, x, z, hgt, cap):
        cells = [(x, y, z) for y in range(0, hgt)]
        yc = hgt
        caps = [(x + a, yc, z + b) for a in range(-2, 3) for b in range(-2, 3) if not (abs(a) == 2 and abs(b) == 2)]
        caps += [(x + a, yc + 1, z + b) for a in range(-1, 2) for b in range(-1, 2)]
        lights = [(x + a, yc - 1, z + b) for a, b in ((1, 1), (-1, -1), (1, -1), (-1, 1))]
        allc = cells + caps + lights
        if not all(self.free(c) for c in allc) or (x, z) in self.water or self.region.get((x, z)) != "garden":
            return
        for c in cells:
            self.put(*c, "mushroom_stem")
        for c in caps:
            self.put(*c, cap)
        for c in lights:
            self.put(*c, "shroomlight")
        # mushroom faces: same-block neighbours -> false
        for c in cells + caps:
            n0 = name(self.cells[c])
            props = {}
            for d, dn in zip(NB6, ("east", "west", "up", "down", "south", "north")):
                nb = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
                props[dn] = "false" if nb in self.cells and name(self.cells[nb]) == n0 else "true"
            self.put(*c, f"{n0}[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]")

    def _water_life(self):
        for (x, z) in sorted(self.water):
            if (x, 0, z) in self.wet:
                continue
            if self.region.get((x, z)) == "bridge":
                continue
            if h2(x, z, 45) < 0.10 and self.free((x, 0, z)):
                self.put(x, 0, z, "lily_pad", "aqua")
            b = self.bed[(x, z)]
            r2 = h2(x, z, 46)
            if r2 < 0.12:
                self.put(x, b + 1, z, "sea_pickle[pickles=3,waterlogged=true]", "aqua")
            elif r2 < 0.30:
                self.put(x, b + 1, z, "seagrass", "aqua")
        # culvert: walkway over the stream
        for (x, z) in self.water:
            if self.region.get((x, z)) == "bridge":
                self.put(x, -1, z, self._path(x, z, "bridge"))

    def _posts(self):
        """Lanterns on posts along the garden side of the walkways."""
        g = self.lay["regions"]["garden"]
        gx = g[1]
        s1 = self.lay["s1"]
        spots = []
        for z in range(g[2] + 2, s1, 6):
            spots += [(gx, z), (-gx, z)]
        for x in range(-gx + 3, gx - 2, 6):
            spots.append((x, s1 + 1))
        for (x, z) in spots:
            if (x, z) in self.water or self.region.get((x, z)) != "garden":
                continue
            if self.free((x, 0, z)) and self.free((x, 1, z)) and self.free((x, 2, z)):
                self.put(x, 0, z, "stripped_dark_oak_log[axis=y]")
                self.put(x, 1, z, "stripped_dark_oak_log[axis=y]")
                self.put(x, 2, z, "lantern[hanging=false,waterlogged=false]", "attach")

    def _chandeliers(self):
        R = self.lay["regions"]
        spots = []
        for key in ("left_walk", "right_walk"):
            r = R[key]
            cx = (r[0] + r[1]) // 2
            for z in range(r[2] + 4, r[3] - 1, 10):
                spots.append((cx, z))
        r = R["back_walk"]
        for x in range(r[0] + 6, r[1] - 3, 10):
            spots.append((x, (r[2] + r[3]) // 2))
        r = R["front_walk"]
        for x in (r[0] + 3, r[1] - 3):
            spots.append((x, (r[2] + r[3]) // 2))
        g = R["garden"]
        for z in range(g[2] + 12, g[3] - 4, 12):
            spots.append((0 if not self.tree_at or abs(self.tree_at[1] - z) > 6 else g[1] - 3, z))
        for (x, z) in spots:
            t = self.top.get((x, z))
            if t is None or t < 9:
                continue
            self._chandelier(x, z, t, low=min(7, t - 2))

    def _ceiling(self):
        """Moss patches with glow berries and spore blossoms on the vault; lichen elsewhere."""
        for (x, z), t in sorted(self.top.items()):
            reg = self.region.get((x, z))
            if reg in ("foyer", "arch"):
                continue
            c = (x, t, z)
            if not self.free(c) or any(self.wet and (x, y, z) in self.wet for y in (t, t - 1)):
                continue
            above = self.cells.get((x, t + 1, z))
            if above is None or name(above) in ("air", "water"):
                continue
            if reg == "bay":
                if h2(x, z, 70) < 0.018:
                    self.put(x, t, z, "spore_blossom", "hang")
                continue
            if name(above) == "moss_block":
                n = min(2 + int(h2(x, z, 54) * 6), t - 5)
                if h2(x, z, 53) < 0.18 and n >= 1 and all(self.free((x, t - i, z)) for i in range(n)):
                    for i in range(n):
                        y = t - i
                        self.put(x, y, z, "cave_vines[age=25,berries=true]" if i == n - 1 else
                                 f"cave_vines_plant[berries={'true' if h3(x, y, z, 55) > 0.45 else 'false'}]", "hang")
                elif h2(x, z, 56) < 0.06:
                    self.put(x, t, z, "spore_blossom", "hang")
            elif h2(x, z, 57) < 0.08:
                self.put(x, t, z, "glow_lichen[down=false,east=false,north=false,south=false,up=true,waterlogged=false,west=false]",
                         "attach")

    def _rim(self):
        """Amethyst clusters and glow lichen on the cavern walls."""
        for c, s in list(self.cells.items()):
            n = name(s)
            if n not in ("amethyst_block", "moss_block", "tuff", "deepslate", "stone"):
                continue
            x, y, z = c
            for (a, b) in NB4:
                f = (x + a, y, z + b)
                if not self.free(f) or (f[0], f[2]) not in self.top or self.region.get((f[0], f[2])) in ("bay", "foyer"):
                    continue
                if f[1] < 0:
                    continue
                fac = DIR[(a, b)]
                if n == "amethyst_block" and h3(*f, 60) > 0.55:
                    self.put(*f, f"amethyst_cluster[facing={fac},waterlogged=false]", "attach")
                elif n != "amethyst_block" and y >= 4 and h3(*f, 61) < 0.035:
                    opp = DIR[(-a, -b)]
                    props = {k: "false" for k in ("down", "east", "north", "south", "up", "waterlogged", "west")}
                    props[opp] = "true"
                    self.put(*f, "glow_lichen[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]", "attach")

    def _plants(self):
        for (x, z), reg in sorted(self.region.items()):
            if reg != "garden" or (x, z) in self.water:
                continue
            if not self.free((x, 0, z)) or name(self.cells.get((x, -1, z), "")) not in ("moss_block", "rooted_dirt"):
                continue
            r = h2(x, z, 62)
            for t, p in PLANTMIX:
                if r < t:
                    self.put(x, 0, z, p, "attach")
                    break

    def _topsoil(self):
        """Moss, grass and azalea bushes on the outside of the mound (it is a hill when built in the open)."""
        for (x, z), t in sorted(self.mt.items()):
            if (x, z) in self.top or self.cells.get((x, t, z)) != "minecraft:moss_block" or (x, t + 1, z) in self.cells:
                continue
            r = h2(x, z, 81)
            for th, p in TOPMIX:
                if r < th:
                    self.put(x, t + 1, z, p, "attach")
                    break

    def _lights(self):
        """Invisible light blocks: no mob spawns, no dark corners."""
        for (x, z), t in self.top.items():
            if x % 5 or z % 5:
                continue
            for y in (3, 9, 15):
                if y <= t and self.free((x, y, z)):
                    self.put(x, y, z, "light[level=15,waterlogged=false]", "attach")


def rotate_spec(spec, rot):
    """Block state as placed by `place template ... rot` (facing / axis / rotation props)."""
    if rot == "none" or "[" not in spec:
        return spec
    fmap = {"180": {"north": "south", "south": "north", "east": "west", "west": "east"},
            "clockwise_90": {"north": "east", "east": "south", "south": "west", "west": "north"},
            "counterclockwise_90": {"north": "west", "west": "south", "south": "east", "east": "north"}}[rot]
    base, props = spec.split("[", 1)
    out = []
    for kv in props.rstrip("]").split(","):
        k, v = kv.split("=", 1)
        if k == "facing":
            v = fmap.get(v, v)
        elif k == "axis" and rot != "180" and v in ("x", "z"):
            v = "z" if v == "x" else "x"
        out.append(f"{k}={v}")
    return base + "[" + ",".join(out) + "]"
