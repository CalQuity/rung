"""`rung draft tla`: a model writes a TLA+ spec that mirrors the code; TLC decides."""

from __future__ import annotations

import re
from pathlib import Path

from rung import guides
from rung.drafting import sql_context
from rung.drafting.common import (
    FILES,
    LAWS,
    LAWS_FORMAT,
    DraftResult,
    files_of,
    machine_path_problem,
    safe_write,
    schema,
    source_block,
)
from rung.drafting.providers import Provider
from rung.engines import tla

FORMAT = (
    """

Return `files` with exactly two entries: `<Module>.tla` (starting `---- MODULE <Module> ----` and ending
`====`) and `<Module>.cfg`. Put anything you want the developer to know in `notes`."""
    + LAWS_FORMAT
)
SCHEMA = schema(files=FILES, laws=LAWS, notes={"type": "string"})

# Slips language models make in TLA+ that are safe to correct mechanically before SANY sees the spec.
UNICODE_OPERATORS = {
    "\u2227": "/\\",  # ∧
    "\u2228": "\\/",  # ∨
    "\u00ac": "~",  # ¬
    "\u2208": "\\in",  # ∈
    "\u2209": "\\notin",  # ∉
    "\u21d2": "=>",  # ⇒
    "\u2192": "->",  # →
    "\u21d4": "<=>",  # ⇔
    "\u2260": "#",  # ≠
    "\u2264": "<=",  # ≤
    "\u2265": ">=",  # ≥
    "\u222a": "\\cup",  # ∪
    "\u2229": "\\cap",  # ∩
    "\u2286": "\\subseteq",  # ⊆
    "\u2200": "\\A",  # ∀
    "\u2203": "\\E",  # ∃
    "\u225c": "==",  # ≜
    "\u25a1": "[]",  # □
    "\u25c7": "<>",  # ◇
    "\u27e8": "<<",  # ⟨
    "\u27e9": ">>",  # ⟩
    "\u2032": "'",  # ′
}


def normalize(spec: str) -> str:
    """Replace Unicode operators with ASCII and drop stray Markdown fences."""
    for uni, ascii_op in UNICODE_OPERATORS.items():
        spec = spec.replace(uni, ascii_op)
    return re.sub(r"^```.*$\n?", "", spec, flags=re.M)


def draft(
    provider: Provider, root: Path, sources: list[Path], out_dir: Path, *, intent: str = "", max_rounds: int = 4,
    sql: sql_context.SqlContext | None = None,
) -> DraftResult:  # fmt: skip
    """Draft a spec for `sources`; `sql` is the SQL behind their database calls (found here when not given)."""
    sql = sql if sql is not None else sql_context.gather(root, sources)
    system = guides.load("tla") + FORMAT
    prompt = (
        "Write a TLA+ model of this code.\n"
        + (f"What it must guarantee (from the developer): {intent}\n" if intent else "")
        + "\n"
        + source_block(root, sources)
        + (f"\n\n{block}" if (block := sql.block(root)) else "")
    )
    files: list[Path] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        produced, notes, laws = files_of(reply), reply["notes"].strip(), reply["laws"]
        spec_name = next((n for n in produced if n.endswith(".tla")), None)
        cfg_name = next((n for n in produced if n.endswith(".cfg")), None)
        if not spec_name or not cfg_name:
            prompt += "\n\nYour reply must contain one `.tla` file and one `.cfg` file."
            continue
        produced[spec_name] = normalize(produced[spec_name])
        if problem := machine_path_problem(produced):
            prompt += problem
            continue
        files = safe_write(out_dir, produced)
        result = tla.run(out_dir / spec_name, out_dir / cfg_name)
        if result.verdict == "fail":
            return DraftResult(
                True,
                f"TLC found a counterexample: {result.reason}. Decide: real bug, or wrong spec?",
                files,
                round_no,
                tla.render_trace(result.trace),
                notes=notes,
                laws=laws,
                warnings=sql.warnings,
                context=sql.files,
            )
        if result.verdict == "pass" and result.distinct_states > 1:
            return DraftResult(True, f"model-checked {result.distinct_states} distinct states, no violation",
                               files, round_no, notes=notes, laws=laws, warnings=sql.warnings,
                               context=sql.files)  # fmt: skip
        problem = (
            (result.reason + "\n" + result.output[-2000:])
            if result.verdict == "error"
            else "TLC explored at most one state, so the invariants are vacuous. Make Init and Next reachable."
        )
        prompt += f"\n\nTLC rejected your previous spec:\n{problem}\nFix the spec (not the invariants)."
    return DraftResult(False, f"no checkable spec after {max_rounds} rounds", files, max_rounds,
                       warnings=sql.warnings, context=sql.files)  # fmt: skip
