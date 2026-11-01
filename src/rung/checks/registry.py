"""Check kinds. Each is a `Checker`; `get(kind)` returns the one for a declared check."""

from __future__ import annotations

from rung.checks.base import Checker, Context, Outcome
from rung.checks.command import CommandChecker, EvidenceChecker
from rung.checks.deps import DepsChecker
from rung.checks.formal import DRTChecker, LeanChecker, TLAChecker
from rung.checks.scan import ScanChecker
from rung.core.config import ConfigError

REGISTRY: dict[str, Checker] = {
    c.kind: c
    for c in (
        CommandChecker(),
        EvidenceChecker(),
        ScanChecker(),
        DepsChecker(),
        TLAChecker(),
        LeanChecker(),
        DRTChecker(),
    )
}


def get(kind: str) -> Checker:
    try:
        return REGISTRY[kind]
    except KeyError:
        raise ConfigError(f"unknown check kind {kind!r}; choose from {', '.join(REGISTRY)}") from None


__all__ = ["REGISTRY", "Checker", "Context", "Outcome", "get"]
