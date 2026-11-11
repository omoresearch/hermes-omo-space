#!/usr/bin/env python3
"""Render docs/hermes-omo.png: the REAL `hermes omo create` session from 11 Nov 2026 (treehouse wish,
model-designed plan, build, how-to-get-in) as a terminal card. Text copied verbatim from the captured run;
the only edits: the duplicate blank line and the room lines' ellipses kept as the CLI printed them."""
import os
from PIL import Image, ImageDraw, ImageFont

BR = os.path.expanduser("~/gtm/projects/omo-space/brief/brand/fonts")
MONO = ImageFont.truetype(f"{BR}/IBMPlexMono-Regular.ttf", 22)
MONO_B = ImageFont.truetype(f"{BR}/IBMPlexMono-Medium.ttf", 22)
SERIF = ImageFont.truetype(f"{BR}/CrimsonText-SemiBold.ttf", 30)
SYM = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 22)   # ◆ ✓ › (not in Plex Mono)
INK, CREAM, DIM, GOLD, GREEN, BAR = (26, 16, 8), (245, 240, 232), (176, 164, 146), (214, 170, 92), (150, 196, 130), (44, 32, 22)

# (text, style) segments per line; styles: n normal, d dim, b bold, g gold, k green
L = [
    [("$ ", "d"), ("hermes omo", "b")],
    [],
    [("  ◆ ", "g"), ("Omo Space", "b"), ("   a world for your Hermes agents", "d")],
    [("    Hermes will build you a Minecraft world where each of your 6 agents (profiles) has a desk,", "n")],
    [("    grouped into rooms by what they do.", "n")],
    [],
    [("    What kind of world would you like?", "b"), ("  Describe it in your own words,", "d")],
    [("    or just press Enter and Hermes will surprise you.", "d")],
    [("    › ", "g"), ("a little treehouse village in a forest", "n")],
    [],
    [("  ◆ ", "g"), ("Mossbough Hollow", "b"), ("   Lanterns sway between the branches; your crew is already up the ladders.", "d")],
    [("    Cherry Blossom · meadow · morning", "d")],
    [],
    [("    Lookout Nest    ", "n"), (" 2", "g"), ("  Keeping watch over the machine, terminals and agents · default, floor5-agent-ops", "d")],
    [("    Canopy Council  ", "n"), (" 1", "g"), ("  Weekly reviews, commitments and kanban triage · omo-00-chief-of-staff", "d")],
    [("    Acorn Archive   ", "n"), (" 2", "g"), ("  Mining streams for long-form video ideas · lf-01-ideas, yt-01-video-research", "d")],
    [("    Bark Edit Bay   ", "n"), (" 1", "g"), ("  Cutting each long-form video to its plan · yt-03-editing", "d")],
    [],
    [("    Happy with it? ", "n"), ("Enter", "b"), (" builds it.  ", "n"), ("a", "b"), (" = a different idea,  ", "n"), ("q", "b"), (" = quit,", "n")],
    [("    or type what to change, e.g. \"make it night\" or \"put the editors together\".", "d")],
    [("    › ", "g"), ("(Enter)", "d")],
    [],
    [("    ✓ ", "k"), ("4 rooms, 6 desks with screens, the Hermes Board", "n")],
    [("    ✓ ", "k"), ("New world \"Mossbough Hollow\" (0.7s)", "n")],
    [],
    [("  ◆ ", "g"), ("Your world is ready. To go in:", "b")],
    [("    1. Open Omo Space. Press Play (sign in with Microsoft the first time).", "n")],
    [("    2. Click Singleplayer.", "n")],
    [("    3. Double-click \"Mossbough Hollow\".", "n")],
    [("    4. You arrive at the front door and the building rises around you.", "n")],
]
COL = {"n": CREAM, "d": DIM, "b": CREAM, "g": GOLD, "k": GREEN}
LH, PADX, TOP = 34, 56, 104
W = 1480
H = TOP + LH * len(L) + 56
img = Image.new("RGB", (W, H), INK)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, W, 64], fill=BAR)
for i, c in enumerate([(196, 120, 96), (214, 170, 92), (150, 170, 120)]):
    d.ellipse([28 + i * 30, 24, 44 + i * 30, 40], fill=c)
d.text((W // 2, 32), "hermes omo", font=SERIF, fill=CREAM, anchor="mm")
y = TOP
for line in L:
    x = PADX
    for text, st in line:
        f = MONO_B if st == "b" else MONO
        for ch in text:
            g = SYM if ch in "◆✓›" else f
            d.text((x, y), ch, font=g, fill=COL[st])
            x += d.textlength(ch, font=f)
    y += LH
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hermes-omo.png")
img.save(out, optimize=True)
print(out, img.size)
