"""Run declared checks and record their verdicts in the ledger."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    evidence: dict[str, str] = field(default_factory=dict)  # artifact path -> sha256


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


def _resolve_tools(command: str) -> str:
    """Point a leading `lake` at the elan install, which is often not on PATH."""
    parts = shlex.split(command)
    if parts and parts[0] == "lake":
        parts[0] = lean.find_lake()
    return shlex.join(parts)


def _run_drt(root: Path, check: Check) -> Outcome:
    model = drt.Side(_resolve_tools(check.require("model")), root / check.get("model_cwd", "."))
    impl = drt.Side(_resolve_tools(check.require("impl")), root / check.get("impl_cwd", "."))
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


def _run_evidence(root: Path, check: Check) -> Outcome:
    """Run a script that drives the real app and must leave artifacts behind.

    The script gets `RUNG_EVIDENCE_DIR`; anything it writes there (a Playwright
    video, a terminal transcript, screenshots, response bodies) is hashed into
    the ledger. A script that exits 0 but records nothing fails: a claim of
    "it works" without evidence is exactly what this rung exists to reject.
    """
    command = check.require("run")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / ".rung" / "evidence" / check.id / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        shlex.split(command),
        cwd=root / check.get("cwd", "."),
        env={**os.environ, "RUNG_EVIDENCE_DIR": str(out_dir)},
        capture_output=True,
        text=True,
        timeout=int(check.get("timeout", 1800)),
    )
    (out_dir / "run.log").write_text(proc.stdout + proc.stderr)
    artifacts = {
        p.relative_to(root).as_posix(): ledger.file_digest(p)
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() and p.name != "run.log"
    }
    lines = [f"evidence: {path}" for path in artifacts]
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-10:]
        return Outcome(check, "fail", f"`{command}` exited {proc.returncode}", lines + tail, artifacts)
    if not artifacts:
        return Outcome(check, "fail", "the script exited 0 but recorded no evidence", lines)
    return Outcome(check, "pass", f"{len(artifacts)} artifact(s) recorded", lines, artifacts)


_RUNNERS = {
    "tla": _run_tla,
    "lean": _run_lean,
    "drt": _run_drt,
    "command": _run_command,
    "evidence": _run_evidence,
}


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
        evidence=[f"{path} sha256:{digest}" for path, digest in outcome.evidence.items()],
        details={"lines": outcome.lines[:60]},
    )
    return outcome


def statuses(config: Config) -> list[ledger.Status]:
    return [ledger.status(config.root, c.id, c.covers, spec_files(c)) for c in config.checks]
