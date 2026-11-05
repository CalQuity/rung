"""A guarded disposable project copy for model-written checks and mutation trials.

Model-written test code runs before any person has read it, and mutation testing edits source files.
Neither should touch the developer's working tree, so both happen in a copy:

- Git supplies tracked and non-ignored inputs. Without Git, Rung walks the tree and excludes
  conventional local environment files;
- dependency directories (`node_modules`, `.venv`) are linked rather than copied, since they are
  large and only read;
- the subprocess inherits an allowlist of tool discovery and locale settings.

The operating system guard confines writes and masks common credential paths. Other host files
remain readable to support toolchains, and dependency-installing runs can reach the network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rung.core import git, proc
from rung.core.config import managed_path
from rung.core.errors import ToolMissing

DEPENDENCY_DIRS = ("node_modules", ".venv", "venv", ".lake")
KEEP = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL", "PLAYWRIGHT_BROWSERS_PATH"}
CREDENTIAL_PATHS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".config/gcloud",
    ".config/gh",
    ".azure",
    ".docker",
    ".codex",
    ".claude",
    ".npmrc",
    ".netrc",
    ".git-credentials",
)


@contextmanager
def copy_of(root: Path) -> Iterator[Path]:
    """Yield a temporary copy of the project at `root`; it is deleted afterwards."""
    root = root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="rung-sandbox-"))
    copy = tmp / root.name
    try:
        copy.mkdir()
        dirs: set[Path] = {Path(".")}
        has_git = git.is_repo(root)
        for rel in git.list_files(root):
            if git.is_rung_output(rel):
                continue
            source, target = root / rel, copy / rel
            if not has_git and _local_environment_file(source.name):
                continue
            resolved = source.resolve()
            if not resolved.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.path.relpath(copy / resolved.relative_to(root), target.parent))
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


def _local_environment_file(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))


def retain_evidence(root: Path, trial: Path, report: str) -> None:
    """Copy a run's generated report and ordinary files out of the disposable tree."""
    relative = Path(report).parent
    source = managed_path(trial, relative)
    target = managed_path(root, relative)

    def ignore_unsafe(directory: str, names: list[str]) -> list[str]:
        return [
            name for name in names if (path := Path(directory) / name).is_symlink()
            or not (path.is_dir() or path.is_file())
        ]  # fmt: skip

    shutil.copytree(source, target, ignore=ignore_unsafe)


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Inherit only tool discovery, locale and terminal settings."""
    env = {k: v for k, v in os.environ.items() if k in KEEP}
    return env | (extra or {})


def write_guard(root: Path, *, network: bool = False) -> tuple[str, ...]:
    """Limit draft subprocess writes to the disposable tree. Use loopback by default."""
    root = root.resolve()
    private = [Path.home().resolve() / relative for relative in CREDENTIAL_PATHS]
    seatbelt = Path("/usr/bin/sandbox-exec")
    if sys.platform == "darwin" and seatbelt.is_file():
        profile = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            f'(allow file-write* (subpath {json.dumps(str(root), ensure_ascii=False)}) (literal "/dev/null"))',
        ]
        profile += [f"(deny file-read* (subpath {json.dumps(str(path), ensure_ascii=False)}))" for path in private]
        if not network:
            profile += [
                "(deny network*)",
                '(allow network-bind (local ip "localhost:*"))',
                '(allow network-inbound (local ip "localhost:*"))',
                '(allow network-outbound (remote ip "localhost:*"))',
            ]
        return (str(seatbelt), "-p", "\n".join(profile))
    if sys.platform.startswith("linux") and (bubblewrap := shutil.which("bwrap")):
        mask = []
        for path in private:
            if path.is_dir():
                mask += ["--tmpfs", str(path)]
            elif path.is_file():
                mask += ["--ro-bind", "/dev/null", str(path)]
        return (
            bubblewrap,
            "--die-with-parent",
            "--unshare-all",
            *(("--share-net",) if network else ()),
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            *mask,
            "--bind",
            str(root),
            str(root),
            "--",
        )
    raise ToolMissing("guarded execution needs sandbox-exec on macOS or Bubblewrap on Linux")


def validated_write_guard(root: Path, *, network: bool = False) -> tuple[str, ...]:
    """Verify the OS can start a guarded process before running a check."""
    prefix = write_guard(root, network=network)
    try:
        result = proc.run([*prefix, "/usr/bin/true"], cwd=root.resolve(), env=scrubbed_env(), timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode:
        raise ToolMissing(
            "The draft write guard could not start. Check sandbox-exec or Bubblewrap and user namespaces."
        )
    return prefix


def probe_write_guard(root: Path) -> str:
    return validated_write_guard(root)[0]


def trial_environment(root: Path) -> dict[str, str]:
    temp = root / ".rung" / "cache" / "trial"
    temp.mkdir(parents=True, exist_ok=True)
    return scrubbed_env(
        {
            "TMPDIR": str(temp),
            "UV_CACHE_DIR": str(temp / "uv-cache"),
            "XDG_CACHE_HOME": str(temp / "cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
