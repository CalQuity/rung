"""An explainable snapshot of configured-check health, not a grade for the codebase.

Every declared check has equal weight. Only a fresh passing verdict earns credit;
absent verification categories and unmeasured test strength remain visible separately.
"""

from __future__ import annotations

import hashlib
import json
import math
import shlex
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from rung.analysis import kit, report
from rung.checks import runner
from rung.checks.command import package_script_commands
from rung.core import launch
from rung.core.config import Check, Config
from rung.core.redact import redact

FORMULA = "100 * fresh passing checks / configured checks (rounded to the nearest whole number)"
LIMITATION = (
    "This is configured-check health, not an overall code-quality score. It does not measure test strength, "
    "code coverage, maintainability or the absence of bugs. Removing checks changes the denominator; "
    "compare scores only for the same check definitions."
)
STATE_TEXT = {
    "fresh": "Passed; the recorded evidence still matches its inputs.",
    "failing": "The check failed or could not complete successfully.",
    "stale": "Inputs, artifacts or validity conditions changed; the previous result no longer applies.",
    "never": "This check has not produced a recorded result yet.",
    "drifted": "The source changed since its formal model was confirmed to describe it.",
}


def build(
    config: Config,
    *,
    ran_checks: bool,
    elapsed_seconds: float | None = None,
    durations: dict[str, float] | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    """Build a redacted, versioned score report using the existing freshness rules."""
    data = report.build(config, base=base)
    statuses = {s.check_id: s for s in runner.statuses(config, base=base)}
    definitions = {check.id: check for check in config.checks}
    for row in data["checks"]:
        check = definitions[row["id"]]
        held = statuses[check.id]
        entry = held.entry or {}
        valid = "invalid" not in entry
        row.update(
            state=held.state,
            changed=held.changed,
            category=kit.part_of(check),
            verdict=entry.get("verdict") if valid else None,
            explanation=STATE_TEXT[held.state],
            diagnostics=entry.get("details", {}).get("lines", []) if valid else [],
            elapsed_seconds=_seconds((durations or {}).get(check.id)),
            recorded_elapsed_seconds=_seconds(entry.get("details", {}).get("execution_seconds")) if valid else None,
            next_step=_next_step(check.id, held.state, entry.get("verdict") if valid else None),
            test_results=entry.get("details", {}).get("test_results") if valid else None,
            assessment=_assessment(check.kind, held.state, entry.get("verdict")),
        )
        if row["assessment"] == "review_required":
            row["explanation"] = (
                "This change adds a test skip or a way to suppress a check. It needs review; "
                "it is not a confirmed application bug. An intentional exception still needs a documented decision."
            )
    counts = Counter(row["state"] for row in data["checks"])
    fresh, total = counts["fresh"], len(config.checks)
    data.update(
        generated_at=datetime.now(UTC).isoformat(),
        ran_checks=ran_checks,
        elapsed_seconds=elapsed_seconds,
        comparison_base=base,
        score={
            "value": int(100 * fresh / total + 0.5) if total else None,
            "fresh_passing": fresh,
            "configured": total,
            "formula": FORMULA,
            "limitation": LIMITATION,
        },
        check_set_digest=_check_set_digest(config),
        categories=_categories(data["checks"]),
        assessments=_assessments(config),
        test_results=_test_results(data["checks"]),
        stats={
            "fresh": fresh,
            "total": total,
            "failed": sum(r["state"] == "failing" and r["verdict"] == "fail" for r in data["checks"]),
            "errors": sum(r["state"] == "failing" and r["verdict"] == "error" for r in data["checks"]),
            "stale": counts["stale"],
            "never": counts["never"],
            "drifted": counts["drifted"],
            "review_required": sum(r["assessment"] == "review_required" for r in data["checks"]),
        },
        passed=bool(total) and fresh == total and data["ledger_intact"],
    )
    data["timing"] = _timing(data["checks"], ran_checks=ran_checks, elapsed_seconds=elapsed_seconds)
    data["test_metrics"] = _test_metrics(data["test_results"])
    for finding in data["new_findings"]:
        finding["current"] = statuses[finding["check"]].state in {"fresh", "failing"}
        finding["explanation"] = _finding_explanation(finding.get("rule", ""))
    data["summary"]["checks_fresh"] = fresh
    return _redact(data)


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _timing(rows: list[dict[str, Any]], *, ran_checks: bool, elapsed_seconds: float | None) -> dict[str, Any]:
    measured = []
    for row in rows:
        if row["source"] == "external_capture" or (not ran_checks and row["state"] not in {"fresh", "failing"}):
            continue
        seconds = row["elapsed_seconds"] if ran_checks else row["recorded_elapsed_seconds"]
        if seconds is not None:
            measured.append({"id": row["id"], "seconds": seconds, "verdict": row["verdict"], "at": row["at"]})
    measured.sort(key=lambda row: (-row["seconds"], row["id"]))
    total = sum(row["seconds"] for row in measured)
    for row in measured:
        row["share_percent"] = round(100 * row["seconds"] / total, 1) if total else 0.0
    return {
        "mode": "current" if ran_checks else "recorded",
        "wall_seconds": elapsed_seconds if ran_checks else None,
        "measured_checks": len(measured),
        "unmeasured_checks": len(rows) - len(measured),
        "total_check_seconds": round(total, 6) if measured else None,
        "average_seconds": total / len(measured) if measured else None,
        "median_seconds": median(row["seconds"] for row in measured) if measured else None,
        "failed_check_seconds": sum(row["seconds"] for row in measured if row["verdict"] in {"fail", "error"})
        if measured
        else None,
        "slowest_check": {"id": measured[0]["id"], "seconds": measured[0]["seconds"]} if measured else None,
        "checks": measured,
    }


def _test_metrics(results: dict[str, int] | None) -> dict[str, Any] | None:
    if results is None:
        return None
    executed = results["passed"] + results["failed"] + results["errors"]
    total = executed + results["skipped"]
    return {
        "total_cases": total,
        "executed_cases": executed,
        "pass_rate_percent": round(100 * results["passed"] / executed, 1) if executed else None,
        "skip_rate_percent": round(100 * results["skipped"] / total, 1) if total else None,
        "reporting_suites": results["suites"],
    }


def _assessment(kind: str, state: str, verdict: str | None) -> str:
    if state == "fresh":
        return "passed"
    if state != "failing":
        return "unverified"
    if verdict == "error":
        return "execution_error"
    return "review_required" if kind == "shortcuts" else "failed"


def _assessments(config: Config) -> dict[str, str]:
    scans = {c.get("tool") for c in config.checks if c.kind == "scan" and not c.get("run")}
    return {
        "static_analysis": "configured"
        if any(c.kind == "command" and _static_command(config.root, c) for c in config.checks)
        else "not_configured",
        "secret_scanning": "configured" if scans & {"gitleaks", "trivy"} else "not_configured",
        "vulnerability_scanning": "configured" if scans & {"osv-scanner", "trivy"} else "not_configured",
        "runtime": "configured" if any(c.kind == "evidence" for c in config.checks) else "not_configured",
        "test_strength": "not_assessed",
        "line_coverage": "not_assessed",
    }


def _static_command(root: Path, check: Check) -> bool:
    argv, cwd = shlex.split(check.get("run", "")), root / check.get("cwd", ".")
    while inner := launch.delegated(argv, cwd):
        argv, cwd = inner.argv, inner.cwd
    candidates = [argv, *package_script_commands(cwd, argv)]
    for command in candidates:
        if not command or any(x in command for x in ("--version", "--help", "-V", "-h")):
            continue
        if target := launch.python_entrypoint(command):
            if target[0] != "module":
                continue
            tool = target[1]
            options = command[command.index("-m") + 2 :] if "-m" in command else command[2:]
        else:
            tool, options = Path(command[0]).name.removesuffix(".exe"), command[1:]
        if tool in {"pyright", "mypy", "tsc", "eslint", "prettier"}:
            return True
        if (tool == "ruff" and "check" in options) or (tool == "go" and options[:1] == ["vet"]):
            return True
        if tool == "cargo" and "clippy" in options:
            return True
    return False


def _test_results(rows: list[dict[str, Any]]) -> dict[str, int] | None:
    measured = [r["test_results"] for r in rows if r["state"] in {"fresh", "failing"} and r["test_results"]]
    if not measured:
        return None
    return {
        **{k: sum(r[k] for r in measured) for k in ("passed", "failed", "errors", "skipped")},
        "suites": len(measured),
    }


def _finding_explanation(rule: str) -> str:
    if rule == "skipped-test":
        return (
            "A test is skipped. Review its reason: an unsupported feature may justify it, "
            "but a regression may be hidden."
        )
    if rule in {"noqa", "type-ignore", "lint-disable"}:
        return "A check is suppressed here. Review whether the exception is intentional, necessary and narrowly scoped."
    return "Review this tool finding in its source context before deciding whether it indicates a product defect."


def _next_step(check_id: str, state: str, verdict: str | None) -> str:
    if state == "fresh":
        return "No action needed for this check."
    if state == "drifted":
        return f"Review the source/model changes, then use rung maintain {check_id}."
    if verdict == "error" and state == "failing":
        return f"Run rung doctor --strict, resolve the execution error, then run rung check {check_id}."
    if state == "failing":
        return f"Review the reported behavior and check assertions, then run rung check {check_id}."
    return f"Run rung check {check_id} to obtain a current result."


def _categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories = []
    for key, title, catches in kit.PARTS:
        if key == "agents":  # agent setup is guidance, not an executed verification check
            continue
        checks = [r for r in rows if r["category"] == key]
        if key == "static":
            title, catches = "Static checks and guardrails", "lint/type rules and changes that weaken verification"
        elif key == "security":
            title = "Dependency and security checks"
        categories.append(
            {
                "id": key,
                "title": title,
                "description": catches,
                "configured": len(checks),
                "fresh_passing": sum(r["state"] == "fresh" for r in checks),
                "checks": [r["id"] for r in checks],
            }
        )
    return categories


def _check_set_digest(config: Config) -> str:
    definitions = sorted((c.id, runner.definition(c)) for c in config.checks)
    return hashlib.sha256(json.dumps(definitions).encode()).hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value
