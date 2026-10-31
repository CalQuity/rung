"""Draft formal artifacts from real code with an LLM, then let the checker decide.

The loop is propose -> check -> repair. Checker *errors* (a parse error, a
failed build, a `sorry`) go back to the model to fix. A checker *finding* (a
TLC counterexample) does not: it is either a real bug or a spec that says the
wrong thing, and only a person can tell which. The draft therefore stops and
shows the trace instead of letting the model edit the spec until it passes.
That rule is the guard against "specification hacking".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rung.engines import lean, tla
from rung.providers import Provider

FENCE = re.compile(r"```[\w+-]*\s+file=(\S+)\s*\n(.*?)```", re.S)

TLA_SYSTEM = """You write TLA+ specifications that mirror real code so TLC can find its bugs.
Rules:
- Model what the code DOES, not what it should do. Every action's guard must match
  the code's actual condition; cite the function and line in a comment above it.
- State what the code SHOULD guarantee as invariants, taken from names, docs and
  comments. Always include TypeOK.
- Keep the model finite and small (2-3 processes/workers, small counters) so TLC
  finishes in seconds. Use model values in the .cfg for identifiers.
- If the system can legitimately stop, add a `Terminated` disjunct to Next that
  stutters (`/\\ <done condition> /\\ UNCHANGED vars`) so TLC does not report a false deadlock.
- No PlusCal. Plain TLA+ only, `EXTENDS Naturals, FiniteSets` at most.
Output exactly two fenced blocks and nothing else:
```tla file=<Module>.tla
...
```
```cfg file=<Module>.cfg
...
```"""

LEAN_SYSTEM = """You write small executable Lean 4 models of real code, prove their laws, and
expose the model as a line-protocol program for differential testing.
Rules:
- Model the INTENDED behaviour as a pure `step : State -> Action -> State`.
- Prove each law as a theorem. Never use sorry, admit, axiom, native_decide,
  unsafe, implemented_by or extern. Prefer `simp`, `omega`, `decide`, `cases`, `split`.
- Core Lean only (no Mathlib), so the project builds in seconds.
- Main.lean reads stdin line by line. Each line is space-separated natural numbers,
  one per action, encoded as documented in a comment. Apply them from the initial
  state and print the final state as one line of `key=value` pairs.
- Also write `drt_adapter` for the implementation: a script in the implementation's
  language that speaks the same protocol and prints the same `key=value` line.
Output fenced blocks only, each with a file= path relative to the project root:
```toml file=lakefile.toml
```
```text file=lean-toolchain
```
```lean file=<Lib>.lean
```
```lean file=Main.lean
```
```python file=drt_adapter.py
```
Then two lines:
THEOREMS: <Lib>.thm1, <Lib>.thm2
ALPHABET: <every action code Main.lean accepts, space-separated>"""


@dataclass
class DraftResult:
    ok: bool
    message: str
    files: list[Path] = field(default_factory=list)
    rounds: int = 0
    trace: str = ""
    theorems: list[str] = field(default_factory=list)
    alphabet: list[str] = field(default_factory=list)


def parse_files(text: str) -> dict[str, str]:
    return {name: body.rstrip() + "\n" for name, body in FENCE.findall(text)}


def safe_write(base: Path, files: dict[str, str]) -> list[Path]:
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


def _source_block(root: Path, sources: list[Path]) -> str:
    parts = []
    for src in sources:
        numbered = "\n".join(f"{i:4} {line}" for i, line in enumerate(src.read_text().splitlines(), 1))
        parts.append(f"--- {src.relative_to(root)} ---\n{numbered}")
    return "\n\n".join(parts)


def draft_tla(
    provider: Provider,
    root: Path,
    sources: list[Path],
    out_dir: Path,
    *,
    intent: str = "",
    max_rounds: int = 4,
) -> DraftResult:
    prompt = (
        "Write a TLA+ model of this code.\n"
        + (f"What it must guarantee (from the developer): {intent}\n" if intent else "")
        + "\n"
        + _source_block(root, sources)
    )
    files: list[Path] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete(prompt, system=TLA_SYSTEM, cwd=root)
        produced = parse_files(reply)
        spec_name = next((n for n in produced if n.endswith(".tla")), None)
        cfg_name = next((n for n in produced if n.endswith(".cfg")), None)
        if not spec_name or not cfg_name:
            prompt += (
                "\n\nYour reply did not contain both a ```tla file=...``` and a ```cfg file=...``` block. Try again."
            )
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
            )
        if result.verdict == "pass" and result.distinct_states > 1:
            return DraftResult(
                True,
                f"model-checked {result.distinct_states} distinct states, no violation",
                files,
                round_no,
            )
        problem = (
            (result.reason + "\n" + result.output[-2000:])
            if result.verdict == "error"
            else ("TLC explored at most one state, so the invariants are vacuous. Make Init and Next reachable.")
        )
        prompt += (
            f"\n\nTLC rejected your previous spec:\n{problem}\n"
            "Fix the spec (not the invariants) and reply in the same format."
        )
    return DraftResult(False, f"no checkable spec after {max_rounds} rounds", files, max_rounds)


def draft_lean(
    provider: Provider,
    root: Path,
    sources: list[Path],
    out_dir: Path,
    *,
    intent: str = "",
    max_rounds: int = 4,
) -> DraftResult:
    prompt = (
        "Write an executable Lean 4 model of this code, prove its laws, and write the adapter.\n"
        + (f"Laws the developer cares about: {intent}\n" if intent else "")
        + "\n"
        + _source_block(root, sources)
    )
    files: list[Path] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete(prompt, system=LEAN_SYSTEM, cwd=root)
        produced = parse_files(reply)
        theorems = []
        if m := re.search(r"^THEOREMS:\s*(.+)$", reply, re.M):
            theorems = [t.strip() for t in m.group(1).split(",") if t.strip()]
        if "Main.lean" not in produced or not any(n.startswith("lakefile") for n in produced):
            prompt += "\n\nYour reply was missing Main.lean or the lakefile. Reply in the required format."
            continue
        files = safe_write(out_dir, produced)
        result = lean.run(out_dir, theorems)
        if result.verdict == "pass":
            alphabet = []
            if a := re.search(r"^ALPHABET:\s*(.+)$", reply, re.M):
                alphabet = a.group(1).split()
            return DraftResult(True, result.reason, files, round_no, theorems=theorems, alphabet=alphabet)
        problem = "\n".join(result.findings) or result.output[-3000:]
        prompt += (
            f"\n\nLean rejected the previous attempt ({result.reason}):\n{problem}\n"
            "Fix the proofs or the model. Do not weaken a theorem to make it pass. Reply in the same format."
        )
    return DraftResult(False, f"no kernel-checked model after {max_rounds} rounds", files, max_rounds)
