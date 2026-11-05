"""The Claude Code and Codex Stop hook asks for a verification pass before finishing.

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

from rung.analysis.plan import uncovered_paths
from rung.analysis.survey import LANGUAGES
from rung.checks import runner
from rung.core import config, git, ledger

HOOK_COMMAND = "rung hook stop"
CODEX_HOOK_COMMAND = "rung hook stop --agent codex"
BLOCK = 2


def stop_hook(cwd: Path, payload: str) -> tuple[int, str]:
    """Decide whether an agent may stop. Returns (exit code, message for the agent)."""
    try:
        data = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or data.get("stop_hook_active"):
        return 0, ""  # already blocked once this turn; do not loop
    try:
        root = config.find_root(cwd)
        cfg = config.load(root)
        # Compare with where the branch left its base, so committing the edits does not hide them.
        try:
            changed = git.changed_files(root, git.default_base(root))
        except git.GitError:
            changed = git.changed_files(root, "HEAD")
        if not changed:
            return 0, ""
        view = ledger.View(root)
        pending = [
            check
            for check in cfg.checks
            if (config.CONFIG_NAME in changed or runner.touched_by(check, changed))
            and not runner.status(cfg, check, view=view).trusted
        ]
        uncovered = uncovered_paths(cfg.checks, {path for path in changed if Path(path).suffix in LANGUAGES})
    except Exception:
        return 0, ""
    if not pending and not uncovered:
        return 0, ""
    messages = []
    if pending:
        ids = " ".join(c.id for c in pending)
        messages.append(f"Rung: run `rung check {ids}` for touched checks without fresh passing verdicts.")
    if uncovered:
        messages.append(f"Rung: {len(uncovered)} changed path(s) have no command, runtime or formal check scope.")
        messages.append("Run `rung plan --changed --json` and report any remaining verification gaps.")
    return BLOCK, "\n".join(messages)


def main_stop_hook(*, agent: str = "claude") -> int:
    payload = sys.stdin.read() if not sys.stdin.isatty() else ""
    # Both agents send cwd. Claude Code also exports CLAUDE_PROJECT_DIR.
    try:
        where = json.loads(payload).get("cwd") if payload.strip() else None
    except (json.JSONDecodeError, AttributeError):
        where = None
    fallback = os.environ.get("CLAUDE_PROJECT_DIR") if agent == "claude" else None
    start = Path(where if isinstance(where, str) and where else fallback or Path.cwd())
    code, message = stop_hook(start, payload)
    if agent == "codex":
        print(json.dumps({"decision": "block", "reason": message} if code == BLOCK else {}))
        return 0
    if message:
        print(message, file=sys.stderr)
    return code
