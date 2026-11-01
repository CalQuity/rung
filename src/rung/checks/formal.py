"""Formal checkers: TLA+ model checking, Lean proofs, and differential testing against a model."""

from __future__ import annotations

import shlex

from rung.checks.base import Checker, Context, Outcome
from rung.core.config import Check
from rung.engines import drt, lean, tla


class TLAChecker(Checker):
    kind = "tla"
    summary = "Model-check a TLA+ spec with TLC"

    def specs(self, check: Check) -> list[str]:
        files = [check.require("spec")]
        if cfg := check.get("config"):
            files.append(cfg)
        return files

    def run(self, ctx: Context, check: Check) -> Outcome:
        cfg = check.get("config")
        result = tla.run(
            ctx.root / check.require("spec"),
            ctx.root / cfg if cfg else None,
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
            return Outcome("fail", "spec reaches at most one state; invariants are vacuous", lines)
        return Outcome(result.verdict, result.reason, lines)


class LeanChecker(Checker):
    kind = "lean"
    summary = "Build a Lean project and audit its theorems' axioms"

    def specs(self, check: Check) -> list[str]:
        project = check.require("project").rstrip("/")
        return [f"{project}/**/*.lean", f"{project}/lakefile.*", f"{project}/lean-toolchain"]

    def run(self, ctx: Context, check: Check) -> Outcome:
        result = lean.run(
            ctx.root / check.require("project"),
            [str(t) for t in check.get("theorems", [])],
            timeout=int(check.get("timeout", 1800)),
        )
        lines = list(result.findings) + [f"WEAK {w}" for w in result.warnings]
        for theorem, axioms in result.axioms.items():
            lines.append(f"{theorem}: axioms [{', '.join(axioms) or 'none'}]")
        if result.verdict != "pass" and not result.findings:
            lines.append(result.output.strip()[-1500:])
        return Outcome(result.verdict, result.reason, lines)


def _resolve_tools(command: str) -> str:
    """Point a leading `lake` at the elan install, which is often not on PATH."""
    parts = shlex.split(command)
    if parts and parts[0] == "lake":
        parts[0] = lean.find_lake()
    return shlex.join(parts)


class DRTChecker(Checker):
    kind = "drt"
    summary = "Differentially test the implementation against an executable model"

    def run(self, ctx: Context, check: Check) -> Outcome:
        model = drt.Side(_resolve_tools(check.require("model")), ctx.root / check.get("model_cwd", "."))
        impl = drt.Side(_resolve_tools(check.require("impl")), ctx.root / check.get("impl_cwd", "."))
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
        return Outcome(result.verdict, result.reason, lines)
