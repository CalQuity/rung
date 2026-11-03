"""`rung.toml`: the project's declared checks and model provider.

The file is small and hand-editable. Rung reads it with `tomllib`, validates every field it
understands before running anything, and writes it with a serializer that covers the shapes
Rung itself produces. A check's id becomes part of file paths (baselines, evidence), so ids are
restricted to letters, digits, `.`, `_` and `-`.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rung.core.errors import RungError

CONFIG_NAME = "rung.toml"
CHECK_KINDS = ("command", "evidence", "scan", "shortcuts", "deps", "tla", "lean", "drt")
CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# A scenario name; scripts receive them comma-separated in RUNG_FAULTS and RUNG_SCENARIOS.
SCENARIO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# Options that must be non-negative integers wherever they appear.
INT_OPTIONS = ("timeout", "cases", "min_len", "max_len", "seed", "min_age_days", "runs")
# Options that must be lists of strings wherever they appear.
LIST_OPTIONS = ("specs", "require", "allow", "theorems", "alphabet", "faults", "scenarios")
# Every option each kind understands; anything else is a typo that would otherwise do nothing.
COMMON_OPTIONS = ("timeout", "max_age_days", "specs", "drafted_by")
KIND_OPTIONS = {
    "command": ("run", "cwd"),
    "evidence": ("run", "cwd", "require", "runs", "seed", "faults", "scenarios"),
    "scan": ("tool", "run", "cwd", "fail_on", "default_level"),
    "shortcuts": ("allow", "fail_on"),
    "deps": ("allow", "min_age_days"),
    "tla": ("spec", "config"),
    "lean": ("project", "theorems"),
    "drt": ("model", "impl", "model_cwd", "impl_cwd", "alphabet", "cases", "min_len", "max_len", "seed"),
}


class ConfigError(RungError, ValueError):
    """`rung.toml` is missing, malformed, or declares an invalid check."""


@dataclass
class Check:
    """One verification check: a spec, a proof, a differential test, or a command."""

    id: str
    kind: str
    covers: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self.options:
            raise ConfigError(f"check {self.id!r} ({self.kind}) needs `{key}`")
        return self.options[key]

    def get_int(self, key: str, default: int) -> int:
        value = self.options.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"check {self.id!r}: `{key}` must be a non-negative integer, not {value!r}")
        return value

    def get_list(self, key: str) -> list[str]:
        value = self.options.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"check {self.id!r}: `{key}` must be a list of strings")
        return value


@dataclass
class LLMConfig:
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    api_key_env: str = "RUNG_API_KEY"


@dataclass
class Config:
    root: Path
    name: str
    llm: LLMConfig
    checks: list[Check]

    def check(self, check_id: str) -> Check:
        for c in self.checks:
            if c.id == check_id:
                return c
        raise ConfigError(f"no check named {check_id!r} in {CONFIG_NAME}")


def find_root(start: Path) -> Path:
    """Walk up from `start` to the directory holding `rung.toml`."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    raise ConfigError(f"no {CONFIG_NAME} in {start} or its parents; run `rung init`")


def _unquoted_tokens(command: str) -> list[str]:
    """Tokens with quoted text removed, so `node -e "a();"` is not mistaken for shell syntax."""
    lexer = shlex.shlex(command, posix=False, punctuation_chars=False)
    lexer.whitespace_split = True
    try:
        return [t for t in lexer if not (len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"")]
    except ValueError:
        return command.split()


SHELL_TOKENS = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "2>&1", "&"}
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "cmd", "powershell", "pwsh"}


def safe_id(text: str) -> str:
    """A check id made from a path or name: unsupported characters become `-`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:64] or "check"


def check_command(check_id: str, key: str, command: str) -> None:
    """Commands run without a shell. Reject shell syntax instead of passing it to a program as arguments.

    Without this, `cd src && pytest` runs `/usr/bin/cd` on macOS, which ignores its arguments and
    exits 0: a pass that never ran the tests.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ConfigError(f"check {check_id!r}: `{key}` is not a valid command ({exc})") from exc
    if not argv:
        raise ConfigError(f"check {check_id!r}: `{key}` is empty")
    if Path(argv[0]).name in SHELLS:
        return  # an explicit shell, e.g. sh -c "cd src && pytest", is the supported way
    problem = ""
    bare = _unquoted_tokens(command)
    if any(t in SHELL_TOKENS or t.startswith(("$(", "`")) or t.endswith(";") or "&&" in t or "||" in t for t in bare):
        problem = "uses shell syntax (&&, |, ;, redirects)"
    elif "=" in argv[0] and not argv[0].startswith(("-", "/", ".")):
        problem = "starts with an environment assignment"
    elif argv[0] == "cd":
        problem = "starts with `cd` (use the check's `cwd` option instead)"
    if problem:
        raise ConfigError(
            f"check {check_id!r}: `{key}` {problem}, but commands run without a shell. "
            f'Use `cwd`, or wrap it explicitly: sh -c "{command}"'
        )


