"""The Claude Code and Codex Stop hook asks for a verification pass before finishing.

Claude Code runs `rung hook stop` when the agent tries to end its turn and sends a JSON
payload on stdin (`cwd`, `stop_hook_active`, ...). Exit code 2 blocks the stop and shows
stderr to the agent as the reason to continue. The hook fails open on anything unexpected,
and allows the stop when `stop_hook_active` is set, so it can never trap an agent in a loop.
See https://code.claude.com/docs/en/hooks.

Codex may not send `stop_hook_active`, so for Codex the hook also keeps a small marker per session
under `.rung/cache/hooks/`: after one block, the next consecutive stop of that session is allowed.

In a workspace, every package's suite has a repo-wide scope, so the hook asks first for the checks
whose declared `verifies` targets hold every changed source file, plus checks with no declared target.
Checks verifying other targets are left to `rung check --changed` and the merge gate. A change to shared
code outside every declared target, or to rung.toml, asks for the full selection.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

from rung.analysis.plan import focused_selection, uncovered_paths
from rung.analysis.survey import LANGUAGES
from rung.checks import runner
from rung.core import config, git, ledger

HOOK_COMMAND = "rung hook stop"
CODEX_HOOK_COMMAND = "rung hook stop --agent codex"
BLOCK = 2


def _marker(root: Path, data: dict) -> Path:
    """The per-session file that records a block, for agents that do not send `stop_hook_active`."""
    session = next((str(data[key]) for key in ("session_id", "transcript_path") if data.get(key)), "default")
    name = hashlib.sha256(session.encode()).hexdigest()[:16]
    return root / ".rung" / "cache" / "hooks" / f"stop-{name}"


def _narrowed(cfg: config.Config, pending: list[config.Check], changed: set[str]) -> tuple[list, list]:
    """(checks to ask for now, checks verifying other declared targets that the gate runs instead)."""
    code = {path for path in changed if Path(path).suffix in LANGUAGES and not git.is_rung_output(path)}
    focused = focused_selection(cfg.checks, code)
    targeted = [check for check in focused if "verifies" in check.options]
    held = all(any(git.glob_match(g, path) for c in targeted for g in c.get_list("verifies")) for path in code)
    if config.CONFIG_NAME in changed or not targeted or not held:
        return pending, []
    deferred = [check for check in pending if "verifies" in check.options and check not in focused]
    return [check for check in pending if check not in deferred], deferred


def stop_hook(cwd: Path, payload: str, *, agent: str = "claude") -> tuple[int, str]:
    """Decide whether an agent may stop. Returns (exit code, message for the agent)."""
    try:
        data = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return 0, ""
    marker: Path | None = None
    if agent == "codex":
        try:
            marker = _marker(config.find_root(cwd), data)
        except Exception:
            marker = None
    if data.get("stop_hook_active"):
        _clear(marker)
        return 0, ""  # already blocked once this turn; do not loop
    if marker is not None and marker.exists():
        _clear(marker)  # blocked on the previous attempt of this session: let it stop now
        return 0, ""
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
        pending, deferred = _narrowed(cfg, pending, changed)
        uncovered = uncovered_paths(cfg.checks, {path for path in changed if Path(path).suffix in LANGUAGES})
    except Exception:
        return 0, ""
    if not pending and not uncovered:
        return 0, ""
    messages = []
    if pending:
        ids = " ".join(c.id for c in pending)
        messages.append(f"Rung: run `rung check {ids}` for touched checks without fresh passing verdicts.")
    if deferred:
        messages.append(f"Rung: {len(deferred)} check(s) for other declared targets run in `rung check --changed` "
                        "and the merge gate.")  # fmt: skip
    if uncovered:
        messages.append(f"Rung: {len(uncovered)} changed path(s) have no command, runtime or formal check scope.")
        messages.append("Run `rung plan --changed --json` and report any remaining verification gaps.")
    if marker is not None:
        with contextlib.suppress(OSError):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("blocked\n")
    return BLOCK, "\n".join(messages)


def _clear(marker: Path | None) -> None:
    if marker is not None:
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


def main_stop_hook(*, agent: str = "claude") -> int:
    payload = sys.stdin.read() if not sys.stdin.isatty() else ""
    # Both agents send cwd. Claude Code also exports CLAUDE_PROJECT_DIR.
    try:
        where = json.loads(payload).get("cwd") if payload.strip() else None
    except (json.JSONDecodeError, AttributeError):
        where = None
    fallback = os.environ.get("CLAUDE_PROJECT_DIR") if agent == "claude" else None
    start = Path(where if isinstance(where, str) and where else fallback or Path.cwd())
    code, message = stop_hook(start, payload, agent=agent)
    if agent == "codex":
        print(json.dumps({"decision": "block", "reason": message} if code == BLOCK else {}))
        return 0
    if message:
        print(message, file=sys.stderr)
    return code
