"""Static launch inputs shared by doctor, policy protection and local reuse."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Delegation:
    argv: list[str]
    cwd: Path
    files: tuple[Path, ...] = ()
    host_path: bool = False


_UV_FILES = {"--config-file", "--env-file", "--with-requirements"}
_UV_VALUES = _UV_FILES | {
    "--directory", "--project", "--package", "--python", "-p", "--with", "-w", "--with-editable",
    "--extra", "--no-extra", "--group", "--no-group", "--only-group", "--index", "--default-index",
    "--index-url", "-i", "--extra-index-url", "--find-links", "-f", "--cache-dir", "--python-platform",
    "--resolution", "--prerelease", "--fork-strategy", "--exclude-newer", "--exclude-newer-package",
    "--allow-insecure-host", "--index-strategy", "--keyring-provider", "--link-mode", "--no-editable-package",
    "--reinstall-package", "--refresh-package", "--no-build-package", "--no-binary-package", "--color",
    "--upgrade-package", "-P", "--upgrade-group", "--prerelease-package", "--no-sources-package",
    "--config-setting", "-C", "--config-settings-package", "--no-build-isolation-package",
}  # fmt: skip


def delegated(argv: list[str], cwd: Path) -> Delegation | None:
    """Unwrap supported static package runner commands without executing their tool."""
    if not argv:
        return None
    name = Path(argv[0]).name
    if name in {"poetry", "pipenv", "pdm", "rye", "hatch"} and argv[1:2] == ["run"]:
        args = argv[3:] if argv[2:3] == ["--"] else argv[2:]
        return Delegation(args, cwd) if args else None
    if name not in {"uv", "uv.exe"}:
        return None

    tokens = iter(argv[1:])
    running = module = script = no_project = managed = False
    directory = cwd
    files: list[str] = []
    for arg in tokens:
        flag, equals, value = arg.partition("=")
        if flag in _UV_VALUES:
            value = value if equals else next(tokens, "")
            if flag == "--directory" and value:
                directory = cwd / value
            if flag in _UV_FILES and value:
                files.append(value)
            managed |= flag in {
                "--env-file",
                "--with",
                "-w",
                "--with-editable",
                "--with-requirements",
                "--python",
                "-p",
            }
            continue
        if flag in {"--isolated", "--active"} or (arg.startswith("-w") and len(arg) > 2):
            managed = True
        if running and flag in {"-m", "--module"}:
            module = True
        elif running and flag in {"-s", "--script", "--gui-script"}:
            script = True
        elif running and flag == "--no-project":
            no_project = True
        elif running and (flag == "--" or not flag.startswith("-")):
            inner = list(tokens) if flag == "--" else [arg, *tokens]
            if not inner:
                return None
            if module:
                inner = ["python", "-m", *inner]
            elif script or Path(inner[0]).suffix.lower() in {".py", ".pyw"}:
                inner = ["python", *inner]
                managed = True
            return Delegation(inner, directory, tuple(directory / f for f in files), no_project and not managed)
        elif not running and flag == "run":
            running = True
        elif not flag.startswith("-"):
            return None
    return None


def file_inputs(argv: list[str], cwd: Path) -> list[Path]:
    """Project files named by a direct launcher or supported package runner."""
    if not argv:
        return []
    files = [cwd / argv[0]] if os.path.dirname(argv[0]) else []
    if inner := delegated(argv, cwd):
        return [*files, *inner.files, *file_inputs(inner.argv, inner.cwd)]
    executable = Path(argv[0]).name
    python = bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", executable))
    if not python and executable not in {"node", "bash", "sh", "zsh"}:
        return files
    for index, arg in enumerate(argv[1:], start=1):
        flag = arg.split("=", 1)[0]
        if python and flag == "-m" and index + 1 < len(argv):
            parts = argv[index + 1].split(".")
            if all(part.isidentifier() for part in parts):
                directory = Path(*parts)
                candidates = [directory.with_suffix(".py"), directory / "__main__.py"]
                candidates.extend(parent / "__init__.py" for parent in (directory, *directory.parents) if parent.parts)
                files.extend(root / candidate for root in (cwd, cwd / "src") for candidate in candidates)
            break
        if flag in {"-c", "--eval", "-e", "-p", "--print", "--test"}:
            break
        if arg and not arg.startswith("-"):
            files.append(cwd / arg)
    return files


def linked_paths(path: Path) -> set[Path]:
    """Return a launcher name, the symlinks it crosses and its final path."""
    paths: set[Path] = set()
    parts = path.absolute().parts
    current = Path(parts[0])
    pending = list(reversed(parts[1:]))
    links = 0
    paths.add(path.absolute())
    while pending:
        component = pending.pop()
        if component == "..":
            current = current.parent
            continue
        current /= component
        try:
            target = current.readlink()
        except OSError:
            continue
        paths.add(current)
        links += 1
        if links > 40:
            break
        current = current.parent
        parts = target.parts
        if target.is_absolute():
            current = Path(parts[0])
            parts = parts[1:]
        pending.extend(reversed(parts))
    paths.add(current)
    return paths


def find_executable(name: str, cwd: Path) -> str | None:
    if os.path.dirname(name):
        return shutil.which(str(cwd / name))
    path = os.pathsep.join(str(cwd / directory) for directory in os.get_exec_path())
    return shutil.which(name, path=path)
