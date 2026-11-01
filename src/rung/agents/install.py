"""Install guidance for coding agents: Claude Code skills, an AGENTS.md section, and the Stop hook.

Skills are installed for Claude Code under `.claude/skills/`; the AGENTS.md section is read by
Codex, Cursor and other agents. Every skill body comes from `rung.guides`, the same text Rung
sends to the model when it drafts specs and proofs itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from rung import guides
from rung.agents.hooks import HOOK_COMMAND

BEGIN, END = "<!-- rung:begin -->", "<!-- rung:end -->"

USAGE = """\
# Working with Rung

This repository proves its behaviour instead of asserting it. Rung is a CLI; this skill only
says how to use it.

## Rules

{rules}
## Commands

- `rung check --changed`: run the checks your change touches and record verdicts.
- `rung status`: `stale` means covered code or a spec changed since the last green run.
- `rung laws`: which laws hold.
- `rung prove '<test command>'`: show your new test fails without the fix and passes with it.
- `rung report`: everything at once; add `--json` to read it programmatically.
"""

SKILLS = {
    "rung": (
        "Verify changes with Rung (laws, scans, TLA+, Lean, differential tests, evidence ledger). Use before "
        "declaring any change done, when a check fails or goes stale, when fixing a bug, or when adding a "
        "dependency, law, spec or proof."
    ),
    "rung-tla": (
        "Write or fix a TLA+ spec that mirrors real code and model-checks with TLC. Use when creating or "
        "editing a .tla/.cfg file, or when a TLC counterexample needs reading."
    ),
    "rung-lean": (
        "Write or fix a core-only Lean 4 model with kernel-checked proofs and a differential-testing "
        "program. Use when creating or editing .lean files or when a Lean proof or build fails."
    ),
}


def _skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: >-\n  {description}\n---\n\n{body}"


def _write_skill(root: Path, name: str, body: str, description: str) -> Path:
    path = root / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_skill(name, description, body))
    return path


def install(root: Path, *, hooks: bool = False, lean_version: str = "the pinned version") -> list[Path]:
    rules = guides.load("rules")
    bodies = {
        "rung": USAGE.format(rules=rules),
        "rung-tla": guides.load("tla"),
        "rung-lean": guides.load("lean", lean_version=lean_version),
    }
    written = [_write_skill(root, name, bodies[name], desc) for name, desc in SKILLS.items()]

    agents = root / "AGENTS.md"
    block = f"{BEGIN}\n## Verification (Rung)\n\n{rules}{END}\n"
    text = agents.read_text() if agents.exists() else ""
    if BEGIN in text and END in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        text = head + block.rstrip("\n") + tail
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    agents.write_text(text)
    written.append(agents)

    if hooks:
        written.append(install_stop_hook(root))
    return written


def install_verify_skill(root: Path, project: str, check_id: str) -> Path:
    """A project-specific skill pointing agents at the proven drive script and its feature map."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in project.lower()).strip("-") or "app"
    description = (
        f"Drive {project} the way a user does and record evidence. Use after any user-facing change "
        "(UI, API, CLI), and before saying such a change works."
    )
    body = guides.load("verify", project=project, check_id=check_id)
    return _write_skill(root, f"verify-{slug}", body, description)


def install_stop_hook(root: Path) -> Path:
    """Add the Stop hook to `.claude/settings.json`, keeping everything already there."""
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text()) if path.exists() else {}
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    present = any(h.get("command") == HOOK_COMMAND for group in stop for h in group.get("hooks", []))
    if not present:
        stop.append({"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 120}]})
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path
