"""Tool schemas (what the model reads) for toolset ``omo``."""

OMO_STATUS = {
    "name": "omo_status",
    "description": (
        "Check Omo Space, the Omo Research Minecraft world where this Hermes setup is shown as a "
        "building of Omo Computers. Read-only: asks the local Omo bridge (loopback only) whether it "
        "is running and returns a compact JSON summary: bridge up/down and version, how many "
        "workspaces exist and how many are live or busy, which workspace this conversation lives in "
        "(or 'elsewhere'), and where this profile's activity feed is written. Use it when the user asks "
        "about Omo Space, the Hermes Board, or whether the in-game desks can see this agent. Takes no "
        "arguments and never changes anything."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

OMO_NOTIFY = {
    "name": "omo_notify",
    "description": (
        "Show a short notice to the user inside Omo Space (the Minecraft world), for example when a long "
        "task has finished or needs their attention while they are in-game. Queues one notify event in "
        "this profile's local feed; the Omo bridge picks it up if it is running. Text is capped at 140 "
        "characters, control characters are stripped and anything that looks like a secret is masked, "
        "so keep it to a plain one-line status. Do not use it for routine progress chatter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "maxLength": 140,
                "description": "One-line message to show, e.g. 'Build finished: 214 tests passed'.",
            },
            "level": {
                "type": "string",
                "enum": ["info", "success", "warn"],
                "description": "Notice style. Default 'info'; 'success' for completions, 'warn' when the user is needed.",
            },
        },
        "required": ["text"],
    },
}

OMO_CREATE_WORLD = {
    "name": "omo_create_world",
    "description": (
        "Design and build a new Minecraft world for the user's Hermes agents: every Hermes profile gets a desk "
        "with a wall of screens, grouped into rooms named for what the agents do, with a name, look and "
        "time of day that fit the wish. Writes ONE new world folder (Omo Space's saves if installed, else the "
        "Minecraft launcher's) and never touches existing worlds. Use it when the user asks for a world, an "
        "office, an HQ or a building for their agents. Takes about 30 seconds (the design uses the model). "
        "Returns the world name, where it was saved and the rooms."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wish": {"type": "string", "maxLength": 300,
                     "description": "What kind of world, in the user's words; empty = be creative."},
            "name": {"type": "string", "maxLength": 48, "description": "World name, only if the user gave one."},
            "game": {"type": "string", "enum": ["auto", "omo", "minecraft"],
                     "description": "omo = Omo Space (live screens), minecraft = plain Minecraft 26.2. Default auto."},
        },
        "required": [],
    },
}
