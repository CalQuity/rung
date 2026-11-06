"""Run external commands with a deadline and process-group cleanup.

`subprocess.run(timeout=...)` kills only the direct child; a test runner or dev server it started
keeps running. Here every command starts in its own process group (session), and on timeout the
whole group and known descendants are killed. A process that detaches and outlives its
parent can escape ancestry-based cleanup. Output is spooled to temporary files, and
the caller receives a bounded tail.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

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
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            text=True,
            start_new_session=posix,
        )
        detached = []
        try:
            proc.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if posix:
                detached = _descendants(proc.pid)
            exc.output, exc.stderr = _tail(out), _tail(err)
            raise
        finally:
            # Files rather than pipes let us return even if a detached child holds a descriptor.
            _kill_tree(proc, posix)
            for pid in detached:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
            proc.wait()
        return subprocess.CompletedProcess(
            list(argv), proc.returncode, _tail(out).decode(errors="replace"), _tail(err).decode(errors="replace")
        )


def _tail(file: BinaryIO) -> bytes:
    end = file.seek(0, os.SEEK_END)
    file.seek(max(0, end - MAX_OUTPUT))
    return file.read(MAX_OUTPUT)


def _descendants(pid: int) -> list[int]:
    """Capture child PIDs before killing the parent, including processes that started a new session."""
    try:
        result = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    children: dict[int, list[int]] = defaultdict(list)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and all(field.isdecimal() for field in fields):
            child, parent = map(int, fields)
            children[parent].append(child)
    found = list(children[pid])
    for child in found:
        found.extend(children[child])
    return found


def _kill_tree(proc: subprocess.Popen[str], posix: bool) -> None:
    if posix:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    elif proc.poll() is None:  # pragma: no cover - Windows
        proc.kill()
