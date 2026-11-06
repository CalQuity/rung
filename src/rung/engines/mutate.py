"""Mutants: small deliberate bugs in a source file, to find out whether tests notice them.

A test that passes on the real code proves little if it also passes when `<` becomes `<=` or `+`
becomes `-`. Rung uses mutants to reject properties that are too weak to catch anything. The
mutations are textual and conservative (comparison, arithmetic, boolean and constant flips on code
lines, never inside strings or comments), so every mutant is a plausible one-token bug.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from rung.core.config import write_atomic
from rung.engines.shortcuts import split_lines

PY = (".py",)
JS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

# (pattern, replacement) pairs; the first group is kept, so patterns can require context.
# Comparisons need spaces around them, as formatters write them, so generics (`Array<T>`), arrows
# (`=>`) and shifts are never touched: those mutants would be syntax errors, not bugs.
_COMMON = (
    (r"(?<=\s)<(?=\s)", "<="),
    (r"(?<=\s)<=(?=\s)", "<"),
    (r"(?<=\s)>(?=\s)", ">="),
    (r"(?<=\s)>=(?=\s)", ">"),
    (r"(?<=\s)\+(?=\s)", "-"),
    (r"(?<=\s)-(?=\s)", "+"),
    (r"(?<=\s)\*(?=\s)", "/"),
)
_PY = (*_COMMON, (r"==", "!="), (r"!=", "=="), (r"\band\b", "or"), (r"\bor\b", "and"),
       (r"\bTrue\b", "False"), (r"\bFalse\b", "True"), (r"\bnot\s+", ""),
       (r"(?<![\w.])min(?=\s*\()", "max"), (r"(?<![\w.])max(?=\s*\()", "min"))  # fmt: skip
_JS = (*_COMMON, (r"===", "!=="), (r"!==", "==="), (r"&&", "||"), (r"\|\|", "&&"),
       (r"\btrue\b", "false"), (r"\bfalse\b", "true"))  # fmt: skip


@dataclass(frozen=True)
class Mutant:
    path: Path
    line: int  # 1-based
    original: str
    mutated: str

    @property
    def describe(self) -> str:
        return f"{self.path.name}:{self.line}: `{self.original.strip()}` -> `{self.mutated.strip()}`"


def _code_spans(line: str, comment: str) -> list[tuple[int, int]]:
    """Ranges of `line` outside string literals and before a comment."""
    spans, start, quote, i = [], 0, "", 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote, start = "", i + 1
        elif ch in "\"'`":
            spans.append((start, i))
            quote = ch
        elif line.startswith(comment, i):
            spans.append((start, i))
            return [s for s in spans if s[1] > s[0]]
        i += 1
    if not quote:
        spans.append((start, len(line)))
    return [s for s in spans if s[1] > s[0]]


def mutants(path: Path, limit: int = 12, lines: set[int] | None = None) -> list[Mutant]:
    """Up to `limit` one-token mutants of `path`, spread across the file (optionally only on `lines`)."""
    if path.suffix in PY:
        rules, comment = _PY, "#"
    elif path.suffix in JS:
        rules, comment = _JS, "//"
    else:
        return []
    found: list[Mutant] = []
    text = path.read_text()
    # Lines with no code at all (inside a docstring, a template literal or a block comment) are skipped.
    code_of = [code for code, _ in split_lines(path.name, text)]
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if lines is not None and number not in lines:
            continue
        if not code_of[number - 1].strip():
            continue
        if not stripped or stripped.startswith((comment, "import ", "from ", "@", "*", "/*", '"""', "'''")):
            continue
        for start, end in _code_spans(line, comment):
            segment = line[start:end]
            for pattern, replacement in rules:
                for m in re.finditer(pattern, segment):
                    new = line[: start + m.start()] + replacement + line[start + m.end() :]
                    if new != line:
                        found.append(Mutant(path, number, line, new))
    if len(found) <= limit:
        return found
    step = len(found) / limit  # spread the sample over the whole file
    return [found[int(i * step)] for i in range(limit)]


@contextmanager
def applied(mutant: Mutant) -> Iterator[None]:
    """Write the mutant into its file for the duration of the block, then restore the original.

    Only ever call this on a disposable copy of the project (see `rung.core.sandbox`): a process
    killed inside the block leaves the mutant in place.
    """
    original = mutant.path.read_bytes()
    lines = original.decode().splitlines(keepends=True)
    current = lines[mutant.line - 1]
    lines[mutant.line - 1] = mutant.mutated + current[len(current.rstrip("\r\n")) :]
    _drop_bytecode(mutant.path)
    write_atomic(mutant.path, "".join(lines))
    try:
        yield
    finally:
        mutant.path.write_bytes(original)
        _drop_bytecode(mutant.path)


def _drop_bytecode(source: Path) -> None:
    """Delete cached bytecode for `source`.

    Python trusts a `.pyc` whose recorded mtime (whole seconds) and size match the source, so a mutant
    and the original written within the same second at the same size would otherwise share bytecode,
    and the "real" code would run as the mutant.
    """
    if source.suffix == ".py":
        for pyc in (source.parent / "__pycache__").glob(f"{source.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)
