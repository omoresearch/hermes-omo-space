"""Tool schemas (what the model reads) for toolset ``omo``."""

OMO_STATUS = {
    "name": "omo_status",
    "description": (
        "Check Omo Space, the Omo Research Minecraft world where this Hermes setup is shown as a "
        "building of Hermes Computers. Read-only: asks the local Omo bridge (loopback only) whether it "
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
        "Show a short toast to the user inside Omo Space (the Minecraft world), for example when a long "
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
                "description": "Toast style. Default 'info'; 'success' for completions, 'warn' when the user is needed.",
            },
        },
        "required": ["text"],
    },
}
