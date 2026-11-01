"""Shared pieces of the propose -> check -> repair loop.

Checker *errors* (a parse error, a failed build, a `sorry`, an adapter that crashes) go back
to the model to fix. Checker *findings* (a TLC counterexample, the code disagreeing with a
proven model) do not: each is either a real bug or a spec that says the wrong thing, and only
a person can tell which. A draft therefore stops and shows the finding instead of letting the
model edit the spec until the checker is quiet. That rule is the guard against specification
hacking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# JSON Schemas for drafted artifacts. Strict style (every property required, no extra keys) is what
# both Claude Code `--json-schema` and Codex `--output-schema` accept.
FILES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
STRINGS = {"type": "array", "items": {"type": "string"}}
LAWS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
        "required": ["id", "text"],
        "additionalProperties": False,
    },
}
LAWS_FORMAT = """
- `laws`: each guarantee the spec or proofs check, as one plain sentence a non-specialist can approve,
  with a short kebab-case `id`. These are added to LAWS.md as proposals; a person approves them."""


def schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


@dataclass
class DraftResult:
    ok: bool
    message: str
    files: list[Path] = field(default_factory=list)
    rounds: int = 0
    trace: str = ""  # a counterexample or divergence for a person to judge
    theorems: list[str] = field(default_factory=list)
    alphabet: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: str = ""  # what the model wants the developer to know
    laws: list[dict[str, str]] = field(default_factory=list)  # proposed laws, for a person to approve


def files_of(reply: dict[str, Any]) -> dict[str, str]:
    """`{"files": [{"path": ..., "content": ...}]}` -> {path: content}."""
    return {f["path"]: f["content"].rstrip() + "\n" for f in reply.get("files", [])}


def safe_write(base: Path, files: dict[str, str]) -> list[Path]:
    """Write model-produced files, refusing any path that escapes `base`."""
    written = []
    base = base.resolve()
    for name, body in files.items():
        target = (base / name).resolve()
        if base not in target.parents:
            raise ValueError(f"refusing to write outside {base}: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        written.append(target)
    return written


def source_block(root: Path, sources: list[Path]) -> str:
    """Source files with line numbers, so the model can cite `file:line` in comments."""
    parts = []
    for src in sources:
        numbered = "\n".join(f"{i:4} {line}" for i, line in enumerate(src.read_text().splitlines(), 1))
        parts.append(f"--- {src.relative_to(root)} ---\n{numbered}")
    return "\n\n".join(parts)
