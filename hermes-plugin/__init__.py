"""Rung for Hermes: tool-backed verification evidence before a coding turn ends."""

from __future__ import annotations

from pathlib import Path

from . import schemas, tools

USAGE = (
    "Usage: /rung [kit|plan|check|map]\n"
    "       /rung approve <feature ids|all>   /rung not-built <ids>   /rung drop <ids>"
)
# Only a person types slash commands, so feature decisions live here and never in a tool the model can call.
_DECISIONS = {"approve": "--approve", "not-built": "--not-built", "drop": "--drop"}
_VIEWS = {
    "kit": (["kit"], tools.QUICK_TIMEOUT_S),
    "plan": (["plan", "--changed"], tools.QUICK_TIMEOUT_S),
    "check": (["check"], tools.CHECK_TIMEOUT_S),
    "map": (["map", "--brief"], tools.CHECK_TIMEOUT_S),
}


def _slash(raw_args: str) -> str:
    """`/rung ...`: the kit (default), plan, checks or feature map, and the person's feature decisions."""
    words = (raw_args or "kit").split()
    name, rest = words[0].lower(), words[1:]
    if name in _VIEWS and not rest:
        args, timeout = _VIEWS[name]
        return tools.text(args, timeout)
    if name in _DECISIONS and rest:
        return tools.text(["map", "--brief", _DECISIONS[name], *rest])
    return USAGE


def register(ctx) -> None:
    ctx.register_tool(name="rung_check", toolset="rung", schema=schemas.RUNG_CHECK, handler=tools.rung_check)
    ctx.register_tool(name="rung_plan", toolset="rung", schema=schemas.RUNG_PLAN, handler=tools.rung_plan)
    ctx.register_tool(name="rung_kit", toolset="rung", schema=schemas.RUNG_KIT, handler=tools.rung_kit)
    ctx.register_tool(name="rung_map", toolset="rung", schema=schemas.RUNG_MAP, handler=tools.rung_map)
    ctx.register_hook("pre_verify", tools.on_pre_verify)
    ctx.register_command(
        "rung",
        _slash,
        description="Rung: kit, plan, check, feature map, and your feature decisions",
        args_hint="[kit|plan|check|map|approve <ids>|not-built <ids>|drop <ids>]",
    )
    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
