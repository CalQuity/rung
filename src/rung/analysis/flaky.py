"""Explicit repeated pytest observations, separate from normal check verdicts."""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from rung.analysis import diagnostics
from rung.checks import prove
from rung.core import launch, proc
from rung.core.config import Check, Config, ConfigError

LIMITATIONS = [
    "No variability observed does not prove stability.",
    "Fresh processes do not reset external services, databases or files.",
    "The configured test order, parallelism and random seeds are not changed.",
    "Repeated subtest contexts are compared as groups; process-specific identities can make evidence incomplete.",
]


def _outcome(phases):
    names = [phase["phase"] for phase in phases]
    if len(names) != len(set(names)) or any(
        phase["phase"] not in {"setup", "call", "teardown"} or phase["outcome"] not in {"passed", "failed", "skipped"}
        for phase in phases
    ):
        return "error"
    if not {"setup", "teardown"} <= set(names):
        return "missing"
    if any(p["outcome"] == "failed" and p["phase"] != "call" for p in phases):
        return "error"
    if any(p.get("strict_xpass") for p in phases):
        return "xpass"
    if any(p.get("wasxfail") is not None and p["outcome"] == "passed" for p in phases):
        return "xpass"
    if any(p.get("wasxfail") is not None for p in phases):
        return "xfail"
    if any(p["outcome"] == "skipped" for p in phases):
        return "skipped"
    if {p["phase"] for p in phases} != {"setup", "call", "teardown"}:
        return "missing"
    return "failed" if any(p["outcome"] == "failed" for p in phases) else "passed"


def _classification(outcomes: list[str]) -> str:
    """Classify ordinary observations without letting skips erase a failure."""
    ordinary = set(outcomes) & {"passed", "failed"}
    if ordinary == {"passed", "failed"}:
        return "intermittent"
    if ordinary == {"failed"}:
        return "consistent_failure"
    if set(outcomes) & {"missing", "error"}:
        return "incomplete"
    return "no_variability" if ordinary == {"passed"} else "non_ordinary"


def _subtest_outcome(reports, parents):
    if not reports:
        return "error"
    values = []
    first = reports[0]
    for report in reports:
        if (
            report.get("parent_id") not in parents
            or report.get("phase") != "call"
            or report.get("outcome") not in {"passed", "failed", "skipped"}
            or report.get("parent_id") != first.get("parent_id")
            or report.get("context") != first.get("context")
        ):
            return "error"
        if report.get("strict_xpass"):
            values.append("xpass")
        elif report.get("wasxfail") is not None:
            values.append("xpass" if report["outcome"] == "passed" else "xfail")
        else:
            values.append(report["outcome"])
    # Repeated contexts describe a group, never invented individual identities.
    # Preserve any ordinary failure even when other group members skip or pass.
    if "failed" in values:
        return "failed"
    if "passed" in values:
        return "passed"
    if len(set(values)) == 1:
        return values[0]
    return "non_ordinary"


def _case_outcomes(identities, events, subtest_metadata=None):
    phases, subtests = defaultdict(list), defaultdict(list)
    for event in events:
        if event["event"] == "phase":
            phases[event["id"]].append(event)
        elif event["event"] == "subtest":
            subtests[event["id"]].append(event)
    outcomes = {identity: _outcome(phases[identity]) for identity in identities}
    parents = set(identities)
    for identity, reports in subtests.items():
        outcomes[identity] = "error" if identity in parents else _subtest_outcome(reports, parents)
        if subtest_metadata is not None:
            subtest_metadata[identity] = {
                "identity_scope": "subtest_group" if len(reports) > 1 else "subtest",
                "occurrences": len(reports),
                "parent_id": reports[0].get("parent_id"),
            }
    return outcomes


def _pytest_flags(argv: list[str], cwd: Path, flags: list[str]) -> list[str]:
    """Add pytest flags after launcher boundaries and before pytest's own --."""
    inner, inner_cwd = argv, cwd
    while delegated := launch.delegated(inner, inner_cwd):
        inner, inner_cwd = delegated.argv, delegated.cwd
    start = len(argv) - len(inner)
    if start < 0 or argv[start:] != inner:
        # uv run -m synthesizes an interpreter prefix absent from the original argv.
        start = max(index for index, value in enumerate(argv) if value == "pytest")
    separator = next((index for index in range(start, len(argv)) if argv[index] == "--"), len(argv))
    return [*argv[:separator], *flags, *argv[separator:]]


