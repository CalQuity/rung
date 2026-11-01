"""Select checks, run them through their checkers, and record verdicts in the ledger."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from rung.checks import registry
from rung.checks.base import Context, Outcome
from rung.core import git, ledger
from rung.core.config import Check, Config
from rung.core.errors import RungError

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
    chosen = [config.check(i) for i in ids] if ids else list(config.checks)
    if changed is not None:
        chosen = [c for c in chosen if touched_by(c, changed)]
    return chosen


def run_check(config: Config, check: Check, *, base: str | None = None) -> Result:
    root = config.root
    checker = registry.get(check.kind)
    cover_patterns, spec_patterns = checker.covers(check), checker.specs(check)
    before = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    try:
        outcome = checker.run(Context(root=root, base=base), check)
    except (RungError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        outcome = Outcome("error", str(exc))
    # Hash before and after: a file edited mid-run must not be recorded as verified.
    after = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    if after != before:
        outcome = Outcome("error", "files changed while the check ran; re-run it", outcome.lines)
    ledger.append(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict=outcome.verdict,
        covers=before[0],
        specs=before[1],
        summary=outcome.summary,
        evidence=[f"{path} sha256:{digest}" for path, digest in outcome.evidence.items()],
        definition=definition(check),
        details={
            "lines": outcome.lines[:60],
            "base": base or "",
            "findings": outcome.findings[:MAX_RECORDED_FINDINGS],
        },
    )
    return Result(check, outcome)


def status(config: Config, check: Check) -> ledger.Status:
    return ledger.status(config.root, check.id, covers(check), specs(check), definition(check))


def statuses(config: Config) -> list[ledger.Status]:
    return [status(config, c) for c in config.checks]
