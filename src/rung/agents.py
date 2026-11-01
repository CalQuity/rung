"""Make coding agents work inside the ladder instead of around it.

Two layers. Guidance: a Claude Code skill and an AGENTS.md section (read by
Codex, Cursor and others) that say how to use `rung` and which shortcuts are
off limits. Enforcement: a Claude Code `Stop` hook that runs `rung hook stop`
when the agent tries to finish; if the checks covering its edits are not fresh,
the agent is told which `rung check` to run and cannot stop until it has.
Guidance can be ignored; the hook cannot.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from rung import checks, config, gitutil

BEGIN, END = "<!-- rung:begin -->", "<!-- rung:end -->"
HOOK_COMMAND = "rung hook stop"

RULES = """\
- Before saying a change is done, run `rung check --changed` and read the result.
  "The build passed" is not evidence; a fresh verdict in the ledger is.
- A TLC counterexample is either a bug in the code or a wrong spec. Fix the code.
  Only change a spec or an invariant when a person has agreed the spec was wrong.
- Never weaken a law, an invariant, a theorem, a property or a test to make it pass.
  Laws in LAWS.md that are `approved` are not yours to edit. Never add a waiver.
- Never use `sorry`, `admit`, `axiom`, `native_decide`, `unsafe`, `implemented_by`
  or `extern` in Lean. Rung rejects them, and the rejection is correct.
- When you fix a bug, prove the test catches it: `rung prove '<test command>'`.
- New invariants go into LAWS.md as `status: proposed`, one plain sentence each.
- Do not add a dependency without checking it exists and is established;
  `rung check` vets new packages against their registry.
"""

SKILL = f"""---
name: rung
description: >-
  Verify changes with Rung (laws, scans, TLA+, Lean, differential tests, evidence
  ledger). Use before declaring any change done, when a check fails or goes stale,
  when fixing a bug, or when adding a dependency, law, spec or proof.
---

# Working with Rung

This repository proves its behaviour instead of asserting it. Rung is a CLI; this
skill only tells you how to use it.

## Rules

{RULES}
## Commands

- `rung check --changed`: run the checks your change touches and record verdicts.
- `rung status`: `stale` means covered code or a spec changed since the last green run.
- `rung laws`: which laws hold.
- `rung prove '<test command>'`: show your new test fails without the fix and passes with it.
- `rung report`: everything at once; add `--json` to read it programmatically.
"""


VERIFY_SKILL = """---
name: verify-{slug}
description: >-
  Drive {project} the way a user does and record evidence. Use after any user-facing
  change (UI, API, CLI), and before saying such a change works.
---

# Verify {project}

`.rung/verify/drive.py` launches the app, drives a feature end to end and records
evidence. `.rung/verify/FEATURES.md` maps each feature to how a user reaches it, how
to drive it, and what observable state proves it works.

- Run `rung check {check_id}`. A pass means the script ran and left evidence; the
  evidence paths are in the output and the ledger.
- Changed a feature the script does not drive yet? Extend `drive.py` and FEATURES.md,
  then run `rung check {check_id}` again. Never weaken what the script asserts.
- A failure is a product bug until shown otherwise. Fix the app, not the script.
"""


def install(root: Path, *, hooks: bool = False) -> list[Path]:
    written = []
    skill_path = root / ".claude" / "skills" / "rung" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(SKILL)
    written.append(skill_path)

    agents = root / "AGENTS.md"
    block = f"{BEGIN}\n## Verification (Rung)\n\n{RULES}{END}\n"
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
    path = root / ".claude" / "skills" / f"verify-{slug}" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(VERIFY_SKILL.format(slug=slug, project=project, check_id=check_id))
    return path


def install_stop_hook(root: Path) -> Path:
    """Add the Stop hook to `.claude/settings.json`, keeping everything already there."""
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text()) if path.exists() else {}
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    present = any(h.get("command") == HOOK_COMMAND for group in stop for h in group.get("hooks", []))
    if not present:
        stop.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def stop_hook(cwd: Path, payload: str) -> tuple[int, str]:
    """Decide whether an agent may stop. Returns (exit code, message for the agent).

    Exit 2 blocks the stop and shows the message to the agent. The hook fails
    open on anything unexpected: a broken hook must never trap an agent.
    """
    try:
        data = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("stop_hook_active"):
        return 0, ""  # already blocked once this turn; do not loop
    try:
        root = config.find_root(cwd)
        cfg = config.load(root)
        changed = gitutil.changed_files(root, "HEAD")
    except Exception:
        return 0, ""
    if not changed:
        return 0, ""
    pending = [c for c in checks.select(cfg, [], changed) if not checks.status(cfg, c).trusted]
    if not pending:
        return 0, ""
    ids = " ".join(c.id for c in pending)
    return 2, (
        f"Rung: your edits touch checks that are not freshly passing: {ids}.\n"
        f"Run `rung check {ids}` and fix what fails before finishing. "
        "If a check cannot pass for a reason outside this task, say so explicitly."
    )


def main_stop_hook() -> int:
    payload = sys.stdin.read() if not sys.stdin.isatty() else ""
    # Claude Code sends the session's directory in the payload and exports CLAUDE_PROJECT_DIR.
    try:
        where = json.loads(payload).get("cwd") if payload.strip() else None
    except (json.JSONDecodeError, AttributeError):
        where = None
    start = Path(where or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    code, message = stop_hook(start, payload)
    if message:
        print(message, file=sys.stderr)
    return code
