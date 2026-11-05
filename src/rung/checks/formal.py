"""Formal checkers: TLA+ model checking, Lean proofs, and differential testing against a model.

A `tla` or `lean` check with a model record (`rung.core.modelcard`) first compares the code it covers
with the code the model was last confirmed to describe. When they differ the check is an error, not a
rerun: TLC passing on an unchanged spec says nothing about code the spec no longer describes.
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path
from typing import Any

from rung.checks.base import Checker, Context, Outcome
from rung.checks.command import evidence_dir
from rung.core import ledger, modelcard
from rung.core.config import Check, ConfigError
from rung.engines import drt, lean, tla, tla_mutate, tla_render


def _drift(root: Path, check: Check) -> tuple[dict[str, Any] | None, Outcome | None]:
    """The check's model record, and an error outcome when the code drifted from it."""
    card = modelcard.load(root, check.id)
    if card and (d := modelcard.drift(root, check.id, card)):
        details = {"drift": {"since": d.commit, "changed": d.changed}}
        return card, Outcome("error", d.message, d.lines(), details=details)
    return card, None


def _described(card: dict[str, Any] | None) -> list[str]:
    """Scope lines and the confirmed commit, for a check's output."""
    lines = [f"scope: {line}" for line in modelcard.scope_lines(card)]
    if modelcard.has_source_confirmation(card):
        commit = (card or {})["confirmed"].get("commit")
        lines.append(f"model confirmed to describe the code at {commit[:7] if commit else 'a local source state'}")
    else:
        lines.append("Model-only result. No implementation link has been confirmed.")
    return lines


def _write(directory: Path | None, root: Path, check_id: str, name: str, text: str) -> tuple[Path, Path]:
    directory = directory or evidence_dir(root, check_id)
    target = directory / name
    target.write_text(text)
    return directory, target


class TLAChecker(Checker):
    kind = "tla"
    summary = "Model-check a TLA+ spec with TLC"

    def specs(self, check: Check) -> list[str]:
        spec = Path(check.require("spec"))
        files = [*super().specs(check), spec.as_posix(), (spec.parent / "*.tla").as_posix()]
        if cfg := check.get("config"):
            files.append(cfg)
        return files

    def run(self, ctx: Context, check: Check) -> Outcome:
        cfg = check.get("config")
        if not cfg:
            raise ConfigError(f"check {check.id!r}: a TLA+ check needs a `config` (.cfg) listing what to check")
        declared = tla.declared_properties(ctx.root / cfg)
        if not declared["invariants"] and not declared["properties"]:
            return Outcome("fail", "the .cfg declares no INVARIANT or PROPERTY, so TLC checked nothing", [
                f"add e.g. `INVARIANT TypeOK` to {cfg}",
            ])  # fmt: skip
        card, drifted = _drift(ctx.root, check)
        if drifted:
            return drifted
        spec, config = ctx.root / check.require("spec"), ctx.root / cfg
        timeout = check.get_int("timeout", 600)
        result = tla.run(spec, config, timeout=timeout)
        lines = [f"{result.distinct_states} distinct states explored"] if result.distinct_states else []
        checked = declared["invariants"] + declared["properties"]
        lines.append("checked: " + ", ".join(checked))
        if declared["narrowing"]:
            lines.append("note: " + "; ".join(declared["narrowing"]) + " (narrows what a pass covers)")
        directory: Path | None = None
        artifacts: dict[str, str] = {}
        if result.trace:
            if card is not None:  # the model's own words for its actions and invariants
                lines += tla_render.story(result.trace, result.violated, card)
            lines.append(f"{'raw ' if card else ''}counterexample ({len(result.trace)} states):")
            lines.append(tla.render_trace(result.trace))
            if card is not None:
                diagram = tla_render.counterexample_mermaid(result.trace, result.violated, card)
                directory, target = _write(directory, ctx.root, check.id, "counterexample.mmd", diagram)
                artifacts[target.relative_to(ctx.root).as_posix()] = ledger.file_digest(target)
                lines.append(f"diagram: {target.relative_to(ctx.root).as_posix()}")
        elif result.verdict == "error":
            lines.append(result.output.strip()[-1500:])
        if result.verdict == "pass" and result.distinct_states <= 1:
            # A spec that reaches one state checks nothing; treat it as a spec bug.
            return Outcome("fail", "spec reaches at most one state; invariants are vacuous", lines)
        if result.verdict == "pass" and card is not None and result.distinct_states < tla_render.GRAPH_LIMIT:
            graph = _state_graph(spec, config, timeout, card)
            if graph:
                directory, target = _write(directory, ctx.root, check.id, "state-graph.mmd", graph)
                artifacts[target.relative_to(ctx.root).as_posix()] = ledger.file_digest(target)
                lines.append(f"state graph: {target.relative_to(ctx.root).as_posix()}")
        lines += _described(card)
        details: dict[str, Any] = {}
        if card is not None:
            digest = tla_mutate.spec_digest(spec, config)
            if fa_line := modelcard.failability_line(card, digest):
                lines.append(fa_line)
            details = {"scope": card.get("scope") or {}, "confirmed": (card.get("confirmed") or {}).get("commit", "")}
            if (card.get("failability") or {}).get("spec_digest") == digest:
                details["failability"] = {k: card["failability"][k] for k in ("summary", "caught", "survived")}
        if result.trace:
            lines.append(f"next: replay the steps against the code (a step the code cannot take: rung maintain "
                         f"{check.id}); if every step is real, fix the code, then rung check {check.id}")  # fmt: skip
        details["implementation_link"] = "source-confirmed" if modelcard.has_source_confirmation(card) else "model-only"
        return Outcome(result.verdict, result.reason, lines, artifacts, details=details)


