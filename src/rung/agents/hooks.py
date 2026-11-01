"""The Claude Code `Stop` hook: an agent may not finish while its edits are unverified.

Claude Code runs `rung hook stop` when the agent tries to end its turn and sends a JSON
payload on stdin (`cwd`, `stop_hook_active`, ...). Exit code 2 blocks the stop and shows
stderr to the agent as the reason to continue. The hook fails open on anything unexpected,
and allows the stop when `stop_hook_active` is set, so it can never trap an agent in a loop.
See https://code.claude.com/docs/en/hooks.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from rung.checks import runner
from rung.core import config, git

HOOK_COMMAND = "rung hook stop"
BLOCK = 2


def stop_hook(cwd: Path, payload: str) -> tuple[int, str]:
    """Decide whether an agent may stop. Returns (exit code, message for the agent)."""
    try:
        data = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("stop_hook_active"):
        return 0, ""  # already blocked once this turn; do not loop
    try:
        root = config.find_root(cwd)
        cfg = config.load(root)
        # Compare with where the branch left its base, so committing the edits does not hide them.
        try:
            changed = git.changed_files(root, git.default_base(root))
        except git.GitError:
            changed = git.changed_files(root, "HEAD")
    except Exception:
        return 0, ""
    if not changed:
        return 0, ""
    pending = [c for c in runner.select(cfg, [], changed) if not runner.status(cfg, c).trusted]
    if not pending:
        return 0, ""
    ids = " ".join(c.id for c in pending)
    return BLOCK, (
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
