"""`rung draft lean`: a model writes a Lean model, proofs and a differential-testing adapter.

The Lean kernel decides the proofs, and a differential test against the real code decides
whether the model describes it. Rung pins the toolchain itself (models remember old versions)
and sends trivially-proved theorems back once for something meaningful.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rung import guides
from rung.core import ledger, sandbox
from rung.core.errors import RungError
from rung.drafting import sql_context
from rung.drafting.common import (
    FILES,
    LAWS,
    LAWS_FORMAT,
    SCOPE,
    SCOPE_FORMAT,
    STRINGS,
    DraftResult,
    files_of,
    machine_path_problem,
    safe_write,
    schema,
    source_block,
)
from rung.drafting.providers import Provider
from rung.engines import drt, lean

FORMAT = (
    """

Return:
- `files`: `lakefile.toml` (a `[[lean_lib]]` and a `[[lean_exe]]` whose `root = "Main"`), `<Lib>.lean`,
  `Main.lean` and `drt_adapter.py`. Do not write `lean-toolchain`; Rung pins it.
- `theorems`: fully qualified names of every theorem, e.g. `GstModel.split_intrastate`.
- `alphabet`: every action code `Main.lean` accepts, as strings.
- `exe`: the `[[lean_exe]]` name.
- `notes`: anything the developer should know."""
    + SCOPE_FORMAT
    + LAWS_FORMAT
)
SCHEMA = schema(
    files=FILES, theorems=STRINGS, alphabet=STRINGS, exe={"type": "string"}, scope=SCOPE, functions=STRINGS,
    laws=LAWS, notes={"type": "string"},
)  # fmt: skip

DRT_SMOKE_CASES = 300


@contextmanager
def _trial(out_dir: Path, root: Path) -> Iterator[tuple[Path, Path, tuple[str, ...], dict[str, str]]]:
    """Run generated formal tooling in a guarded copy with a filtered environment."""
    with sandbox.copy_of(root) as trial:
        project = trial / ".rung" / "cache" / "formal-trial"
        shutil.copytree(out_dir, project, ignore=shutil.ignore_patterns(".lake", ".git", ".rung"))
        prefix = sandbox.validated_write_guard(trial)
        yield project, trial, prefix, sandbox.trial_environment(trial)


def _trial_inputs(project: Path, trial: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Detect source or build-policy edits inside a disposable copy as well as outside it."""
    patterns = ["**/*.lean", "lakefile.*", "lean-toolchain", "lake-manifest.json", "**/*.py"]
    return ledger.snapshot(project, patterns), ledger.snapshot(trial, ["**/*"])


def _lean_check(out_dir: Path, root: Path, theorems: list[str]) -> lean.LeanResult:
    try:
        with _trial(out_dir, root) as (project, trial, prefix, env):
            before = _trial_inputs(project, trial)
            result = lean.run(project, theorems, command_prefix=prefix, environment=env)
            if before != _trial_inputs(project, trial):
                return lean.LeanResult(
                    "error", "formal trial inputs changed during execution; review build metadata first"
                )
            return result
    except (RungError, OSError) as exc:
        return lean.LeanResult("error", str(exc))


def _drt_check(out_dir: Path, root: Path, exe: str, alphabet: list[str]) -> drt.DRTResult:
    try:
        with _trial(out_dir, root) as (project, trial, prefix, env):
            model = drt.Side(shlex.join([lean.find_lake(), "exe", exe]), project, prefix, env)
            impl = drt.Side(shlex.join([sys.executable, str(project / "drt_adapter.py")]), trial, prefix, env)
            before = _trial_inputs(project, trial)
            result = drt.run(model, impl, alphabet=alphabet, cases=DRT_SMOKE_CASES, max_len=6, seed=1)
            if before != _trial_inputs(project, trial):
                return drt.DRTResult(
                    "error", "formal trial inputs changed during execution; review build metadata first"
                )
            return result
    except (RungError, OSError) as exc:
        return drt.DRTResult("error", str(exc))


def draft(
    provider: Provider, root: Path, sources: list[Path], out_dir: Path, *, intent: str = "", max_rounds: int = 4,
    sql: sql_context.SqlContext | None = None,
) -> DraftResult:  # fmt: skip
    """Draft a model for `sources`; `sql` is the SQL behind their database calls (found here when not given)."""
    sql = sql if sql is not None else sql_context.gather(root, sources)
    toolchain = lean.toolchain()
    system = guides.load("lean", lean_version=toolchain.split(":")[-1]) + FORMAT
    prompt = (
        "Write an executable Lean 4 model of this code, prove its laws, and write the adapter.\n"
        + (f"Laws the developer cares about: {intent}\n" if intent else "")
        + "\n"
        + source_block(root, sources)
        + (f"\n\n{block}" if (block := sql.block(root)) else "")
    )
    files: list[Path] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        produced = files_of(reply)
        if "Main.lean" not in produced or "lakefile.toml" not in produced or "drt_adapter.py" not in produced:
            prompt += "\n\nYour reply was missing Main.lean, lakefile.toml or drt_adapter.py."
            continue
        if problem := machine_path_problem(produced):
            prompt += problem
            continue
        produced["lean-toolchain"] = toolchain + "\n"  # never the model's choice
        produced[".gitignore"] = ".lake/\n"  # build output must never be committed or stale a verdict
        files = safe_write(out_dir, produced)
        theorems = [t.strip() for t in reply["theorems"] if t.strip()]
        alphabet = [str(a).strip() for a in reply["alphabet"] if str(a).strip()]
        exe = reply["exe"].strip() or "model"

        result = _lean_check(out_dir, root, theorems)
        if result.verdict != "pass":
            problem = "\n".join(result.findings) or result.output[-3000:]
            prompt += (
                f"\n\nLean rejected the previous attempt ({result.reason}):\n{problem}\n"
                "Fix the proofs or the model. Do not weaken a theorem to make it pass."
            )
            continue
        if result.warnings:
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
                           warnings=result.warnings + sql.warnings, notes=reply["notes"].strip(), laws=reply["laws"],
                           context=sql.files,
                           described={"scope": reply["scope"], "functions": reply["functions"]})  # fmt: skip
        if check.verdict == "fail" and check.minimal:
            m = check.minimal
            base.message = (
                f"{result.reason}; the code and the model disagree on {check.divergences} of {check.cases} "
                "inputs. Decide: a bug in the code, or a model that says the wrong thing?"
            )
            base.trace = f"input: {m.input or '(empty)'}\n  model: {m.model}\n  code:  {m.impl}"
        return base
    return DraftResult(False, f"no kernel-checked model after {max_rounds} rounds", files, max_rounds,
                       warnings=sql.warnings, context=sql.files)  # fmt: skip
