"""Shared pieces of the propose -> check -> repair loop.

Checker *errors* (a parse error, a failed build, a `sorry`, an adapter that crashes) go back
to the model to fix. Checker *findings* (a TLC counterexample, the code disagreeing with a
proven model) do not: each is either a real bug or a spec that says the wrong thing, and only
a person can tell which. A draft therefore stops and shows the finding instead of letting the
model edit the spec until the checker is quiet. That rule is the guard against specification
hacking.
"""

from __future__ import annotations

import re
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


def _objects(**fields: dict[str, Any]) -> dict[str, Any]:
    """An array of strict objects with these fields."""
    return {
        "type": "array",
        "items": {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False},
    }


_STR = {"type": "string"}
LAWS = _objects(id=_STR, text=_STR, harm=_STR)
LAWS_FORMAT = """
- `laws`: each guarantee the spec or proofs check, as one plain sentence a non-specialist can approve,
  with a short kebab-case `id` and `harm`: one sentence on what goes wrong for a person if it breaks.
  These are added to LAWS.md as proposals; a person approves them."""
# What a formal model covers, for people and for the per-check verify skill.
SCOPE = {
    "type": "object",
    "properties": {"bounds": STRINGS, "assumptions": STRINGS, "not_covered": STRINGS},
    "required": ["bounds", "assumptions", "not_covered"],
    "additionalProperties": False,
}
SCOPE_FORMAT = """
- `scope`: `bounds` (each constant or limit and its value, e.g. "Clicks = {c1, c2}: two clicks of Pay"),
  `assumptions` (what the model takes as given, e.g. "each database call is atomic") and `not_covered`
  (behaviour of the code the model leaves out). Plain sentences.
- `functions`: each function the model mirrors, as `path:line name`."""
ACTIONS = _objects(name=_STR, label=_STR)
INVARIANTS = _objects(name=_STR, statement=_STR, harm=_STR)
LABELS_FORMAT = """
- `actions`: every action `Next` can take, with `name` exactly as in the spec and `label`: what happens,
  in plain words a non-specialist understands ("a customer clicks Pay", "the gateway charges the card").
- `invariants`: every invariant and property the .cfg lists, with `name` as in the spec, `statement`
  (one plain sentence) and `harm` (one sentence: what goes wrong for a person if it breaks)."""


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
    context: list[str] = field(default_factory=list)  # files Rung added to the prompt, e.g. SQL definitions
    described: dict[str, Any] = field(default_factory=dict)  # scope, functions, action labels, invariants
    failability: dict[str, Any] = field(default_factory=dict)  # guard mutants caught, for a passing TLA+ spec


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


# Absolute paths that exist on one machine only: a home directory or a temporary directory.
_MACHINE_PATH = re.compile(
    r"(?<![\w.~-])(?:/(?:Users|home)/[^/\s'\"`]+/|[A-Za-z]:[\\/]+(?:Users|Windows[\\/]+Temp)[\\/]"
    r"|/(?:private/)?(?:tmp|var/tmp)(?=[/\s'\"`]|$)|/(?:private/)?var/folders/)",
    re.M,
)


def machine_paths(files: dict[str, str]) -> list[str]:
    """`file:line: path` for each home or temporary directory path a model wrote into `files`.

    Such a path works only on the machine that drafted the file (a fallback to the developer's own
    checkout, a scratch file in /tmp), so a teammate's run or CI fails or reads the wrong thing.
    """
    found = []
    for name, body in files.items():
        if name.endswith(".md"):  # prose for people (a feature map) runs nowhere
            continue
        for n, line in enumerate(body.splitlines(), 1):
            if m := _MACHINE_PATH.search(line):
                found.append(f"{name}:{n}: {m.group(0)}")
    return found


def machine_path_problem(files: dict[str, str]) -> str:
    """The repair request for a reply that hard-codes machine-specific paths, or "" when it has none."""
    found = machine_paths(files)
    if not found:
        return ""
    return (
        "\n\nThese files hard-code absolute paths that exist only on one machine:\n"
        + "\n".join(f"- {f}" for f in found)
        + "\nUse paths relative to the script or the repository root (e.g. `Path(__file__).resolve().parent`), "
        "or a temporary directory created at run time. Return the same fields."
    )
