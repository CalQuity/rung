"""Run external commands with a deadline that owns the whole process tree.

`subprocess.run(timeout=...)` kills only the direct child; a test runner or dev server it started
keeps running. Here every command starts in its own process group (session), and on timeout the
whole group is killed. Output is captured in memory and truncated to a bounded tail.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

MAX_OUTPUT = 2_000_000  # characters kept per stream


def run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` (no shell). Raises `subprocess.TimeoutExpired` after killing the whole process group."""
    posix = os.name == "posix"
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=posix,
    )
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc, posix)
        proc.communicate()
        raise
    finally:
        # Anything the command left behind in its group (a server it backgrounded) goes too.
        if proc.returncode is not None:
            _kill_tree(proc, posix)
    return subprocess.CompletedProcess(list(argv), proc.wait(), out[-MAX_OUTPUT:], err[-MAX_OUTPUT:])


def _kill_tree(proc: subprocess.Popen[str], posix: bool) -> None:
    if posix:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    elif proc.poll() is None:  # pragma: no cover - Windows
        proc.kill()
