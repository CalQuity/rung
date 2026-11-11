"""Tool handlers and the pre_verify hook. Every verdict comes from the Rung CLI, never from this plugin."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from agent.runtime_cwd import resolve_agent_cwd

CHECK_TIMEOUT_S = 1800  # a project's test suite can be slow; rung records each check's own timing
QUICK_TIMEOUT_S = 120
MAX_OUTPUT_CHARS = 20_000
MAX_NUDGES = 2  # Hermes caps continuations too (agent.max_verify_nudges); this keeps Rung's share small


def _rung() -> list[str]:
    """The Rung CLI: the declared rung-cli dependency in Hermes's environment, else `rung` on PATH."""
    if importlib.util.find_spec("rung") is not None:
        return [sys.executable, "-m", "rung"]
    found = shutil.which("rung")
    return [found] if found else [sys.executable, "-m", "rung"]


def _run(args: list[str], cwd: Path, timeout: int, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_rung(), *args], cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
    )


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… {len(text) - MAX_OUTPUT_CHARS} more characters cut"


def _tool(args: list[str], timeout: int) -> str:
    """Run a Rung command with --json in the session's folder and return its result as a JSON string."""
    cwd = resolve_agent_cwd()
    try:
        done = _run([*args, "--json"], cwd, timeout)
    except FileNotFoundError:
        return json.dumps({"ok": False, "error": "Rung is not installed. Install it with `uv tool install rung-cli`."})
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"`rung {' '.join(args)}` ran longer than {timeout} seconds."})
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"Could not run Rung: {exc}"})
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        result = _clip(done.stdout.strip())
    reply = {"ok": done.returncode == 0, "exit_code": done.returncode, "result": result}
    if done.stderr.strip():
        reply["stderr"] = _clip(done.stderr.strip())
    return json.dumps(reply)


def rung_check(args: dict, **kwargs) -> str:
    ids = [str(item) for item in args.get("ids") or [] if str(item).strip()]
    flags = ["--changed"] if args.get("changed") else []
    return _tool(["check", *flags, *ids], CHECK_TIMEOUT_S)


def rung_plan(args: dict, **kwargs) -> str:
    return _tool(["plan", "--changed"], QUICK_TIMEOUT_S)


def rung_kit(args: dict, **kwargs) -> str:
    return _tool(["kit"], QUICK_TIMEOUT_S)


def _start_dir(changed_paths: list) -> Path:
    """The folder to look for rung.toml from: the first edited file's folder, else the session's folder."""
    base = resolve_agent_cwd()
    for item in changed_paths or []:
        path = Path(str(item))
        path = path if path.is_absolute() else base / path
        return path.parent
    return base


def on_pre_verify(coding: bool = False, attempt: int = 0, changed_paths: list | None = None, **kwargs):
    """Before a coding turn ends, ask Rung whether the edited code has fresh passing verdicts.

    Rung's stop hook answers from its ledger without running anything: exit 2 with a reason means touched
    checks lack a fresh pass (or changed paths have no check), exit 0 means the agent may finish. Anything
    unexpected lets the turn finish, so a missing Rung or a project without rung.toml never traps the agent.
    """
    if not coding:
        return None
    payload = json.dumps({"cwd": str(_start_dir(changed_paths or [])), "stop_hook_active": attempt >= MAX_NUDGES})
    try:
        done = _run(["hook", "stop"], _start_dir(changed_paths or []), QUICK_TIMEOUT_S, stdin=payload)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 2 or not done.stderr.strip():
        return None
    return {
        "action": "continue",
        "message": (
            f"{done.stderr.strip()}\n"
            "The rung_check tool runs these checks and rung_plan lists the gaps. Fix what fails and run the "
            "checks again. If a check still fails or a path stays uncovered, say so plainly in your answer "
            "with the check ids; do not report the work as verified."
        ),
    }