class LeanChecker(Checker):
    kind = "lean"
    summary = "Build a Lean project and audit its theorems' axioms"

    def specs(self, check: Check) -> list[str]:
        project = Path(check.require("project"))
        files = ("**/*.lean", "lakefile.*", "lean-toolchain", "lake-manifest.json")
        return [*super().specs(check), *((project / name).as_posix() for name in files)]

    def run(self, ctx: Context, check: Check) -> Outcome:
        card, drifted = _drift(ctx.root, check)
        if drifted:
            return drifted
        result = lean.run(
            ctx.root / check.require("project"),
            check.get_list("theorems"),
            timeout=check.get_int("timeout", 1800),
        )
        lines = list(result.findings) + [f"WEAK {w}" for w in result.warnings]
        for theorem, axioms in result.axioms.items():
            lines.append(f"{theorem}: axioms [{', '.join(axioms) or 'none'}]")
        if result.verdict != "pass" and not result.findings:
            lines.append(result.output.strip()[-1500:])
        lines += _described(card)
        details = {"scope": card.get("scope") or {}} if card else {}
        details["implementation_link"] = "source-confirmed" if modelcard.has_source_confirmation(card) else "model-only"
        return Outcome(result.verdict, result.reason, lines, details=details)


def _state_graph(spec: Path, config: Path, timeout: int, card: dict[str, Any]) -> str:
    """A Mermaid diagram of every state TLC explores, from its DOT dump; empty if TLC gives none."""
    with tempfile.TemporaryDirectory(prefix="rung-graph-") as tmp:
        target = Path(tmp) / "states.dot"
        result = tla.run(spec, config, timeout=timeout, dump=target)
        if result.verdict != "pass" or not target.exists():
            return ""
        graph = tla_render.parse_dot(target.read_text())
    if not graph.nodes or len(graph.nodes) >= tla_render.GRAPH_LIMIT:
        return ""
    return tla_render.graph_mermaid(graph, card)


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
        cases, min_len, max_len = check.get_int("cases", 1000), check.get_int("min_len", 0), check.get_int("max_len", 8)
        if cases < 1:
            raise ConfigError(f"check {check.id!r}: `cases` must be at least 1; zero inputs prove nothing")
        if min_len > max_len:
            raise ConfigError(f"check {check.id!r}: `min_len` ({min_len}) is greater than `max_len` ({max_len})")
        model = drt.Side(_resolve_tools(check.require("model")), ctx.root / check.get("model_cwd", "."))
        impl = drt.Side(_resolve_tools(check.require("impl")), ctx.root / check.get("impl_cwd", "."))
        result = drt.run(
            model,
            impl,
            alphabet=[str(t) for t in check.require("alphabet")],
            cases=cases,
            min_len=min_len,
            max_len=max_len,
            seed=check.get_int("seed", 0),
            timeout=check.get_int("timeout", 600),
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
