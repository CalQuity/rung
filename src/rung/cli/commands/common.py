"""Helpers shared by the command modules."""

from __future__ import annotations

import sys
from pathlib import Path

from rung.checks import registry
from rung.cli.output import Output
from rung.core import git
from rung.core.config import CONFIG_NAME, Check, Config, find_root, load, save

OK, GATE_FAILED = 0, 1
OK, GATE_FAILED = 0, 1


def load_config(args) -> Config:
    return load(find_root(Path(args.dir)))


def ask(question: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    print(f"{question}{suffix}: ", end="", file=sys.stderr, flush=True)
    return input().strip() or default


def resolve_base(root: Path, base: str | None) -> str:
    """A merge base commit for `base` (or the default branch)."""
    return git.merge_base(root, base or git.default_base(root))


def add_check(config: Config, check: Check, out: Output) -> None:
    registry.get(check.kind)  # validate the kind before writing anything
    replaced = any(c.id == check.id for c in config.checks)
    config.checks = [c for c in config.checks if c.id != check.id] + [check]
    save(config)
    out.emit({"check": check.id, "kind": check.kind, "covers": check.covers, "options": check.options,
              "replaced": replaced})  # fmt: skip
    out.line(f"{'Updated' if replaced else 'Added'} check {check.id!r} ({check.kind}) in {CONFIG_NAME}.")
