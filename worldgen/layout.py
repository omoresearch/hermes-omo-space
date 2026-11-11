"""Hermes HQ v2 layout: a glowing cavern hall, as plain data (no blocks here).

Coordinates are relative to the build origin: (0, 0, 0) is the block the player
stands in when they run the build (after `execute align xyz`); y 0 is their feet,
the hall floor is y -1. The hall grows toward +Z (south): the facade is z 1..2
with a 5-wide arch on x -2..2, so the player starts just outside and is never
inside the built volume.

    z 1..2    facade: arch + big glowing HERMES letters
    z 3..7    foyer (Hermes black + gold)
    z 8..11   front walkway; straight ahead the Board crag: the Hermes Board web
              wall in its north face, a waterfall down each side into the pond
    z 12..S1  the garden (pond, glow tree, stream, mushrooms), walkways left and
              right of it, side bays off the walkways
    S1+2..+5  back walkway; back bays behind it

The walkway is one level loop: foyer -> left side -> back -> right side ->
foyer. Desks live in bays ("neighbourhoods"): small themed rooms off the loop,
up to 10 desks each (2 on the back wall, 2 per side wall, 4 on a low spine in
the middle). Profiles that share a name prefix (omo-*, yt-*, lf-*) share bays
and a material theme, so a big setup reads as neighbourhoods. Groups with fewer
than 3 desks share a 'misc' bay.

Every desk is a 7 x 5 x 2 structure template drawn facing south and placed with
`place template ... <rotation>`; rotation maps template (x, z) to world
offsets: none (x, z), clockwise_90 (-z, x), 180 (-x, -z), counterclockwise_90
(z, -x) (StructureTemplate.transform with pivot 0, checked on 26.2).
"""
import math
import re

# desk kit (template drawn facing south): frame row z 0, desk row z 1
KIT_W, KIT_H, KIT_D = 7, 5, 2
KIT_MID = 3                       # the Omo Computer: under the screen, frame row
SCREEN_W, SCREEN_H = 5, 3         # terminal wall above the computer (monitor limit 16 x 12)
PLATE_T = (1, 1, 1)               # name plate on the desk, left of the open cell
LAMP_T = (5, 1, 1)                # status lamp on the desk, right of the open cell
SIGN_T = (3, 4, 1)                # glowing wall sign on the top beam
DESK_W = KIT_W                    # kept for older callers

BOARD_W, BOARD_H = 11, 7          # board template (frame), 1 deep
BOARD_MON_W, BOARD_MON_H = 9, 5   # the web wall itself
HARD_MAX_DESKS = 96               # the bridge allows 128 workspaces in total
BAY_CAP = 10
BAY_W = 17                        # bay interior width (u 0..16), walls at u -1 and 17
PITCH = BAY_W + 2                 # neighbouring bays keep their own wall (2 thick)
WALK = 4                          # walkway width
FOYER_HALF = 5                    # foyer x -5..5
S0 = 12                           # first garden row
GARDEN_MIN_W = 19
GARDEN_MIN_L = 26
BAY_TOP = 8                       # bays: air y 0..8, ceiling y 9
INNER_H = BAY_TOP + 1
FLOOR_Y = -1
ARCH = {"x0": -2, "x1": 2, "y0": 0, "y1": 4, "z0": 1, "z1": 2}
CRAG_HALF = 6                     # Board crag x -6..6, z 12..17
CRAG_Z = (S0, S0 + 5)

ROTATIONS = {(0, 1): "none", (-1, 0): "clockwise_90", (0, -1): "180", (1, 0): "counterclockwise_90"}


def rot_xz(rot, x, z):
    """Template offset (x, z) -> world offset for `place template ... rot`."""
    return {"none": (x, z), "clockwise_90": (-z, x), "180": (-x, -z), "counterclockwise_90": (z, -x)}[rot]


# bay themes (the Omo Falls / mountain studio zone palettes) + their glowing sign colour
THEMES = {
    1: ("birch", "yellow"), 2: ("cherry", "pink"), 3: ("library", "orange"), 4: ("prismarine", "cyan"),
    5: ("copper", "red"), 6: ("tuff", "lime"), 7: ("quartz", "light_blue"), 8: ("purpur", "magenta"),
}
THEME_ORDER = [4, 2, 1, 5, 6, 8, 3, 7]