def _validate_check(check: Check) -> None:
    known = (*COMMON_OPTIONS, *KIND_OPTIONS[check.kind])
    for key in check.options:
        if key not in known:
            close = difflib.get_close_matches(key, (*known, "covers"), n=1)
            hint = f"; did you mean `{close[0]}`?" if close else f"; {check.kind} checks take {', '.join(known)}"
            raise ConfigError(f"check {check.id!r}: unknown option `{key}`{hint}")
    for key in INT_OPTIONS:
        if key in check.options:
            check.get_int(key, 0)
    for key in LIST_OPTIONS:
        if key in check.options:
            check.get_list(key)
    if "runs" in check.options and check.get_int("runs", 1) == 0:
        raise ConfigError(f"check {check.id!r}: `runs` must be at least 1")
    for key in ("faults", "scenarios"):
        if bad := [f for f in check.get_list(key) if not SCENARIO_NAME.match(f)]:
            raise ConfigError(f"check {check.id!r}: `{key}` names are letters, digits, `.`, `_` or `-`, not {bad[0]!r}")
    for key in ("run", "cwd", "tool", "spec", "config", "project", "model", "impl", "fail_on", "default_level",
                "model_cwd", "impl_cwd"):  # fmt: skip
        if key in check.options and not isinstance(check.options[key], str):
            raise ConfigError(f"check {check.id!r}: `{key}` must be a string")
    for key in ("run", "model", "impl"):
        if key in check.options:
            check_command(check.id, key, check.options[key])


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found; run `rung init`") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    llm_raw = raw.get("llm", {})
    if not isinstance(llm_raw, dict):
        raise ConfigError("[llm] must be a table")
    llm = LLMConfig(**{k: str(v) for k, v in llm_raw.items() if k in LLMConfig.__dataclass_fields__})

    entries = raw.get("check", [])
    if not isinstance(entries, list):
        raise ConfigError("checks are declared as [[check]] tables")
    checks: list[Check] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        entry = dict(entry)
        check_id = entry.pop("id", None)
        kind = entry.pop("kind", None)
        if not check_id or not kind:
            raise ConfigError(f"[[check]] #{i + 1} needs both `id` and `kind`")
        if not isinstance(check_id, str) or not CHECK_ID.match(check_id):
            raise ConfigError(f"check id {check_id!r} must be letters, digits, `.`, `_` or `-` (at most 64)")
        if kind not in CHECK_KINDS:
            raise ConfigError(f"check {check_id!r}: kind must be one of {', '.join(CHECK_KINDS)}")
        if check_id in seen:
            raise ConfigError(f"duplicate check id {check_id!r}")
        seen.add(check_id)
        covers = entry.pop("covers", [])
        if not isinstance(covers, list) or not all(isinstance(p, str) for p in covers):
            raise ConfigError(f'check {check_id!r}: `covers` must be a list of glob strings, e.g. ["src/**"]')
        check = Check(id=check_id, kind=kind, covers=covers, options=entry)
        _validate_check(check)
        checks.append(check)

    project = raw.get("project", {})
    name = (project.get("name") if isinstance(project, dict) else None) or root.name
    return Config(root=root, name=str(name), llm=llm, checks=checks)


def toml_value(value: Any) -> str:
    """A TOML literal for the values Rung writes. Strings use JSON escaping, which is valid TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def dump(config: Config) -> str:
    lines = ["[project]", f"name = {toml_value(config.name)}", "", "[llm]"]
    for key in ("provider", "model", "base_url", "api_key_env"):
        lines.append(f"{key} = {toml_value(getattr(config.llm, key))}")
    for c in config.checks:
        lines += ["", "[[check]]", f"id = {toml_value(c.id)}", f"kind = {toml_value(c.kind)}"]
        for key, value in c.options.items():
            lines.append(f"{key} = {toml_value(value)}")
        lines.append(f"covers = {toml_value(c.covers)}")
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` in one step, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        # mkstemp creates the file 0600; keep the existing mode, or use the usual 0644.
        os.chmod(tmp, path.stat().st_mode & 0o7777 if path.exists() else 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(config: Config) -> Path:
    """Write `rung.toml`. Comments are not preserved; the file is regenerated from the parsed config.

    Every check is validated exactly as `load` will validate it, so a command can never save a
    configuration that the next command rejects.
    """
    for check in config.checks:
        if not CHECK_ID.match(check.id):
            raise ConfigError(f"check id {check.id!r} must be letters, digits, `.`, `_` or `-` (at most 64)")
        _validate_check(check)
    text = dump(config)
    tomllib.loads(text)  # never write a file Rung itself could not read back
    path = config.root / CONFIG_NAME
    write_atomic(path, text)
    return path
