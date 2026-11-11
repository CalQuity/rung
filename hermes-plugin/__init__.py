"""Rung for Hermes: tool-backed verification evidence before a coding turn ends."""

from __future__ import annotations

import json
from pathlib import Path

from . import schemas, tools

_COMMANDS = {"kit": tools.rung_kit, "plan": tools.rung_plan, "check": tools.rung_check}


def _slash(raw_args: str) -> str:
    """`/rung [kit|plan|check]`: show the kit (default), the changed-file plan, or run the checks."""
    name = (raw_args or "kit").split()[0].lower()
    handler = _COMMANDS.get(name)
    if handler is None:
        return "Usage: /rung [kit|plan|check]"
    reply = json.loads(handler({}))
    if not reply.get("ok") and "error" in reply:
        return reply["error"]
    result = reply.get("result")
    return result if isinstance(result, str) else json.dumps(result, indent=2)


def register(ctx) -> None:
    ctx.register_tool(name="rung_check", toolset="rung", schema=schemas.RUNG_CHECK, handler=tools.rung_check)
    ctx.register_tool(name="rung_plan", toolset="rung", schema=schemas.RUNG_PLAN, handler=tools.rung_plan)
    ctx.register_tool(name="rung_kit", toolset="rung", schema=schemas.RUNG_KIT, handler=tools.rung_kit)
    ctx.register_hook("pre_verify", tools.on_pre_verify)
    ctx.register_command(
        "rung", _slash, description="Rung verification kit: kit, plan or check", args_hint="[kit|plan|check]"
    )
    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
