"""The world designer: turn a one-line wish ("a cosy library in the snow", or nothing = be creative)
plus this Hermes install (profile names and their one-line descriptions) into a world plan.

The plan is plain data, always validated here before anything is built:

    {"name": str, "tagline": str, "style": STYLE key, "ground": GROUND key, "time": TIME key,
     "rooms": [{"name": str, "theme": THEME name, "about": str, "profiles": [profile names]}]}

Every profile lands in exactly one room; whatever the model leaves out goes to a "Commons" room, and a
plan the model could not produce falls back to `fallback_plan` (rooms from name prefixes). The model
sees only profile names, display labels and descriptions (from profile.yaml) and the user's wish:
never chats, memories, config values or secrets.
"""

from __future__ import annotations

import random
import re
from typing import Any, Callable, Optional

STYLES = {
    "falls": ("Omo Falls", "glowing cavern, prismarine and cherry", ["prismarine", "cherry", "birch", "copper"]),
    "blossom": ("Cherry Blossom", "soft pinks, purpur and white quartz", ["cherry", "purpur", "quartz", "birch"]),
    "library": ("Old Library", "dark oak, bookshelves, warm copper", ["library", "copper", "birch", "tuff"]),
    "studio": ("Clean Studio", "quartz, birch and glass, bright and calm", ["quartz", "birch", "prismarine", "purpur"]),
    "forge": ("Copper Forge", "deepslate, copper and tuff, moody", ["copper", "tuff", "library", "prismarine"]),
}
GROUNDS = {"grass": "Meadow", "snow": "Snowfield", "sand": "Desert", "stone": "Stone plaza"}
TIMES = {"morning": ("Morning", 1000), "noon": ("Noon", 6000), "sunset": ("Golden hour", 12000),
         "night": ("Night", 18000)}
THEMES = {"birch": 1, "cherry": 2, "library": 3, "prismarine": 4, "copper": 5, "tuff": 6, "quartz": 7, "purpur": 8}
THEME_BLURB = {"birch": "pale wood, warm yellow light", "cherry": "pink cherry wood", "library": "bookshelves, dark oak",
               "prismarine": "sea-green stone, sea lanterns", "copper": "deepslate and copper",
               "tuff": "grey-green tuff, bamboo", "quartz": "white quartz, blue glass", "purpur": "calcite, amethyst"}
MAX_ROOMS = 12
ROOM_NAME_MAX = 15            # fits one line of a Minecraft sign

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "world name, 2-4 words"},
        "tagline": {"type": "string", "description": "one short evocative line shown on arrival"},
        "style": {"type": "string", "enum": list(STYLES)},
        "ground": {"type": "string", "enum": list(GROUNDS)},
        "time": {"type": "string", "enum": list(TIMES)},
        "rooms": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": f"room name, at most {ROOM_NAME_MAX} characters"},
                "theme": {"type": "string", "enum": list(THEMES)},
                "about": {"type": "string", "description": "what happens in this room, under 60 characters"},
                "profiles": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "theme", "profiles"],
        }},
    },
    "required": ["name", "style", "ground", "time", "rooms"],
}

INSTRUCTIONS = f"""You design a Minecraft building that holds someone's AI agents. Each agent is a Hermes
profile and becomes a desk (a computer with a wall of screens) in a room of the building.

Group the profiles into rooms by what they do, the way a real studio has departments: use their names
and descriptions. 2-{MAX_ROOMS} rooms, each with 1-10 profiles; every profile in exactly one room, spelled
exactly as given. Give rooms short, warm, specific names (at most {ROOM_NAME_MAX} characters, like
"Edit Bay", "Research Den", "Launch Pad"), never a raw prefix like "omo-*".

Pick one style, ground and time that fit the wish, and a different theme for neighbouring rooms.
Styles: {"; ".join(f"{k} = {v[0]} ({v[1]})" for k, v in STYLES.items())}.
Room themes: {"; ".join(f"{k} = {v}" for k, v in THEME_BLURB.items())}.
Grounds: {", ".join(GROUNDS)}. Times: {", ".join(TIMES)}.
If the wish is empty or says "be creative", surprise them with something tasteful and specific.
Answer with JSON only."""


def _clean(s: Any, limit: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f§]", "", str(s or "")).strip()
    return s[:limit].strip()


def _prefix(name: str) -> str:
    m = re.match(r"^([A-Za-z]{2,})[-_.]", name)
    return m.group(1).lower() if m else ""


FRIENDLY = {"omo": "Omo Studio", "yt": "YouTube Room", "lf": "Long-form", "vod": "VOD Room", "floor3": "Floor 3",
            "floor5": "Ops Deck"}


