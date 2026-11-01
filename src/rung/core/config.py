"""`rung.toml`: the project's declared checks and LLM provider.

The file is small and hand-editable. Rung reads it with `tomllib` and writes it
with a minimal serializer that covers only the shapes Rung itself produces.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rung.core.errors import RungError

CONFIG_NAME = "rung.toml"
CHECK_KINDS = ("command", "evidence", "scan", "deps", "tla", "lean", "drt")


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


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found; run `rung init`") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(**{k: str(v) for k, v in llm_raw.items() if k in LLMConfig.__dataclass_fields__})

    checks: list[Check] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw.get("check", [])):
        entry = dict(entry)
        check_id = entry.pop("id", None)
        kind = entry.pop("kind", None)
        if not check_id or not kind:
            raise ConfigError(f"[[check]] #{i + 1} needs both `id` and `kind`")
        if kind not in CHECK_KINDS:
            raise ConfigError(f"check {check_id!r}: kind must be one of {', '.join(CHECK_KINDS)}")
        if check_id in seen:
            raise ConfigError(f"duplicate check id {check_id!r}")
        seen.add(check_id)
        covers = [str(p) for p in entry.pop("covers", [])]
        checks.append(Check(id=check_id, kind=kind, covers=covers, options=entry))

    name = raw.get("project", {}).get("name") or root.name
    return Config(root=root, name=name, llm=llm, checks=checks)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def dump(config: Config) -> str:
    lines = ["[project]", f"name = {_toml_value(config.name)}", "", "[llm]"]
    for key in ("provider", "model", "base_url", "api_key_env"):
        lines.append(f"{key} = {_toml_value(getattr(config.llm, key))}")
    for c in config.checks:
        lines += ["", "[[check]]", f"id = {_toml_value(c.id)}", f"kind = {_toml_value(c.kind)}"]
        for key, value in c.options.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append(f"covers = {_toml_value(c.covers)}")
    return "\n".join(lines) + "\n"


def save(config: Config) -> Path:
    path = config.root / CONFIG_NAME
    path.write_text(dump(config))
    return path
