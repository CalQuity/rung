"""The checker interface every check kind implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from rung.config import Check


@dataclass
class Context:
    """What a checker may know about the run beyond its own check."""

    root: Path
    # Commit to compare against for "what did this change introduce" (a merge base), if any.
    base: str | None = None


@dataclass
class Outcome:
    verdict: str  # pass | fail | error
    summary: str
    lines: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # artifact path -> sha256
    findings: list[dict[str, Any]] = field(default_factory=list)


class Checker:
    """Turns one declared check into a verdict.

    Subclasses set `kind`, implement `run`, and may override `specs` and
    `default_covers`. A checker must never return `pass` unless its tool
    actually confirmed the result; when unsure it returns `error`.
    """

    kind: ClassVar[str] = ""
    summary: ClassVar[str] = ""

    def specs(self, check: Check) -> list[str]:
        """Spec-side files whose change should stale this check's verdict."""
        return [str(s) for s in check.get("specs", [])]

    def default_covers(self, check: Check) -> list[str]:
        return []

    def covers(self, check: Check) -> list[str]:
        return check.covers or self.default_covers(check)

    def run(self, ctx: Context, check: Check) -> Outcome:
        raise NotImplementedError
