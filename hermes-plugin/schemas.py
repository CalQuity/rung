"""Tool schemas: what the model reads to decide when to call each Rung tool."""

RUNG_CHECK = {
    "name": "rung_check",
    "description": (
        "Run the project's recorded checks (tests, linters, type checkers, security scanners, runtime and "
        "formal checks listed in rung.toml) and record a verdict for the exact code they ran against. "
        "Use it after editing code and before saying the work is done. Each result is passed, failed or "
        "errored, with the command's output; a verdict comes from the tool, never from a model. "
        "Pass ids to run specific checks, or changed=true for only the checks the branch touches."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Check ids from rung.toml to run (default: all checks).",
            },
            "changed": {
                "type": "boolean",
                "description": "Run only the checks touched by this branch's changes.",
            },
        },
        "required": [],
    },
}

RUNG_PLAN = {
    "name": "rung_plan",
    "description": (
        "List which checks cover the files changed on this branch and which changed paths no check "
        "covers. Use it to find verification gaps before finishing, then report them to the user."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RUNG_MAP = {
    "name": "rung_map",
    "description": (
        "Draft and show the project's feature map: the user-visible features and sub-features of the app, "
        "each with a status the person decides (approved, not built, dropped). Approved features are what "
        "Rung's app-driving scripts exercise with scenarios and faults. The first call drafts the map with "
        "the model configured in rung.toml; redraft=true refreshes it from the code (changed features need "
        "approval again). You cannot approve features: ask the user to run /rung approve <ids> in chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "redraft": {"type": "boolean", "description": "Refresh the map from the current code."},
            "intent": {
                "type": "string",
                "description": "What to focus the map on, e.g. 'checkout and coupons'.",
            },
        },
        "required": [],
    },
}

RUNG_KIT = {
    "name": "rung_kit",
    "description": (
        "Show the project's verification kit: every recorded check, whether its latest verdict is fresh "
        "for the current code, what is missing, and the command that adds it."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}
