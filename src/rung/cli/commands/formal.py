"""Commands that keep formal models describing the code: maintain and confirm, and accepting a draft.

These are the formal-methods part of a project's verification kit. Each `tla` or `lean` check has a
record (`rung.core.modelcard`) of the code its model was last confirmed to describe, and a project
skill, `.claude/skills/verify-<check>/SKILL.md`, that tells a coding agent how to run, read and
maintain it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from rung.agents.install import install_formal_skill
from rung.checks import runner
from rung.cli.commands.common import GATE_FAILED, OK, add_check, load_config
from rung.cli.output import VERDICT_STYLE, Output
from rung.core import laws, modelcard
from rung.core.config import Check, Config, ConfigError
from rung.drafting import maintain, providers
from rung.drafting.common import DraftResult

OUTCOME_STYLE = {
    "in-sync": ("IN SYNC", "green"),
    "updated": ("UPDATED", "green"),
    "bug": ("BUG", "red"),
    "blocked": ("BLOCKED", "yellow"),
}


def _model_files(cfg: Config, check: Check) -> list[str]:
    if check.kind == "tla":
        return [check.require("spec"), *([check.get("config")] if check.get("config") else [])]
    project = cfg.root / check.require("project")
    files = [f for f in sorted(project.rglob("*")) if f.is_file() and ".lake" not in f.relative_to(project).parts
             and (f.suffix == ".lean" or f.name == "drt_adapter.py")]  # fmt: skip
    return [f.relative_to(cfg.root).as_posix() for f in files] or [check.require("project")]


def write_skill(cfg: Config, check: Check, card: dict[str, Any]) -> Path:
    enforced = [(law.text, law.harm) for law in laws.load(cfg.root) if check.id in law.enforced_by]
    return install_formal_skill(cfg.root, cfg.name, check.id, check.kind, card, _model_files(cfg, check), enforced)


def _sources(cfg: Config, check: Check) -> list[str]:
    """The code a model describes: the check's own `covers`, or for Lean, its differential test's."""
    if check.covers:
        return list(check.covers)
    sibling = next((c for c in cfg.checks if c.id == f"{check.id}-drt"), None)
    return list(sibling.covers) if sibling else []


def _print_result(result: runner.Result, out: Output) -> None:
    o = result.outcome
    out.line(f"{out.badge(o.verdict, VERDICT_STYLE, 6)}{result.check.id} ({result.check.kind}): {o.summary}")
    for line in o.lines:
        out.detail(line)


def _describe(card: dict[str, Any], out: Output, *, scope: bool = True) -> None:
    for line in modelcard.scope_lines(card) if scope else []:
        out.detail(f"scope: {line}", indent=2)
    if line := modelcard.failability_line(card):
        out.detail(line, indent=2)
    commit = (card.get("confirmed") or {}).get("commit", "")
    out.detail(f"confirmed: the model describes the code at {commit or 'an uncommitted state'}", indent=2)


def accept_draft(cfg: Config, check: Check, result: DraftResult, out: Output, *, sources: list[str], by: str) -> int:
    """Record what an accepted draft describes and the code it describes, write its skill, and check it."""
    card = modelcard.load(cfg.root, check.id) or modelcard.new(check.id, check.kind, sources)
    card["sources"] = list(sources)
    modelcard.describe(card, result.described)
    card["failability"] = result.failability
    modelcard.confirm(cfg.root, card, by=by, reason="the draft was accepted", outcome="drafted")
    path = modelcard.save(cfg.root, card)
    out.line(f"Recorded the code this model describes in {path.relative_to(cfg.root).as_posix()}:")
    _describe(card, out, scope=check.kind != "tla")  # the TLA+ check below prints the scope
    skill = write_skill(cfg, check, card)
    out.line(f"wrote {skill.relative_to(cfg.root).as_posix()} so agents run, read and maintain `{check.id}`.")
    if check.kind != "tla":
        return OK
    # The draft was accepted either way; the verdict records whether the model checks (a counterexample fails it).
    _print_result(runner.run_check(cfg, check), out)
    return OK


def cmd_maintain(args, out: Output) -> int:
    cfg = load_config(args)
    check = cfg.check(args.check)
    if check.kind not in modelcard.KINDS:
        raise ConfigError(f"`rung maintain` works on tla and lean checks; {check.id!r} is a {check.kind} check")
    card = modelcard.load(cfg.root, check.id)
    if not card or not (card.get("confirmed") or {}).get("digest"):
        raise ConfigError(
            f"{check.id!r} has no record of the code its model describes. Review the model, then: "
            f'rung confirm {check.id} --by "NAME" --reason "TEXT"'
        )
    provider = providers.get(replace(cfg.llm, model=args.model) if args.model else cfg.llm)
    changed = modelcard.changed_files(cfg.root, card)
    since = (card["confirmed"].get("commit") or "")[:7] or "an uncommitted state"
    if changed:
        out.line(f"Maintaining {check.id}: {len(changed)} changed file(s) since {since}, with {cfg.llm.provider} ...")
    result = maintain.maintain(provider, cfg, check, card)
    by = f"rung maintain ({cfg.llm.provider}:{provider.models()})" if changed else "rung maintain"
    out.emit({"check": check.id, "outcome": result.outcome, "message": result.message, "changes": result.changes,
              "files": result.files, "rounds": result.rounds, "failability": result.failability,
              "story": result.story, "attempt": result.attempt, "confirmed": result.confirms})  # fmt: skip
    out.line(f"{out.badge(result.outcome, OUTCOME_STYLE, 8)}{check.id}: {result.message}")
    for change in result.changes:
        out.detail(f"[{change['classification']}] {change['change']}", indent=2)
        if change["classification"] == "promise-changed" and change["evidence"].strip():
            out.detail(f"evidence: {change['evidence'].strip()}", indent=4)
    for f in result.files:
        out.detail(f"updated {f}", indent=2)
    if result.failability:
        out.detail(f"fail-ability: {result.failability.get('summary', '')}", indent=2)
    for line in result.story:
        out.detail(line, indent=2)
    if not result.confirms:
        if result.outcome == "blocked":
            if result.attempt:
                out.detail(f"the last attempt is in {result.attempt}; the model files were left as they were", indent=2)
            out.line(f'next: fix the model by hand, then rung confirm {check.id} --by "NAME" --reason "TEXT"')
        else:
            out.line(f"next: fix the code, then rung check {check.id}")
        return GATE_FAILED

    modelcard.describe(card, result.reply)
    if result.failability:
        card["failability"] = result.failability
    counts: dict[str, int] = {}
    for change in result.changes:
        counts[change["classification"]] = counts.get(change["classification"], 0) + 1
    reason = result.outcome + "".join(f"; {n} {kind}" for kind, n in counts.items())
    modelcard.confirm(cfg.root, card, by=by, reason=reason, outcome=result.outcome)
    modelcard.save(cfg.root, card)
    theorems = [t.strip() for t in result.reply.get("theorems", []) if t.strip()]
    if check.kind == "lean" and theorems and theorems != check.get_list("theorems"):
        check = Check(check.id, check.kind, check.covers, {**check.options, "theorems": theorems})
        add_check(cfg, check, out)
    if any(c["classification"] == "promise-changed" for c in result.changes):
        out.line(f"A promise changed on purpose: check that the laws in {laws.LAWS_FILE} still say what you mean.")
    skill = write_skill(cfg, check, card)
    out.line(f"Confirmed the model describes the code at {card['confirmed']['commit'][:7] or 'this state'}; "
             f"updated {skill.relative_to(cfg.root).as_posix()}.")  # fmt: skip
    checked = runner.run_check(cfg, check)
    _print_result(checked, out)
    return OK if result.outcome != "bug" and checked.verdict == "pass" else GATE_FAILED


def cmd_confirm(args, out: Output) -> int:
    cfg = load_config(args)
    check = cfg.check(args.check)
    if check.kind not in modelcard.KINDS:
        raise ConfigError(f"`rung confirm` works on tla and lean checks; {check.id!r} is a {check.kind} check")
    by, reason = (args.by or "").strip(), (args.reason or "").strip()
    if not by or not reason:
        raise ConfigError("say who reviewed the model with --by and what they checked with --reason")
    card = modelcard.load(cfg.root, check.id) or modelcard.new(check.id, check.kind, _sources(cfg, check))
    if not card["sources"]:
        raise ConfigError(f"{check.id!r} covers no source files, so there is no code to confirm it against; "
                          "add the files it models to its `covers` in rung.toml")  # fmt: skip
    modelcard.confirm(cfg.root, card, by=by, reason=reason, outcome="confirmed by a person")
    path = modelcard.save(cfg.root, card)
    skill = write_skill(cfg, check, card)
    commit = card["confirmed"]["commit"][:7] or "this uncommitted state"
    out.emit({"check": check.id, "confirmed": card["confirmed"], "record": path.relative_to(cfg.root).as_posix(),
              "skill": skill.relative_to(cfg.root).as_posix()})  # fmt: skip
    out.line(f"Confirmed {check.id} describes the code at {commit}, by {by}: {reason}")
    out.detail(f"recorded in {path.relative_to(cfg.root).as_posix()}; updated {skill.relative_to(cfg.root).as_posix()}",
               indent=2)  # fmt: skip
    checked = runner.run_check(cfg, check)
    _print_result(checked, out)
    return OK if checked.verdict == "pass" else GATE_FAILED