def run(
    config: Config,
    check: Check,
    *,
    attempts: int = 5,
    budget_seconds: float = 600,
    fail_on_flaky: bool = False,
) -> dict:
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 2:
        raise ConfigError("flaky attempts must be an integer of at least 2")
    if (
        isinstance(budget_seconds, bool)
        or not isinstance(budget_seconds, (int, float))
        or not math.isfinite(budget_seconds)
        or budget_seconds <= 0
    ):
        raise ConfigError("flaky budget must be a finite positive number")
    argv = shlex.split(check.require("run"))
    if check.kind != "command" or not prove.runs_pytest(argv):
        raise ConfigError("flaky requires a configured pytest command check")
    session = diagnostics.Session(
        config, check, "flaky", {"attempts": attempts, "budget_seconds": budget_seconds, "fail_on_flaky": fail_on_flaky}
    )
    plugin = session.path("rung_pytest_diagnostic.py")
    plugin.write_text(Path(__file__).parents[1].joinpath("checks/pytest_diagnostic.py").read_text())
    env = dict(os.environ)
    # Keep the copied reporter's generated bytecode out of retained text artifacts.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(session.directory) + os.pathsep + env.get("PYTHONPATH", "")
    cwd = config.root / check.get("cwd", ".")
    records, observations, errors = [], [], []
    subtest_observations = []
    deadline = time.monotonic() + budget_seconds
    for index in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("Total execution budget exhausted")
            break
        report, log = session.path(f"attempt-{index}.jsonl"), session.path(f"attempt-{index}.log")
        report.touch()
        env["RUNG_DIAGNOSTIC_EVENTS"] = str(report)
        # Preserve any prior configured JUnit receipt; diagnostics write only new reports.
        report_flag = "--junitxml=" + str(session.path(f"attempt-{index}.xml"))
        attempt_args = _pytest_flags(argv, cwd, ["-p", "rung_pytest_diagnostic", report_flag])
        start = time.monotonic()
        code, issue = None, ""
        events = []
        subtest_metadata = {}
        try:
            completed = proc.run(attempt_args, cwd=cwd, env=env, timeout=min(check.get_int("timeout", 1800), remaining))
            code = completed.returncode
            log.write_text(completed.stdout + "\n" + completed.stderr)
        except subprocess.TimeoutExpired as exc:
            issue = "Attempt timed out within the remaining budget"
            chunks = [
                v.decode(errors="replace") if isinstance(v, bytes) else (v or "") for v in (exc.stdout, exc.stderr)
            ]
            log.write_text("\n".join(chunks))
        except (OSError, KeyboardInterrupt) as exc:
            issue = f"Execution interrupted: {exc}"
            log.write_text(issue)
        try:
            events = [json.loads(line) for line in report.read_text().splitlines()]
            collected = next(e for e in events if e["event"] == "collection")
            finish = next(e for e in events if e["event"] == "finish")
            issues = [*collected["errors"], *finish["errors"]]
            if any(e["event"] == "collection_error" for e in events):
                issues.append("pytest collection failed")
            if code not in (0, 1) or finish["exit_code"] not in (0, 1):
                issues.append(f"pytest execution exit {code}")
            if not collected["ids"]:
                issues.append("pytest collected no test cases")
            outcomes = _case_outcomes(collected["ids"], events, subtest_metadata)
            if len(set(collected["ids"])) != len(collected["ids"]):
                issues.append("Duplicate pytest node IDs are unsupported")
            if any(o in ("error", "missing") for o in outcomes.values()):
                issues.append("Test lifecycle errors, ambiguous subtests or missing reports")
            if code == 1 and not any(o in ("failed", "error") for o in outcomes.values()):
                issues.append("pytest failed without a matching case failure")
            if issues:
                issue = "; ".join(dict.fromkeys([issue, *issues])).strip("; ")
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            events = []
            outcomes = {}
            subtest_metadata = {}
            issue = issue or "Missing or malformed pytest diagnostic report"
        environment = next((e for e in events if e["event"] == "configured"), {})
        state = "incomplete" if issue else "complete"
        if issue:
            errors.append(f"Attempt {index}: {issue}")
        observations.append(outcomes)
        subtest_observations.append(subtest_metadata)
        records.append(
            {
                "index": index,
                "exit_code": code,
                "duration_seconds": time.monotonic() - start,
                "state": state,
                "environment": environment,
                "subtests": subtest_metadata,
                "report": str(report.relative_to(config.root)),
                "log": str(log.relative_to(config.root)),
                "command": attempt_args,
                "cwd": str(cwd),
            }
        )
        if code is None or any("unsupported" in error for error in errors):
            break
    cases = []
    warnings = []
    for identity in sorted(set().union(*(set(o) for o in observations))):
        values = [o.get(identity, "missing") for o in observations]
        counts = [metadata.get(identity, {}).get("occurrences", 0) for metadata in subtest_observations]
        scope = "subtest_group" if any(count > 1 for count in counts) else "subtest" if any(counts) else "test"
        count_changed = scope == "subtest_group" and len(set(counts)) > 1
        if count_changed:
            errors.append("Subtest group occurrence count changed across attempts: " + identity)
        cases.append(
            {
                "id": identity,
                "outcomes": values,
                "classification": "incomplete" if count_changed else _classification(values),
                "classification_scope": "ordinary observations",
                "identity_scope": scope,
                "observation_counts": counts if scope != "test" else [int(identity in o) for o in observations],
                "incomplete": count_changed or bool(set(values) & {"missing", "error"}),
            }
        )
    if any(case["identity_scope"] == "subtest_group" for case in cases):
        warnings.append(
            "Repeated subtest contexts are compared as groups; group outcomes cannot identify "
            "which individual unnamed subtest varied. Every raw report and occurrence count is retained."
        )
    incomplete = sum(c["incomplete"] for c in cases)
    if incomplete and not errors:
        errors.append("Cases were missing or errored across attempts")
    if len(records) < attempts and not errors:
        errors.append("Requested attempts were not completed")
    flaky = sum(c["classification"] == "intermittent" for c in cases)
    failures = sum(c["classification"] == "consistent_failure" for c in cases)
    state = "incomplete" if errors else "complete"
    summary = f"{flaky} intermittent case(s); {failures} consistently failing case(s) among ordinary observations. "
    result = {
        "state": state,
        "summary": summary + f"No clean sample proves stability ({len(records)} attempt(s) retained).",
        "errors": errors,
        "warnings": warnings,
        "exit_code": 2 if errors else 1 if failures or (flaky and fail_on_flaky) else 0,
        "metrics": {
            "attempts_requested": attempts,
            "attempts_completed": sum(r["state"] == "complete" for r in records),
            "flaky_cases": flaky,
            "consistent_failures": failures,
            "incomplete_cases": incomplete,
            "cases": len(cases),
        },
        "attempts": records,
        "cases": cases,
        "limitations": LIMITATIONS,
    }
    return session.finish(result)