def group_of(name):
    """Neighbourhood key: a leading alphabetic prefix of 2+ letters before - _ or . ('omo-05-x' -> 'omo')."""
    m = re.match(r"^([A-Za-z]{2,})[-_.]", name or "")
    return m.group(1).lower() if m else ""


def _groups(names):
    """[(key, label, [desk indices])] in first-appearance order; small groups merge into 'misc'."""
    order, members = [], {}
    for i, nm in enumerate(names):
        k = group_of(nm)
        if k not in members:
            order.append(k)
            members[k] = []
        members[k].append(i)
    big = [k for k in order if k and len(members[k]) >= 3]
    small = [k for k in order if k not in big]
    out = [(k, k + "-*", members[k]) for k in big]
    if small:
        idx = sorted(i for k in small for i in members[k])
        if big:
            out.append(("misc", "misc", idx))
        elif len(small) == 1 and small[0]:
            out.append((small[0], small[0] + "-*", idx))
        else:
            out.append(("misc", "agents", idx))
    return out


def _chunks(idx, cap):
    k = max(1, math.ceil(len(idx) / cap))
    base, extra = divmod(len(idx), k)
    out, p = [], 0
    for j in range(k):
        m = base + (1 if j < extra else 0)
        out.append(idx[p:p + m])
        p += m
    return out


def bay_depth(cap):
    return 7 if cap <= 2 else 11 if cap <= 4 else 19


def bay_slots(cap, depth):
    """Kit slots for a bay of `cap` desks, in bay-local (u, v): list of (frame cells, facing).
    u runs across the mouth (0..16), v from the mouth (0) to the back wall (depth)."""
    D = depth
    if cap == 1:
        return [([(u, D) for u in range(5, 12)], (0, -1))]
    slots = [([(u, D) for u in range(1, 8)], (0, -1)), ([(u, D) for u in range(9, 16)], (0, -1))]
    if D >= 11:
        slots.append(([(-1, v) for v in range(D - 10, D - 3)], (1, 0)))
        slots.append(([(BAY_W, v) for v in range(D - 10, D - 3)], (-1, 0)))
    if D >= 19:
        slots.append(([(-1, v) for v in range(1, 8)], (1, 0)))
        slots.append(([(BAY_W, v) for v in range(1, 8)], (-1, 0)))
        # the spine (partition u 8, v 0..14) with a kit on each face, two rows
        slots.append(([(7, v) for v in range(8, 15)], (-1, 0)))
        slots.append(([(9, v) for v in range(8, 15)], (1, 0)))
        slots.append(([(7, v) for v in range(0, 7)], (-1, 0)))
        slots.append(([(9, v) for v in range(0, 7)], (1, 0)))
    return slots[:cap]


def _dims(nback, nside, bd):
    gw = max(GARDEN_MIN_W, nback * PITCH - 2 - 2 * WALK)
    gw += 1 - gw % 2
    hw = gw // 2 + WALK
    gl = max(GARDEN_MIN_L, nside * PITCH - 2)
    s1 = S0 + gl - 1
    back_half = (nback * PITCH - 2) // 2 + 1
    width = 2 * (max(hw, back_half) + (bd + 2 if nside else 3)) + 1
    depth = s1 + 6 + (bd + 2 if nback else 2)
    return gw, hw, gl, s1, width, depth


def _choose(nbays, bd):
    best = None
    for nback in range(1, min(nbays, 5) + 1):
        rest = nbays - nback
        nside = math.ceil(rest / 2)
        w, d = _dims(nback, nside, bd)[4:]
        score = max(w, d) + 0.35 * min(w, d) + (4 if rest % 2 else 0)
        if best is None or score < best[0]:
            best = (score, nback, rest)
    _, nback, rest = best
    return nback, (rest + 1) // 2, rest // 2


