"""A disposable copy of a project, for running model-written tests and deliberate bugs.

Model-written test code runs before any person has read it, and mutation testing edits source files.
Neither should touch the developer's working tree, so both happen in a copy:

- every file git would show (tracked, plus untracked files that are not ignored) is copied, so
  ignored local files such as `.env` are not there;
- dependency directories (`node_modules`, `.venv`) are linked rather than copied, since they are
  large and only read;
- environment variables that look like credentials are removed for the run.

This is damage control, not a security sandbox: a test can still reach the network and any path it
names explicitly. Read a drafted test before you trust it.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rung.core import git

DEPENDENCY_DIRS = ("node_modules", ".venv", "venv", ".lake")
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|SESSION|COOKIE|AUTH", re.I)
KEEP = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL", "UV_CACHE_DIR", "XDG_CACHE_HOME"}


@contextmanager
def copy_of(root: Path) -> Iterator[Path]:
    """Yield a temporary copy of the project at `root`; it is deleted afterwards."""
    tmp = Path(tempfile.mkdtemp(prefix="rung-sandbox-"))
    copy = tmp / root.name
    try:
        dirs: set[Path] = {Path(".")}
        for rel in git.list_files(root):
            if git.is_rung_output(rel):
                continue
            source, target = root / rel, copy / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, target)
            dirs.update(Path(rel).parents)
        for d in dirs:
            for name in DEPENDENCY_DIRS:
                if (root / d / name).is_dir() and not (copy / d / name).exists():
                    (copy / d).mkdir(parents=True, exist_ok=True)
                    (copy / d / name).symlink_to(root / d / name, target_is_directory=True)
        yield copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The current environment without variables whose names look like credentials."""
    env = {k: v for k, v in os.environ.items() if k in KEEP or not SECRET_NAME.search(k)}
    return env | (extra or {})
