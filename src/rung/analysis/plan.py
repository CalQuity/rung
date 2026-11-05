"""Recommend checks for a repository, cheapest and most widely useful first.

The ladder is applied by escalation, not by climbing every rung everywhere.
Every repository benefits from the cheap rungs: tests recorded as evidence,
security scanners gated on new findings, vetted dependencies, fix proofs.
Formal methods are recommended only where the survey finds code they pay off
for: TLA+ for stateful protocols, a Lean model for small pure cores.
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from rung.analysis.survey import Inspection
from rung.checks import runner
from rung.checks.presets import PRESETS
from rung.core import git
from rung.core.config import Check, safe_id

LADDER = (
    ("tests", "Record the test suite as evidence; prove that each bug fix's test catches the bug."),
    ("security", "Gate on new secrets, vulnerable or invented dependencies, and risky CI workflows."),
    ("architecture", "Make the wrong shortcut impossible: import boundaries and lint rules, in CI."),
    ("runtime", "Drive the real app like a user and keep the evidence: transcripts, screenshots, video."),
    ("protocol", "Model stateful code (queues, retries, leases, sync) in TLA+ and check every interleaving."),
    ("model", "Prove the laws of a small pure core in Lean and hold the real code to it on random inputs."),
)


@dataclass
class Recommendation:
    rung: str
    title: str
    why: str
    targets: list[str] = field(default_factory=list)
    effort: str = "low"
    command: str = ""


def uncovered_paths(checks: list[Check], paths: set[str], *, declared: bool = False) -> list[str]:
    """Changed paths with no declared scope for a command, runtime or formal check."""
    executing = [check for check in checks if check.kind in {"command", "evidence", "tla", "lean", "drt"}]

    def covers(check: Check, path: str) -> bool:
        if not runner.touched_by(check, {path}):
            return False
        if declared or "verifies" in check.options:
            return any(git.glob_match(pattern, path) for pattern in check.get("verifies", check.covers))
        return True

    return sorted(path for path in paths if not any(covers(check, path) for check in executing))


def focused_checks(checks: list[Check], paths: set[str]) -> list[Check]:
    """Execution checks with a declared verification target in the change."""
    return [
        check
        for check in checks
        if check.kind in {"command", "evidence", "tla", "lean", "drt"}
        and any(
            runner.touched_by(check, {path})
            and any(git.glob_match(pattern, path) for pattern in check.get("verifies", check.covers))
            for path in paths
        )
    ]


def change_plan(info: Inspection, checks: list[Check], paths: set[str], *, base: str | None = None) -> dict:
    """Map a proposed or actual change to checks and surface gaps in the declared scopes."""
    selected = [check for check in checks if runner.touched_by(check, paths)] if paths else []
    follow_up = []
    for candidates, label, kind in (
        (info.protocol_candidates, "Consider an interleaving check for state transitions", "tla"),
        (info.model_candidates, "Consider property tests for the affected pure logic", "property"),
    ):
        for candidate in candidates:
            if candidate.path in paths:
                follow_up.append(
                    {
                        "path": candidate.path,
                        "reason": candidate.reason,
                        "label": label,
                        "command": shlex.join(["rung", "draft", kind, candidate.path]),
                    }
                )
    ids = [check.id for check in selected]
    focused = [check.id for check in focused_checks(selected, paths)]
    args = ["rung", "check", *(["--base", base] if base else [])]
    return {
        "paths": sorted(paths),
        "checks": ids,
        "focused_checks": focused,
        "without_execution_check": uncovered_paths(selected, paths),
        "run": shlex.join([*args, *ids]) if ids else "",
        "focused_run": shlex.join([*args, *focused]) if focused and focused != ids else "",
        "follow_up": follow_up,
    }


def _scanner_rec(tool: str, check_id: str, why: str) -> Recommendation:
    preset = PRESETS[tool]
    installed = shutil.which(tool) is not None
    command = f"rung add scan {check_id} --tool {tool}"
    if not installed:
        why += f" Install it first: {preset.install}."
    return Recommendation("security", f"Scan for {preset.purpose}", why, effort="low", command=command)


def static_check_id(project_path: str, kind: str) -> str:
    """`typecheck` at the root, `frontend-typecheck` for a sub-project."""
    return kind if project_path == "." else safe_id(f"{project_path.replace('/', '-')}-{kind}")


def detected_checks(info: Inspection) -> list[tuple[str, Check]]:
    """Commands recorded by setup and shown by the planner, in the same order and scope."""
    found: list[tuple[str, Check]] = []
    used: set[str] = set()
    for project in info.projects:
        tests = [("tests", project.test_command), *project.additional_tests] if project.test_command else []
        for group, commands in (("tests", tests), ("architecture", project.static)):
            for kind, command in commands:
                name = static_check_id(project.path, kind)
                if name in used:
                    digest = hashlib.sha256(f"{project.path}/{kind}/{command}".encode()).hexdigest()[:12]
                    name = f"{name[:51]}-{digest}"
                if name in used:  # identical command from multiple detectors
                    continue
                used.add(name)
                options = {"run": command} | (
                    {} if project.path == "." else {"cwd": project.path, "verifies": [f"{project.path}/**"]}
                )
                # The cwd does not describe dependencies on shared code and workspace config.
                found.append((group, Check(name, "command", [], options)))
    return found


def plan(
    info: Inspection,
    existing: set[str] | None = None,
    *,
    paths: set[str] | None = None,
) -> list[Recommendation]:
    """Recommendations for a surveyed repository, skipping check ids that already exist."""
    existing = existing or set()
    recs: list[Recommendation] = []
    lang = info.primary_language

    for group, check in detected_checks(info):
        if check.id in existing:
            continue
        command = check.require("run")
        cwd = check.get("cwd")
        argv = ["rung", "add", "command", check.id, "--run", command]
        if cwd:
            argv += ["--cwd", cwd]
        if targets := check.get_list("verifies"):
            argv += ["--verifies", *targets]
        label = f" in {cwd}" if cwd else ""
        recs.append(
            Recommendation(
                group,
                f"Record {command!r}{label} as a check",
                "The repository already configures it. Keep coverage repo-wide until you know its shared inputs.",
                command=shlex.join(argv),
            )
        )

    suites = [p for p in info.projects if p.test_command]
    if paths is None:
        suites = suites[:1]
    else:
        nested_first = sorted(suites, key=lambda p: len(Path(p.path).parts), reverse=True)
        selected = set()
        for path in paths:
            if project := next((p for p in nested_first if Path(path).is_relative_to(p.path)), None):
                selected.add(project.path)
        suites = [p for p in suites if p.path in selected]
    for project in suites:
        argv = ["rung", "prove", project.test_command]
        if project.path != ".":
            argv += ["--cwd", project.path]
        recs.append(
            Recommendation(
                "tests",
                "Prove bug-fix tests catch the bug",
                "The branch's new tests must fail on the merge base and pass on the branch. For runners "
                "without per-test proof support, add --expect with the failure you want to reproduce.",
                command=shlex.join(argv),
            )
        )

    for project in info.projects:
        where = "" if project.path == "." else f" in {project.path}"
        for tool in project.unrecorded:
            recs.append(Recommendation(
                "architecture", f"Record a check for {tool.split(' ', 1)[0]}{where} by hand",
                f"Configured but not recorded automatically: {tool}.",
            ))  # fmt: skip
    if "shortcuts" not in existing:
        recs.append(Recommendation(
            "tests", "Block the shortcuts agents take to get to green",
            "New `@ts-ignore`, `# type: ignore`, `as any`, lint disables, skipped or focused tests, deleted tests, "
            "lowered coverage thresholds and relaxed type-checker settings block; existing ones do not. No tool "
            "to install.",
            command="rung add shortcuts shortcuts",
        ))  # fmt: skip

    if "secrets" not in existing:
        recs.append(_scanner_rec("gitleaks", "secrets", "Leaked keys are the most common agent-introduced incident."))
    if "vulnerable-deps" not in existing:
        recs.append(_scanner_rec("osv-scanner", "vulnerable-deps", "Known-vulnerable dependency versions."))
    if "deps" not in existing:
        recs.append(Recommendation(
            "security", "Vet every newly added dependency",
            "Agents sometimes add packages that do not exist; attackers register those names. New packages "
            "must exist on their registry and be at least 30 days old unless a person allows them.",
            command="rung add deps deps",
        ))  # fmt: skip
    if info.ci and "workflows" not in existing and any(p.startswith(".github/workflows/") for p in info.ci):
        recs.append(_scanner_rec("zizmor", "workflows", "CI workflows run with secrets and write access."))
    if lang in ("Python", "TypeScript", "JavaScript", "Go", "Java", "Ruby") and "code-patterns" not in existing:
        recs.append(_scanner_rec("semgrep", "code-patterns", "Injection, SSRF and unsafe deserialization patterns."))

    if lang != "unknown" and not info.lint:
        recs.append(Recommendation(
            "architecture", "Add a linter and import-boundary rules",
            f"No lint configuration found. Agents copy the patterns they see; for {lang}, a linter plus "
            "layer rules stops the shortcut before review. Record it with `rung add command lint --run ...`.",
        ))  # fmt: skip

    if info.surfaces and info.surfaces != ["library"]:
        recs.append(Recommendation(
            "runtime", f"Record runtime evidence for the {info.surfaces[0]}",
            "Write a script that launches the app, drives one feature end to end, and saves what it saw to "
            "$RUNG_EVIDENCE_DIR (a transcript, response bodies, a screenshot or a video). It must record "
            "something to pass.",
            effort="medium", command="rung add evidence runtime --run '<your drive script>'",
        ))  # fmt: skip

    if info.protocol_candidates:
        top = info.protocol_candidates[:3]
        recs.append(Recommendation(
            "protocol", "Model the stateful core in TLA+",
            "These files look like they hold state, retries or leases, where bugs need a specific "
            "interleaving that tests rarely reach: " + "; ".join(c.path for c in top) + ". Worth it only if "
            "a concurrency bug here would be expensive.",
            targets=[c.path for c in top], effort="high", command=shlex.join(["rung", "draft", "tla", top[0].path]),
        ))  # fmt: skip
    if info.model_candidates:
        top = info.model_candidates[:3]
        recs.append(Recommendation(
            "model", "Model a pure core in Lean and test the code against it",
            "Small, side-effect-free logic (pricing, permissions, parsers, reducers) is where a proven model "
            "pays off: " + "; ".join(c.path for c in top) + ".",
            targets=[c.path for c in top], effort="high", command=shlex.join(["rung", "draft", "lean", top[0].path]),
        ))  # fmt: skip

    recs.append(Recommendation(
        "ledger", "Gate pull requests on fresh verdicts",
        "In CI, `rung gate` runs automated checks again against the merge base under reviewed policy; "
        "`rung report` writes the pull request comment.",
        command="rung gate",
    ))  # fmt: skip
    return recs
