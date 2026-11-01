"""Recommend checks for a repository, cheapest and most widely useful first.

The ladder is applied by escalation, not by climbing every rung everywhere.
Every repository benefits from the cheap rungs: tests recorded as evidence,
security scanners gated on new findings, vetted dependencies, fix proofs.
Formal methods are recommended only where the survey finds code they pay off
for: TLA+ for stateful protocols, a Lean model for small pure cores.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from rung.analysis.survey import Inspection
from rung.checks.presets import PRESETS

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


def _scanner_rec(tool: str, check_id: str, why: str) -> Recommendation:
    preset = PRESETS[tool]
    installed = shutil.which(tool) is not None
    command = f"rung add scan {check_id} --tool {tool}"
    if not installed:
        why += f" Install it first: {preset.install}."
    return Recommendation("security", f"Scan for {preset.purpose}", why, effort="low", command=command)


def static_check_id(project_path: str, kind: str) -> str:
    """`typecheck` at the root, `frontend-typecheck` for a sub-project."""
    return kind if project_path == "." else f"{project_path.replace('/', '-')}-{kind}"


def plan(info: Inspection, existing: set[str] | None = None) -> list[Recommendation]:
    """Recommendations for a surveyed repository, skipping check ids that already exist."""
    existing = existing or set()
    recs: list[Recommendation] = []
    lang = info.primary_language

    tested = [p for p in info.projects if p.test_command]
    for project in tested:
        name = "tests" if project.path == "." else f"{project.path.replace('/', '-')}-tests"
        if name in existing:
            continue
        label = "the" if project.path == "." else f"the {project.path}"
        cwd = "" if project.path == "." else f" --cwd {project.path} --covers '{project.path}/**'"
        recs.append(Recommendation(
            "tests", f"Record {label} test suite as a check",
            "A green run becomes a ledger entry tied to the exact code it ran against, and goes stale "
            "when that code changes.",
            command=f"rung add command {name}{cwd} --run '{project.test_command}'",
        ))  # fmt: skip
    if tested:
        first = tested[0]
        cwd = "" if first.path == "." else f" --cwd {first.path}"
        recs.append(Recommendation(
            "tests", "Prove bug-fix tests catch the bug",
            "Agents often write regression tests that pass with or without the fix. `rung prove` runs the "
            "branch's new tests on the merge base, where they must fail, and on the branch, where they must pass.",
            command=f"rung prove{cwd} '{first.test_command}'",
        ))  # fmt: skip

    for project in info.projects:
        for kind, command in project.static:
            name = static_check_id(project.path, kind)
            if name in existing:
                continue
            cwd = "" if project.path == "." else f" --cwd {project.path} --covers '{project.path}/**'"
            recs.append(Recommendation(
                "architecture", f"Record {command!r} as a check",
                "The project already configures it; recorded as a check, an agent cannot finish with it failing, "
                "and the `shortcuts` check stops it being silenced.",
                command=f"rung add command {name}{cwd} --run '{command}'",
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

    if not info.lint:
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
            targets=[c.path for c in top], effort="high", command=f"rung draft tla {top[0].path}",
        ))  # fmt: skip
    if info.model_candidates:
        top = info.model_candidates[:3]
        recs.append(Recommendation(
            "model", "Model a pure core in Lean and test the code against it",
            "Small, side-effect-free logic (pricing, permissions, parsers, reducers) is where a proven model "
            "pays off: " + "; ".join(c.path for c in top) + ".",
            targets=[c.path for c in top], effort="high", command=f"rung draft lean {top[0].path}",
        ))  # fmt: skip

    recs.append(Recommendation(
        "ledger", "Gate pull requests on fresh verdicts",
        "In CI, `rung check --changed` runs what a branch touches and blocks only what it introduces; "
        "`rung report` writes the pull request comment.",
        command="rung check --changed",
    ))  # fmt: skip
    return recs
