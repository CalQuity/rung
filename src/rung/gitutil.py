"""Git plumbing: changed files, merge bases, temporary worktrees, and file globbing.

Everything here degrades gracefully outside a git repository: file listing falls
back to walking the tree, and diff-based features report that they need git.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from rung.errors import RungError

# Directories never worth hashing or scanning when git cannot tell us what is ignored.
SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".lake", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".rung", "vendor",
})  # fmt: skip


class GitError(RungError):
    """A git operation failed or the directory is not a repository."""


def _git(root: Path, *args: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def is_repo(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def head(root: Path) -> str:
    try:
        return _git(root, "rev-parse", "HEAD").strip()
    except GitError:
        return ""


def merge_base(root: Path, base: str) -> str:
    """The commit where the current branch left `base`."""
    return _git(root, "merge-base", base, "HEAD").strip()


def changed_files(root: Path, base: str) -> set[str]:
    """Files that differ from the merge base with `base`, including uncommitted and untracked ones."""
    mb = merge_base(root, base)
    changed = set(_git(root, "diff", "--name-only", mb).splitlines())
    changed |= set(_git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    return {c for c in changed if c}


def show(root: Path, commit: str, path: str) -> str:
    """The contents of `path` at `commit`; raises GitError if it did not exist there."""
    return _git(root, "show", f"{commit}:{path}")


def default_base(root: Path) -> str:
    """The branch a pull request would merge into: origin's default, else main or master."""
    try:
        ref = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
        return ref.removeprefix("refs/remotes/")
    except GitError:
        pass
    for candidate in ("origin/main", "main", "origin/master", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", candidate, check=False).strip():
            return candidate
    raise GitError("cannot find a base branch; pass --base")


def list_files(root: Path) -> list[str]:
    """Every file in the project that git does not ignore, as sorted posix paths."""
    if is_repo(root):
        out = _git(root, "ls-files", "--cached", "--others", "--exclude-standard")
        return sorted({p for p in out.splitlines() if p and (root / p).is_file()})
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = Path(dirpath).relative_to(root)
        files += [(rel / f).as_posix() for f in filenames]
    return sorted(files)


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob with `**` support into an anchored regex over posix paths."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def expand(root: Path, patterns: list[str]) -> list[str]:
    """Resolve glob patterns against the project's files.

    A pattern that matches nothing is kept literally, so a deleted file shows up
    as missing instead of silently dropping out of a verdict's coverage.
    """
    if not patterns:
        return []
    files = list_files(root)
    out: set[str] = set()
    for pattern in patterns:
        matches = [f for f in files if glob_match(pattern, f)]
        out.update(matches or [pattern])
    return sorted(out)


@contextlib.contextmanager
def worktree(root: Path, ref: str) -> Iterator[Path]:
    """Check out `ref` in a temporary worktree and remove it afterwards."""
    tmp = Path(tempfile.mkdtemp(prefix="rung-base-"))
    target = tmp / "tree"
    _git(root, "worktree", "add", "--detach", str(target), ref)
    try:
        yield target
    finally:
        _git(root, "worktree", "remove", "--force", str(target), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def user_name(root: Path) -> str:
    try:
        return _git(root, "config", "user.name").strip()
    except GitError:
        return ""