def plan_layout(n, names=None, theme_order=None, rooms=None):
    """Positions for n desks (n >= 1): hall box, regions, bays, desks, Board, garden.
    `names` (profile names, desk order) drives the neighbourhood grouping; `theme_order` (bay theme
    numbers, see THEMES) is the palette the bays cycle through (default THEME_ORDER).
    `rooms` (from the world designer) replaces the prefix grouping: [{"name", "theme": THEMES number,
    "desks": [desk indices]}], each desk in exactly one room, rooms in hall order."""
    order = [t for t in (theme_order or THEME_ORDER) if t in THEMES] or THEME_ORDER
    n = max(1, min(int(n), HARD_MAX_DESKS))
    names = [str(x) for x in (names or [])][:n]
    names += [f"agent{i}" for i in range(len(names), n)]
    if rooms:
        groups = [(f"room{i}", str(r["name"]), [d for d in r["desks"] if 0 <= d < n]) for i, r in enumerate(rooms)]
        groups = [g for g in groups if g[2]]
        forced = {f"room{i}": r.get("theme") for i, r in enumerate(rooms)}
    else:
        groups, forced = _groups(names), {}
    bays = []
    for gi, (key, label, idx) in enumerate(groups):
        theme = forced.get(key) if forced.get(key) in THEMES else order[gi % len(order)]
        for part in _chunks(idx, BAY_CAP):
            bays.append({"group": key, "label": label, "theme": theme, "theme_name": THEMES[theme][0],
                         "color": THEMES[theme][1], "desks": part,
                         "about": str((rooms[int(key[4:])].get("about") if rooms else "") or "")})
    bd = bay_depth(max(len(b["desks"]) for b in bays))
    nback, nleft, nright = _choose(len(bays), bd)
    nside = max(nleft, nright)
    gw, hw, gl, s1, _, _ = _dims(nback, nside, bd)
    gx = gw // 2
    xm = hw + 1                                    # side bay mouths
    zb = s1 + 6                                    # back bay mouths
    lb = nback * PITCH - 2
    bx_max = lb - 1 - lb // 2

    # bay frames: world = origin + u * U + v * V (same handedness everywhere)
    frames = []
    for k in range(nleft):
        frames.append(("left", (xm, S0 + PITCH * k), (0, 1), (1, 0)))
    for j in range(nback):
        frames.append(("back", (bx_max - PITCH * j, zb), (-1, 0), (0, 1)))
    for k in reversed(range(nright)):
        frames.append(("right", (-xm, S0 + PITCH * k + BAY_W - 1), (0, -1), (-1, 0)))
    assert len(frames) == len(bays)

    desks = [None] * n
    for bi, (b, (side, o, U, V)) in enumerate(zip(bays, frames)):
        def w(u, v, o=o, U=U, V=V):
            return (o[0] + u * U[0] + v * V[0], o[1] + u * U[1] + v * V[1])

        corners = [w(0, 0), w(BAY_W - 1, bd - 1)]
        b.update({"index": bi, "side": side, "origin": list(o), "U": list(U), "V": list(V), "depth": bd,
                  "x0": min(c[0] for c in corners), "x1": max(c[0] for c in corners),
                  "z0": min(c[1] for c in corners), "z1": max(c[1] for c in corners),
                  "spine": len(b["desks"]) >= 7,
                  "signpost": list(w(0, 0)), "sign_rotation": {"left": 4, "back": 8, "right": 12}[side]})
        for di, (cells, f) in zip(b["desks"], bay_slots(len(b["desks"]), bd)):
            wc = [w(*c) for c in cells]
            face = (f[0] * U[0] + f[1] * V[0], f[0] * U[1] + f[1] * V[1])
            rot = ROTATIONS[face]
            ax = rot_xz(rot, 1, 0)
            start = next(c for c in wc if (c[0] + 6 * ax[0], c[1] + 6 * ax[1]) in wc)

            def tw(tx, ty, tz, start=start, rot=rot):
                dx, dz = rot_xz(rot, tx, tz)
                return [start[0] + dx, ty, start[1] + dz]

            st = tw(KIT_MID, 0, 0)
            desks[di] = {
                "index": di, "name": names[di], "bay": bi, "group": b["group"], "theme": b["theme"],
                "template_pos": [start[0], 0, start[1]], "rotation": rot, "facing": list(face),
                "station": st, "front": [st[0] + face[0], 0, st[2] + face[1]],
                "plate": tw(*PLATE_T), "lamp": tw(*LAMP_T), "sign": tw(*SIGN_T),
                "monitors": [tw(x, y, 0) for y in range(1, 1 + SCREEN_H) for x in range(1, 1 + SCREEN_W)],
                "cells": [tw(x, y, z) for x in range(KIT_W) for y in range(KIT_H) for z in range(KIT_D)],
                "text_pos": [st[0] + 0.5 + 0.7 * face[0], 5.25, st[2] + 0.5 + 0.7 * face[1]],
            }

    regions = {
        "foyer": [-FOYER_HALF, FOYER_HALF, 3, 7],
        "front_walk": [-hw, hw, 8, 11],
        "left_walk": [gx + 1, hw, 8, s1 + 5],
        "right_walk": [-hw, -gx - 1, 8, s1 + 5],
        "back_walk": [min(-hw, -(lb // 2) - 1), max(hw, bx_max + 1), s1 + 2, s1 + 5],
        "garden": [-gx, gx, S0, s1 + 1],
    }
    if gl >= 34:
        mz = S0 + 8 + (gl - 8) // 2
        regions["bridge"] = [-gx, gx, mz - 1, mz + 1]
    crag = {"x0": -CRAG_HALF, "x1": CRAG_HALF, "z0": CRAG_Z[0], "z1": CRAG_Z[1],
            "falls": [[-CRAG_HALF - 1, CRAG_Z[0] + 3], [CRAG_HALF + 1, CRAG_Z[0] + 3]], "fall_top": 8}
    board = {"template_pos": [BOARD_W // 2, 0, S0], "rotation": "180", "w": BOARD_W, "h": BOARD_H,
             "x0": -(BOARD_W // 2), "z": S0, "monitor_z": S0, "monitor_w": BOARD_MON_W, "monitor_h": BOARD_MON_H,
             "monitors": [[x, y, S0] for y in range(1, 1 + BOARD_MON_H)
                          for x in range(-(BOARD_MON_W // 2), BOARD_MON_W // 2 + 1)]}
    vault = min(9, 3 + hw // 3)
    # facade: HERMES in a 3x5 pixel font (25 x 5), drawn at scale 2 on wide halls
    scale = 2 if hw >= 15 else 1
    word_w = 25 * scale
    facade = {"scale": scale, "letters_x0": -(word_w // 2), "letters_x1": -(word_w // 2) + word_w - 1,
              "letters_y0": 6, "letters_y1": 6 + 5 * scale - 1,
              "half": max(hw + 2, word_w // 2 + 4), "top": 6 + 5 * scale + 3}
    xs = [r[0] for r in regions.values()] + [r[1] for r in regions.values()] + \
         [b["x0"] for b in bays] + [b["x1"] for b in bays] + [-facade["half"], facade["half"]]
    zs = [r[3] for r in regions.values()] + [b["z1"] for b in bays]
    margin = 8
    hall = {"x0": min(xs) - margin, "x1": max(xs) + margin, "y0": -4,
            "y1": max(BAY_TOP + vault + 5, facade["top"] + 2),
            "z0": 1, "z1": max(zs) + margin}
    size = [hall["x1"] - hall["x0"] + 1, hall["y1"] - hall["y0"] + 1, hall["z1"] - hall["z0"] + 1]
    return {
        "version": 2, "desk_count": n, "hall": hall, "size": size,
        "garden_w": gw, "garden_l": gl, "half_width": hw, "s1": s1, "vault": vault, "bay_depth": bd,
        "bays_left": nleft, "bays_back": nback, "bays_right": nright,
        "door": {"x0": ARCH["x0"], "x1": ARCH["x1"], "y0": 0, "y1": 3, "z": 1},
        "arch": ARCH, "regions": regions, "crag": crag, "board": board, "facade": facade,
        "groups": [{"key": k, "label": lab, "desks": idx} for k, lab, idx in groups],
        "bays": bays, "desks": desks,
        # v1 fields kept for older callers (summary output)
        "cols": len(bays), "rows": max(len(b["desks"]) for b in bays),
    }
