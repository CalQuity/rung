"""The SQL behind database calls, for drafts that must model what a call does.

Code that calls a Postgres function (`supabase.rpc("claim_job", ...)`, `cur.execute("select
claim_job(%s)")`) hides its locking and its transaction inside SQL the model never sees. Without
it, a TLA+ spec has to assume each call is one atomic step, and "no violation" says little. This
module finds those calls in the sources, finds the `create function` statements that define them
in the repository's `*.sql` files, and orders the defining files oldest first, so the prompt can
say that the newest definition is the one that runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rung.core import git

# `.rpc("name"` (Supabase, PostgREST clients) and wrappers such as `self._rpc("name"`, with an optional
# `schema.` prefix.
_RPC = re.compile(r"""\b\w*rpc\(\s*[rbu]?["'`]([A-Za-z_][\w.]*)["'`]""", re.I)
# `select name(`, `select * from name(`, `call name(` and `perform name(`, inside SQL strings.
_SQL_CALL = re.compile(
    r"\b(?:select\s+(?:\*\s+from\s+)?|call\s+|perform\s+)((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*\(", re.I
)
_DEFINITION = re.compile(
    r"""\bcreate\s+(?:or\s+replace\s+)?(?:function|procedure)\s+(?:"?([A-Za-z_]\w*)"?\.)?"?([A-Za-z_]\w*)"?\s*\(""",
    re.I,
)
_DOLLAR_TAG = re.compile(r"\bas\s+(\$[A-Za-z_]*\$)", re.I)
# SQL built-ins that `select name(` also matches; they have no definition to find.
BUILTINS = frozenset((
    "count", "sum", "min", "max", "avg", "coalesce", "nullif", "greatest", "least", "now", "exists", "cast",
    "lower", "upper", "length", "trim", "array_agg", "json_agg", "jsonb_agg", "string_agg", "json_build_object",
    "jsonb_build_object", "to_json", "to_jsonb", "row_number", "rank", "dense_rank", "gen_random_uuid",
    "uuid_generate_v4", "current_setting", "set_config", "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_try_advisory_lock", "pg_try_advisory_xact_lock", "pg_advisory_unlock", "pg_sleep", "pg_notify",
    "date_trunc", "extract", "to_char", "to_timestamp", "abs", "round", "floor", "ceil", "random", "unnest",
    "generate_series", "version", "nextval", "currval", "setval", "distinct", "row",
))  # fmt: skip
WHOLE_FILE_BYTES = 40_000  # larger files contribute only the definitions that matter


@dataclass
class SqlContext:
    """The database functions some sources call, and where each is defined."""

    calls: list[str] = field(default_factory=list)  # as written, e.g. `public.claim_job`
    defined: dict[str, list[str]] = field(default_factory=dict)  # call -> defining files, oldest first
    missing: list[str] = field(default_factory=list)  # called, but no definition was found
    files: list[str] = field(default_factory=list)  # every defining file, oldest first

    @property
    def warnings(self) -> list[str]:
        return [
            f"no SQL definition found for database function `{name}`; the model will assume a call to it is "
            "one atomic step, so the result is weaker. Pass the file that defines it, if it is in another repository."
            for name in self.missing
        ]

    def block(self, root: Path) -> str:
        """The defining SQL for the prompt, with line numbers, and a note on which definition wins."""
        if not self.defined and not self.missing:
            return ""
        lines = []
        if self.defined:
            lines.append(
                "The sources call these database functions. Their SQL definitions follow, so model what each one "
                "does (its locks, checks and writes) instead of assuming a call is atomic."
            )
        for name, where in self.defined.items():
            if len(where) > 1:
                lines.append(
                    f"- `{name}` is defined in {len(where)} files, listed oldest to newest: {', '.join(where)}. "
                    "Each `create or replace` replaces the one before, so the newest definition is the one that runs."
                )
            else:
                lines.append(f"- `{name}` is defined in {where[0]}.")
        if self.missing:
            lines.append(
                f"No definition was found for: {', '.join(f'`{m}`' for m in self.missing)}. Model each of those "
                "calls as one atomic step and say so in `notes`."
            )
        names = {_bare(n) for n in self.defined}
        for rel in self.files:
            text = (root / rel).read_text(errors="ignore")
            lines += ["", f"--- {rel} ---", _numbered(text, names)]
        return "\n".join(lines)


def _bare(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower()


def calls_in(text: str) -> list[str]:
    """Database function names called in `text`, in order of first appearance, without built-ins."""
    found = [m.group(1) for m in _RPC.finditer(text)] + [m.group(1) for m in _SQL_CALL.finditer(text)]
    return list(dict.fromkeys(n for n in found if _bare(n) not in BUILTINS))


def definitions_in(text: str) -> set[tuple[str, str]]:
    """`(schema, name)` of every function or procedure `text` creates; schema is empty when unqualified."""
    return {((m.group(1) or "").lower(), m.group(2).lower()) for m in _DEFINITION.finditer(text)}


def _matches(call: str, definition: tuple[str, str]) -> bool:
    schema, _, name = call.lower().rpartition(".")
    return definition[1] == name and (not schema or definition[0] in (schema, ""))


def _numbered(text: str, names: set[str]) -> str:
    """A whole small file, or only the `create function` statements for `names` in a large one."""
    rows = text.splitlines()
    if len(text) <= WHOLE_FILE_BYTES:
        return "\n".join(f"{i:4} {line}" for i, line in enumerate(rows, 1))
    spans = []
    for m in _DEFINITION.finditer(text):
        if m.group(2).lower() not in names:
            continue
        start = text.count("\n", 0, m.start())
        end = _statement_end(text, m.end())
        spans.append((start, text.count("\n", 0, end)))
    parts = []
    for start, end in spans:
        parts.append("\n".join(f"{i + 1:4} {rows[i]}" for i in range(start, min(end + 1, len(rows)))))
    return "\n   ...\n".join(parts)


def _statement_end(text: str, pos: int) -> int:
    """Where the statement starting near `pos` ends: after its dollar-quoted body, or at the next `;`."""
    tag = _DOLLAR_TAG.search(text, pos)
    semicolon = text.find(";", pos)
    if tag and (semicolon == -1 or tag.start() < semicolon):
        close = text.find(tag.group(1), tag.end())
        if close != -1:
            pos = close + len(tag.group(1))
            semicolon = text.find(";", pos)
    return semicolon if semicolon != -1 else len(text)


def _age_order(root: Path, files: list[str]) -> list[str]:
    """Oldest to newest by last commit date; uncommitted files count as newest, then by file name."""
    dated = git.commit_times(root, files) if git.is_repo(root) else {}
    newest = max(dated.values(), default=0) + 1
    return sorted(files, key=lambda f: (dated.get(f, newest), f))


def gather(root: Path, sources: list[Path], *, files: list[str] | None = None) -> SqlContext:
    """Find the database functions `sources` call and the `*.sql` files under `root` that define them."""
    calls: list[str] = []
    for src in sources:
        if src.suffix.lower() != ".sql":
            calls += calls_in(src.read_text(errors="ignore"))
    ctx = SqlContext(calls=list(dict.fromkeys(calls)))
    if not ctx.calls:
        return ctx
    given = {s.resolve() for s in sources}
    sql_files = [f for f in (files if files is not None else git.list_files(root)) if f.lower().endswith(".sql")]
    found: dict[str, list[str]] = {c: [] for c in ctx.calls}
    for rel in sql_files:
        defs = definitions_in((root / rel).read_text(errors="ignore"))
        for call in ctx.calls:
            if any(_matches(call, d) for d in defs):
                found[call].append(rel)
    ordered = _age_order(root, sorted({f for where in found.values() for f in where}))
    rank = {f: i for i, f in enumerate(ordered)}
    ctx.defined = {c: sorted(where, key=rank.__getitem__) for c, where in found.items() if where}
    ctx.missing = [c for c, where in found.items() if not where]
    ctx.files = [f for f in ordered if (root / f).resolve() not in given]  # given sources are already shown
    return ctx
