"""Hall geometry for Hermes HQ, in coordinates relative to the build origin.

Origin (0, 0, 0) is the block the player stands in when they run the build
(after `execute align xyz`); y 0 is their feet, the floor is y -1. The hall
extends toward +Z (south): the entrance wall is z 1 with a 3-wide door on
x -1..1, so the player stands just outside the door and is never inside the
cleared volume.

  z 1                 entrance wall + door
  z 2..6              foyer
  z 7, 13, 19, ...    desk rows: the screen layer (zf) faces the door (-Z),
                      the desk's back panel is zf + 1, then a 4-deep aisle
  last back + 5       back wall with the Hermes Board facing the door

A 5-wide central aisle (x -2..2) runs from the door to the Board, desks sit
left and right of it in columns 6 apart (5-wide desk + 1 gap).
"""
import math

DESK_W = 5          # desk template width (x)
DESK_D = 2          # screen layer + back panel
COL_PITCH = 6
ROW_PITCH = 6
AISLE_HALF = 2      # central aisle x -2..2
SIDE_MARGIN = 2     # floor between the outer desks and the side walls
FOYER = 5           # z 2..6
FIRST_ROW_Z = 2 + FOYER
BACK_SPACE = 4      # floor between the last desk row and the Board
INNER_H = 6         # y 0..5 inside, ceiling y 6
MIN_HALF = 7        # the hall is at least 2 * 7 + 1 wide inside (the Board is 9 wide)
MAX_COLS = 8
BOARD_W, BOARD_H = 9, 5           # board template (backing incl. frame)
BOARD_MON_W, BOARD_MON_H = 7, 3   # the web wall itself
HARD_MAX_DESKS = 96               # the bridge allows 128 workspaces in total


def columns_for(n):
    if n <= 1:
        return 1
    return max(1, min(MAX_COLS, n, math.ceil(math.sqrt(n * 1.5))))


def plan_layout(n):
    """Positions for n desks (n >= 1) plus the hall box, door and Board."""
    n = max(1, min(int(n), HARD_MAX_DESKS))
    cols = columns_for(n)
    rows = math.ceil(n / cols)
    left = cols // 2
    right = cols - left
    # desk column x starts, ordered left-to-right as seen from the door (facing +Z,
    # the player's left is +X): +X columns first, outermost first
    starts = []
    for i in reversed(range(right)):
        starts.append(AISLE_HALF + 1 + i * COL_PITCH)                         # x0 .. x0+4
    for i in range(left):
        starts.append(-(AISLE_HALF + 1) - i * COL_PITCH - (DESK_W - 1))
    starts.sort(reverse=True)

    half_r = AISLE_HALF + right * COL_PITCH + 1 if right else AISLE_HALF + SIDE_MARGIN
    half_l = AISLE_HALF + left * COL_PITCH + 1 if left else AISLE_HALF + SIDE_MARGIN
    inner_x1 = max(half_r, MIN_HALF)
    inner_x0 = -max(half_l, MIN_HALF)
    last_back = FIRST_ROW_Z + (rows - 1) * ROW_PITCH + 1
    back_wall = last_back + BACK_SPACE + 1            # board monitors at back_wall - 1
    hall = {"x0": inner_x0 - 1, "x1": inner_x1 + 1, "y0": -1, "y1": INNER_H, "z0": 1, "z1": back_wall}

    desks = []
    for i in range(n):
        r, c = divmod(i, cols)
        x0 = starts[c]
        zf = FIRST_ROW_Z + r * ROW_PITCH
        desks.append({
            "index": i, "row": r, "col": c, "x0": x0, "zf": zf,
            # template drawn facing south, placed with rotation 180: (x, z) -> (X - x, Z - z)
            "template_pos": [x0 + DESK_W - 1, 0, zf + 1],
            "station": [x0 + 2, 0, zf],
            "text_pos": [x0 + 2.5, 3.1, zf + 0.5],
        })
    board = {"x0": -(BOARD_W // 2), "w": BOARD_W, "h": BOARD_H, "z": back_wall,
             "monitor_z": back_wall - 1, "monitor_w": BOARD_MON_W, "monitor_h": BOARD_MON_H,
             "template_pos": [BOARD_W // 2, 0, back_wall]}
    size = [hall["x1"] - hall["x0"] + 1, hall["y1"] - hall["y0"] + 1, hall["z1"] - hall["z0"] + 1]
    return {
        "desk_count": n, "cols": cols, "rows": rows, "hall": hall, "size": size,
        "door": {"x0": -1, "x1": 1, "y0": 0, "y1": 2, "z": 1},
        "desks": desks, "board": board,
    }