def fallback_plan(profiles: list[dict], wish: str = "", seed: Optional[int] = None) -> dict:
    """A plan without a model: rooms from name prefixes, look from keywords in the wish."""
    rnd = random.Random(seed)
    w = (wish or "").lower()
    style = next((k for k, kw in (("library", "librar"), ("blossom", "cherr"), ("blossom", "pink"),
                                  ("studio", "clean"), ("studio", "minimal"), ("forge", "copper"),
                                  ("forge", "industrial"), ("falls", "cave")) if kw in w), None)
    ground = next((k for k, kw in (("snow", "snow"), ("snow", "winter"), ("sand", "desert"), ("sand", "beach"),
                                   ("stone", "city"), ("stone", "plaza")) if kw in w), None)
    tod = next((k for k, kw in (("night", "night"), ("sunset", "sunset"), ("sunset", "golden"),
                                ("morning", "morning")) if kw in w), None)
    style = style or rnd.choice(list(STYLES))
    groups: dict[str, list[str]] = {}
    for p in profiles:
        groups.setdefault(_prefix(p["name"]), []).append(p["name"])
    big = {k: v for k, v in groups.items() if k and len(v) >= 3}
    rest = [n for k, v in groups.items() if k not in big for n in v]
    rooms = [{"name": FRIENDLY.get(k, k.capitalize() + " Room"), "profiles": v} for k, v in big.items()]
    if rest:
        rooms.append({"name": "Commons" if rooms else "The Studio", "profiles": rest})
    pal = STYLES[style][2] + [t for t in THEMES if t not in STYLES[style][2]]
    for i, r in enumerate(rooms):
        r["theme"] = pal[i % len(pal)]
        r["about"] = ""
    return {"name": "Hermes HQ", "tagline": "Your agents, at home.", "style": style,
            "ground": ground or "grass", "time": tod or "sunset", "rooms": rooms}


def validate(plan: Any, profiles: list[dict]) -> dict:
    """Clamp a model's plan to something buildable; raises ValueError if it is not a plan at all."""
    if not isinstance(plan, dict) or not isinstance(plan.get("rooms"), list):
        raise ValueError("not a plan")
    names = [p["name"] for p in profiles]
    known, lower = set(names), {n.lower(): n for n in names}
    seen: set[str] = set()
    rooms = []
    for r in plan["rooms"]:
        if not isinstance(r, dict):
            continue
        members = []
        for n in r.get("profiles") or []:
            n = lower.get(str(n).strip().lower(), str(n).strip())
            if n in known and n not in seen:
                seen.add(n)
                members.append(n)
        if not members:
            continue
        theme = r.get("theme") if r.get("theme") in THEMES else None
        rooms.append({"name": _clean(r.get("name"), ROOM_NAME_MAX) or "Room", "theme": theme,
                      "about": _clean(r.get("about"), 60), "profiles": members})
    rest = [n for n in names if n not in seen]
    if rest:
        rooms.append({"name": "Commons", "theme": None, "about": "everyone else", "profiles": rest})
    while len(rooms) > MAX_ROOMS:                      # merge the two smallest
        rooms.sort(key=lambda r: len(r["profiles"]))
        a, b = rooms.pop(0), rooms.pop(0)
        b["profiles"] += a["profiles"]
        rooms.append(b)
    style = plan.get("style") if plan.get("style") in STYLES else "falls"
    pal = STYLES[style][2] + [t for t in THEMES if t not in STYLES[style][2]]
    for i, r in enumerate(rooms):
        if not r["theme"]:
            r["theme"] = pal[i % len(pal)]
    order = {n: i for i, n in enumerate(names)}
    for r in rooms:
        r["profiles"].sort(key=order.get)
    return {
        "name": _clean(plan.get("name"), 48) or "Hermes HQ",
        "tagline": _clean(plan.get("tagline"), 80),
        "style": style,
        "ground": plan.get("ground") if plan.get("ground") in GROUNDS else "grass",
        "time": plan.get("time") if plan.get("time") in TIMES else "sunset",
        "rooms": rooms,
    }


def _profile_lines(profiles: list[dict]) -> str:
    out = []
    for p in profiles:
        label = _clean(p.get("label"), 100)
        out.append(f"- {p['name']}" + (f": {label}" if label else ""))
    return "\n".join(out)


def design(profiles: list[dict], wish: str, llm: Optional[Callable[..., Any]], *,
           previous: Optional[dict] = None, change: str = "") -> tuple[dict, str]:
    """(plan, source) where source is 'model' or 'fallback'. Never raises for model trouble."""
    if llm is None:
        return validate(fallback_plan(profiles, wish), profiles), "fallback"
    text = f"Wish: {wish.strip() or 'be creative'}\n\nProfiles ({len(profiles)}):\n{_profile_lines(profiles)}"
    if previous and change:
        import json
        text += f"\n\nYour previous plan:\n{json.dumps(previous)}\n\nThe user wants this changed: {change}"
    try:
        res = llm(instructions=INSTRUCTIONS, input=[{"type": "text", "text": text}], json_schema=SCHEMA,
                  schema_name="omo.world_plan", purpose="omo-space.design", temperature=0.9, max_tokens=4000)
        parsed = getattr(res, "parsed", None)
        if parsed is None:
            import json
            raw = getattr(res, "text", "") or ""
            m = re.search(r"\{.*\}", raw, re.S)
            parsed = json.loads(m.group(0)) if m else None
        return validate(parsed, profiles), "model"
    except Exception:  # noqa: BLE001 - any model/parse trouble: still give them a world
        base = previous if previous else fallback_plan(profiles, wish + " " + change)
        return validate(base, profiles), "fallback"
