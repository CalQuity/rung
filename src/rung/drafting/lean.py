"""`rung draft lean`: a model writes a Lean model, proofs and a differential-testing adapter.

The Lean kernel decides the proofs, and a differential test against the real code decides
whether the model describes it. Rung pins the toolchain itself (models remember old versions)
and sends trivially-proved theorems back once for something meaningful.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from rung import guides
from rung.drafting.common import FILES, STRINGS, DraftResult, files_of, safe_write, schema, source_block
from rung.drafting.providers import Provider
from rung.engines import drt, lean

FORMAT = """

Return:
- `files`: `lakefile.toml` (a `[[lean_lib]]` and a `[[lean_exe]]` whose `root = "Main"`), `<Lib>.lean`,
  `Main.lean` and `drt_adapter.py`. Do not write `lean-toolchain`; Rung pins it.
- `theorems`: fully qualified names of every theorem, e.g. `GstModel.split_intrastate`.
- `alphabet`: every action code `Main.lean` accepts, as strings.
- `exe`: the `[[lean_exe]]` name.
- `notes`: anything the developer should know."""
SCHEMA = schema(files=FILES, theorems=STRINGS, alphabet=STRINGS, exe={"type": "string"}, notes={"type": "string"})

DRT_SMOKE_CASES = 300


def _drt_check(out_dir: Path, root: Path, exe: str, alphabet: list[str]) -> drt.DRTResult:
    model = drt.Side(shlex.join([lean.find_lake(), "exe", exe]), out_dir)
    impl = drt.Side(shlex.join([sys.executable, str(out_dir / "drt_adapter.py")]), root)
    return drt.run(model, impl, alphabet=alphabet, cases=DRT_SMOKE_CASES, max_len=6, seed=1)


def draft(
    provider: Provider, root: Path, sources: list[Path], out_dir: Path, *, intent: str = "", max_rounds: int = 4
) -> DraftResult:
    toolchain = lean.toolchain()
    system = guides.load("lean", lean_version=toolchain.split(":")[-1]) + FORMAT
    prompt = (
        "Write an executable Lean 4 model of this code, prove its laws, and write the adapter.\n"
        + (f"Laws the developer cares about: {intent}\n" if intent else "")
        + "\n"
        + source_block(root, sources)
    )
    files: list[Path] = []
    asked_for_stronger = False
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        produced = files_of(reply)
        if "Main.lean" not in produced or "lakefile.toml" not in produced or "drt_adapter.py" not in produced:
            prompt += "\n\nYour reply was missing Main.lean, lakefile.toml or drt_adapter.py."
            continue
        produced["lean-toolchain"] = toolchain + "\n"  # never the model's choice
        produced[".gitignore"] = ".lake/\n"  # build output must never be committed or stale a verdict
        files = safe_write(out_dir, produced)
        theorems = [t.strip() for t in reply["theorems"] if t.strip()]
        alphabet = [str(a).strip() for a in reply["alphabet"] if str(a).strip()]
        exe = reply["exe"].strip() or "model"

        result = lean.run(out_dir, theorems)
        if result.verdict != "pass":
            problem = "\n".join(result.findings) or result.output[-3000:]
            prompt += (
                f"\n\nLean rejected the previous attempt ({result.reason}):\n{problem}\n"
                "Fix the proofs or the model. Do not weaken a theorem to make it pass."
            )
            continue
        if result.warnings and not asked_for_stronger:
            asked_for_stronger = True
            prompt += (
                "\n\nThese theorems look true of anything, regardless of the model, so they may prove nothing:\n"
                + "\n".join(result.warnings)
                + "\nIf so, replace them with statements about the model's behaviour that matter to the developer."
            )
            continue
        if not alphabet:
            prompt += "\n\nYour reply has an empty `alphabet`."
            continue

        check = _drt_check(out_dir, root, exe, alphabet)
        if check.verdict == "error":
            prompt += (
                f"\n\nThe differential test could not run ({check.reason}). Fix Main.lean or drt_adapter.py so "
                "both read one input per line and print exactly one line per input."
            )
            continue
        base = DraftResult(True, result.reason, files, round_no, theorems=theorems, alphabet=alphabet,
                           warnings=result.warnings, notes=reply["notes"].strip())  # fmt: skip
        if check.verdict == "fail" and check.minimal:
            m = check.minimal
            base.message = (
                f"{result.reason}; the code and the model disagree on {check.divergences} of {check.cases} "
                "inputs. Decide: a bug in the code, or a model that says the wrong thing?"
            )
            base.trace = f"input: {m.input or '(empty)'}\n  model: {m.model}\n  code:  {m.impl}"
        return base
    return DraftResult(False, f"no kernel-checked model after {max_rounds} rounds", files, max_rounds)
