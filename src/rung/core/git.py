"""Git plumbing: changed files, merge bases, temporary worktrees, and file globbing.

All paths are POSIX paths relative to the project root (the directory holding `rung.toml`),
which may be a subdirectory of the git repository. Git is always asked for NUL-delimited
output, so names with non-ASCII characters, tabs or newlines are never mangled.

Outside a git repository, file listing falls back to walking the tree, and diff-based
features report that they need git.
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

from rung.core.errors import RungError

GIT_TIMEOUT = 120

# Directories that are build output or dependencies. Untracked files inside them never count as
# project files; tracked files always do (a repository may track its own `build/` sources).
SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".lake", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor",
})  # fmt: skip

# Rung's own outputs. Writing a verdict or recording evidence must never stale another verdict.
# Verification inputs under `.rung/` (drive scripts, baselines, waivers) are not listed here.
RUNG_OUTPUTS = (".rung/ledger.jsonl", ".rung/ledger.lock", ".rung/evidence/", ".rung/cache/", ".rung/runs/")
# Files whose meaning reaches a verdict another way: each verdict hashes its check's declaration.
RUNG_STATE_FILES = frozenset({"rung.toml", "LAWS.md"})


class GitError(RungError):
    """A git operation failed or the directory is not a repository."""


def _git(root: Path, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _paths(output: str) -> list[str]:
    return [p for p in output.split("\0") if p]


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


def prefix(root: Path) -> str:
    """Where `root` sits inside its repository, e.g. `backend/` (empty at the top)."""
    return _git(root, "rev-parse", "--show-prefix").strip()


def merge_base(root: Path, base: str) -> str:
    """The commit where the current branch left `base`."""
    return _git(root, "merge-base", base, "HEAD").strip()


def changed_files(root: Path, base: str) -> set[str]:
    """Files under `root` that differ from the merge base with `base`, including uncommitted and untracked ones."""
    mb = merge_base(root, base)
    changed = set(_paths(_git(root, "diff", "--name-only", "-z", "--relative", mb)))
    changed |= set(_paths(_git(root, "ls-files", "-z", "--others", "--exclude-standard")))
    return changed


def show(root: Path, commit: str, path: str) -> str:
    """The contents of `path` (relative to `root`) at `commit`; raises GitError if it did not exist there."""
    return _git(root, "show", f"{commit}:./{path}")


def files_at(root: Path, commit: str) -> list[str]:
    """Files under `root` as they were at `commit`."""
    return _paths(_git(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", "."))


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
    """Every project file git does not ignore: all tracked files, plus untracked ones outside build dirs."""
    if is_repo(root):
        tracked = _paths(_git(root, "ls-files", "-z", "--cached"))
        untracked = [
            p for p in _paths(_git(root, "ls-files", "-z", "--others", "--exclude-standard"))
            if not SKIP_DIRS.intersection(p.split("/"))
        ]  # fmt: skip
        return sorted({p for p in tracked + untracked if (root / p).is_file()})
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != ".git"]
        rel = Path(dirpath).relative_to(root)
        files += [(rel / f).as_posix() for f in filenames]
    return sorted(files)


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into an anchored regex over POSIX paths.

    Supports `*` (within one directory), `**` (across directories), `?` and `[...]` classes
    (`[!...]` negates). Everything else matches literally.
    """
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
        elif c == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            negate = body.startswith("!")
            body = re.escape(body[1:] if negate else body).replace("\\-", "-")
            out.append(f"[^/{body}]" if negate else f"[{body}]")
            i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def is_rung_output(path: str) -> bool:
    return path in RUNG_STATE_FILES or any(path == o or path.startswith(o) for o in RUNG_OUTPUTS)


def expand(root: Path, patterns: list[str]) -> list[str]:
    """Resolve glob patterns against the project's files.

    A pattern that matches nothing is kept literally, so a deleted file shows up
    as missing instead of silently dropping out of a verdict's coverage.
    """
    if not patterns:
        return []
    files = [f for f in list_files(root) if not is_rung_output(f)]
    out: set[str] = set()
    for pattern in patterns:
        matches = [f for f in files if glob_match(pattern, f)]
        out.update(matches or [pattern])
    return sorted(out)


@contextlib.contextmanager
def worktree(root: Path, ref: str) -> Iterator[Path]:
    """Check out `ref` in a temporary worktree and yield the same project directory inside it."""
    tmp = Path(tempfile.mkdtemp(prefix="rung-base-"))
    target = tmp / "tree"
    try:
        sub = prefix(root)
        _git(root, "worktree", "add", "--detach", str(target), ref, timeout=600)
        yield target / sub if sub else target
    finally:
        if target.exists():
            _git(root, "worktree", "remove", "--force", str(target), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def user_name(root: Path) -> str:
    try:
        return _git(root, "config", "user.name").strip()
    except GitError:
        return ""
