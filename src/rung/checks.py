"""Run declared checks and record their verdicts in the ledger."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rung import ledger
from rung.config import Check, Config, ConfigError
from rung.errors import ToolMissing
from rung.runners import drt, lean, tla


@dataclass
class Outcome:
    check: Check
    verdict: str
    summary: str
    lines: list[str]


def spec_files(check: Check) -> list[str]:
    """The spec-side files whose change should stale this check's verdict."""
    if check.kind == "tla":
        files = [check.require("spec")]
        if cfg := check.get("config"):
            files.append(cfg)
        return files
    if check.kind == "lean":
        project = check.require("project").rstrip("/")
        return [f"{project}/**/*.lean", f"{project}/lakefile.*", f"{project}/lean-toolchain"]
    if check.kind == "drt":
        return list(check.get("specs", []))
    return list(check.get("specs", []))


def _run_tla(root: Path, check: Check) -> Outcome:
    cfg = check.get("config")
    result = tla.run(
        root / check.require("spec"),
        root / cfg if cfg else None,
        timeout=int(check.get("timeout", 600)),
    )
    lines = [f"{result.distinct_states} distinct states explored"] if result.distinct_states else []
    if result.trace:
        lines.append(f"counterexample ({len(result.trace)} states):")
        lines.append(tla.render_trace(result.trace))
    elif result.verdict == "error":
        lines.append(result.output.strip()[-1500:])
    if result.verdict == "pass" and result.distinct_states <= 1:
        # A spec that reaches one state checks nothing; treat it as a spec bug.
        return Outcome(check, "fail", "spec reaches at most one state; invariants are vacuous", lines)
    return Outcome(check, result.verdict, result.reason, lines)


def _run_lean(root: Path, check: Check) -> Outcome:
    result = lean.run(
        root / check.require("project"),
        list(check.get("theorems", [])),
        timeout=int(check.get("timeout", 1800)),
    )
    lines = list(result.findings)
    for theorem, axioms in result.axioms.items():
        lines.append(f"{theorem}: axioms [{', '.join(axioms) or 'none'}]")
    if result.verdict != "pass" and not result.findings:
        lines.append(result.output.strip()[-1500:])
    return Outcome(check, result.verdict, result.reason, lines)


def _run_drt(root: Path, check: Check) -> Outcome:
    model = drt.Side(check.require("model"), root / check.get("model_cwd", "."))
    impl = drt.Side(check.require("impl"), root / check.get("impl_cwd", "."))
    result = drt.run(
        model,
        impl,
        alphabet=[str(t) for t in check.require("alphabet")],
        cases=int(check.get("cases", 1000)),
        min_len=int(check.get("min_len", 0)),
        max_len=int(check.get("max_len", 8)),
        seed=int(check.get("seed", 0)),
        timeout=int(check.get("timeout", 600)),
    )
    lines = []
    if result.minimal:
        m = result.minimal
        lines += [
            f"minimal diverging input: {m.input or '(empty)'}",
            f"  model: {m.model}",
            f"  impl:  {m.impl}",
        ]
    return Outcome(check, result.verdict, result.reason, lines)


def _run_command(root: Path, check: Check) -> Outcome:
    command = check.require("run")
    proc = subprocess.run(
        shlex.split(command),
        cwd=root / check.get("cwd", "."),
        capture_output=True,
        text=True,
        timeout=int(check.get("timeout", 1800)),
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
    verdict = "pass" if proc.returncode == 0 else "fail"
    return Outcome(check, verdict, f"`{command}` exited {proc.returncode}", tail)


_RUNNERS = {"tla": _run_tla, "lean": _run_lean, "drt": _run_drt, "command": _run_command}


def run_check(config: Config, check: Check) -> Outcome:
    root = config.root
    covers = ledger.snapshot(root, check.covers)
    specs = ledger.snapshot(root, spec_files(check))
    try:
        outcome = _RUNNERS[check.kind](root, check)
    except (ToolMissing, ConfigError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        outcome = Outcome(check, "error", str(exc), [])
    # Hash before and after: a file edited mid-run must not be recorded as verified.
    if ledger.snapshot(root, check.covers) != covers or ledger.snapshot(root, spec_files(check)) != specs:
        outcome = Outcome(check, "error", "files changed while the check ran; re-run it", outcome.lines)
    ledger.append(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict=outcome.verdict,
        covers=covers,
        specs=specs,
        summary=outcome.summary,
        details={"lines": outcome.lines[:60]},
    )
    return outcome


def statuses(config: Config) -> list[ledger.Status]:
    return [ledger.status(config.root, c.id, c.covers, spec_files(c)) for c in config.checks]
