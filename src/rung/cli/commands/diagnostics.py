"""Explicit diagnostics; normal check verdicts and merge policy are unchanged."""

from __future__ import annotations

import math
from typing import Any

from rung.analysis import coverage, flaky
from rung.cli.commands.common import load_config
from rung.cli.output import Output
from rung.core import git
from rung.core.config import ConfigError


def _positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be finite and greater than zero")


def cmd_coverage(args, out: Output) -> int:
    if not args.source:
        raise ConfigError("coverage requires at least one --source project-relative directory")
    _positive(args.timeout, "--timeout")
    if args.fail_under is not None and (not math.isfinite(args.fail_under) or not 0 <= args.fail_under <= 100):
        raise ConfigError("--fail-under must be a finite percentage from 0 to 100")
    cfg = load_config(args)
    result = coverage.run(
        cfg,
        cfg.check(args.check),
        sources=args.source,
        base=args.base or git.default_base(cfg.root),
        fail_under=args.fail_under,
        timeout=args.timeout,
    )
    return _display(result, out)


def cmd_flaky(args, out: Output) -> int:
    if args.attempts < 2:
        raise ConfigError("--attempts must be at least 2")
    _positive(args.budget, "--budget")
    cfg = load_config(args)
    result = flaky.run(
        cfg,
        cfg.check(args.check),
        attempts=args.attempts,
        budget_seconds=args.budget,
        fail_on_flaky=args.fail_on_flaky,
    )
    return _display(result, out)


def _display(result: dict[str, Any], out: Output) -> int:
    out.emit(result)
    out.heading("Test diagnostic", result.get("check_id", ""))
    out.line(result["summary"])
    for error in [*result.get("errors", []), *result.get("warnings", [])]:
        out.detail(error)
    for item in result.get("files", []):
        if item.get("unmeasured") or item.get("missing_lines"):
            description = "unmeasured" if item.get("unmeasured") else f"missing lines {item['missing_lines']}"
            out.detail(f"{item['path']}: {description}")
    for item in result.get("cases", []):
        if item.get("classification") != "no_variability":
            out.detail(f"{item['id']}: {item['classification']} · {item['outcomes']}")
    if result.get("manifest_path"):
        out.location(result["manifest_path"], label="Evidence: ", indent=0)
    out.line("Normal check verdicts were not replaced. View saved diagnostics with rung score-my-code --no-run.")
    return result["exit_code"]
