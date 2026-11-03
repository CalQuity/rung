"""Select checks, run them through their checkers, and record verdicts in the ledger."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rung.checks import registry
from rung.checks.base import Context, Outcome
from rung.checks.command import evidence_dir, is_attested, missing_required
from rung.core import git, ledger
from rung.core.config import Check, Config, ConfigError
from rung.core.errors import RungError
from rung.core.redact import redact, redact_lines

MAX_RECORDED_FINDINGS = 200


@dataclass
class Result:
    check: Check
    outcome: Outcome

    @property
    def verdict(self) -> str:
        return self.outcome.verdict


def covers(check: Check) -> list[str]:
    return registry.get(check.kind).covers(check)


def specs(check: Check) -> list[str]:
    return registry.get(check.kind).specs(check)


def definition(check: Check) -> str:
    return ledger.definition_hash(check.kind, check.covers, check.options)


def touched_by(check: Check, changed: set[str]) -> bool:
    """Does a change to `changed` files affect this check? Checks covering nothing always run."""
    patterns = covers(check) + specs(check)
    if not patterns:
        return True
    return any(git.glob_match(p, f) for p in patterns for f in changed)


def select(config: Config, ids: list[str], changed: set[str] | None) -> list[Check]:
    """Checks to run: all (or the named ones), narrowed to those a change touches.

    A check that is not currently fresh is always selected, so editing a check's own declaration,
    a baseline or a waiver, or breaking the ledger, can never be skipped by `--changed`.
    """
    chosen = [config.check(i) for i in ids] if ids else list(config.checks)
    if changed is None:
        return chosen
    view = ledger.View(config.root)
    return [c for c in chosen if touched_by(c, changed) or not status(config, c, view=view).trusted]


def run_check(
    config: Config, check: Check, *, base: str | None = None, seed: int | None = None, scenarios: tuple[str, ...] = ()
) -> Result:
    """Run one check and record its verdict.

    With `seed` or `scenarios`, a scripted evidence check runs that one seed or those scenarios only, to
    reproduce a failure. Such a run does not stand for the check as declared, so it is not recorded.
    """
    root = config.root
    partial = seed is not None or bool(scenarios)
    if partial and (check.kind != "evidence" or is_attested(check)):
        raise ConfigError(f"--seed and --scenario apply to evidence checks with `run`, not {check.id!r} ({check.kind})")
    if is_attested(check) and (held := status(config, check)).trusted and held.entry:
        # Attested evidence cannot be re-run; while its files are unchanged, the attestation stands.
        return Result(check, Outcome("pass", held.entry.get("summary", "attested"), held.entry.get("evidence", [])))
    checker = registry.get(check.kind)
    cover_patterns, spec_patterns = checker.covers(check), checker.specs(check)
    before = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    try:
        outcome = checker.run(Context(root=root, base=base, seed=seed, scenarios=scenarios), check)
    except (RungError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        outcome = Outcome("error", str(exc))
    # Hash before and after: a file edited mid-run must not be recorded as verified.
    after = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    if after != before:
        moved = sorted(p for b, a in zip(before, after, strict=True) for p in set(b) | set(a) if b.get(p) != a.get(p))
        hint = (
            "files it covers changed while it ran: " + ", ".join(moved[:5]) + (" ..." if len(moved) > 5 else "")
            + ". If the check writes them itself (coverage, reports), add them to .gitignore or narrow `covers`."
        )  # fmt: skip
        outcome = Outcome("error", hint, outcome.lines, details=outcome.details)
    if partial:
        return Result(check, outcome)
    ledger.record(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict=outcome.verdict,
        covers=before[0],
        specs=before[1],
        summary=redact(outcome.summary),
        evidence=[f"{path} sha256:{digest}" for path, digest in outcome.evidence.items()],
        definition=definition(check),
        context=base or "",
        valid_until=outcome.valid_until,
        details={
            "lines": redact_lines(outcome.lines[:60]),
            "base": base or "",
            "findings": outcome.findings[:MAX_RECORDED_FINDINGS],
            **outcome.details,
        },
    )
    return Result(check, outcome)


def status(config: Config, check: Check, *, base: str | None = None, view: ledger.View | None = None) -> ledger.Status:
    """Whether `check`'s latest verdict still holds. With `base`, it must have been made against that base."""
    view = view or ledger.View(config.root)
    max_age = registry.get(check.kind).max_age_days(check)
    return view.status(check.id, covers(check), specs(check), definition(check), base, max_age)


def statuses(config: Config, *, base: str | None = None) -> list[ledger.Status]:
    view = ledger.View(config.root)
    return [status(config, c, base=base, view=view) for c in config.checks]


def attest(config: Config, check: Check, files: list[Path], *, by: str, note: str = "") -> Result:
    """Record evidence an agent or a person captured (a browser recording, screenshots) for an attested check.

    The files are copied into `.rung/evidence/` and hashed into the verdict, which binds them to the
    code as it is now. Nothing is re-run: the verdict says who recorded what, not that Rung saw it work.
    """
    if not is_attested(check):
        raise ConfigError(
            f'check {check.id!r} is not an attested evidence check (kind = "evidence" without `run`); '
            "scripted checks record their own evidence with `rung check`"
        )
    if not by.strip():
        raise ConfigError("say which tool or person recorded the evidence with --by")
    empty = [str(f) for f in files if not f.is_file() or f.stat().st_size == 0]
    if empty:
        raise ConfigError(f"not a non-empty file: {', '.join(empty)}")
    if missing := missing_required(check, (f.name for f in files)):
        raise ConfigError(f"check {check.id!r} requires evidence ending in {', '.join(missing)}")
    root = config.root
    cover_patterns, spec_patterns = covers(check), specs(check)
    before = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    out_dir = evidence_dir(root, check.id)
    artifacts: dict[str, str] = {}
    for i, f in enumerate(files):
        target = out_dir / f"{i + 1:02d}-{f.name}"
        shutil.copyfile(f, target)
        artifacts[target.relative_to(root).as_posix()] = ledger.file_digest(target)
    if (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns)) != before:
        raise RungError("files the check covers changed while the evidence was being recorded; attest again")
    summary = redact(f"attested by {by.strip()}: {len(artifacts)} artifact(s), not re-run by Rung")
    lines = [f"evidence: {path}" for path in artifacts] + ([redact(f"note: {note}")] if note else [])
    ledger.record(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict="pass",
        covers=before[0],
        specs=before[1],
        summary=summary,
        evidence=[f"{path} sha256:{digest}" for path, digest in artifacts.items()],
        definition=definition(check),
        details={"lines": lines, "attested_by": by.strip(), "note": redact(note)},
    )
    return Result(check, Outcome("pass", summary, lines, artifacts))
