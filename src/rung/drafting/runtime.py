"""`rung draft runtime`: generate a repo-specific verification script, and make it prove itself.

The idea comes from Lauren Tan's pstack (`/create-verification-skill`): every
project needs a scripted way to drive the real app the way a user does and
capture evidence, plus a feature map saying how each feature is reached and what
observable state proves it works. pstack states the key rule in prose: a
generated skill that was never executed is a draft, not a deliverable.

Rung enforces that rule mechanically. The model writes the drive script and the
feature map; Rung runs the script as an `evidence` check and feeds failures back
for repair. Nothing is registered until a run has passed and left evidence. The
feature map's sources are covered by the check, so when the app changes the
verdict goes stale and the script must be re-proven.
"""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rung import guides
from rung.analysis.survey import Inspection
from rung.checks import deps
from rung.checks.base import Context, Outcome
from rung.checks.command import (
    CATEGORIES,
    EvidenceChecker,
    assertion_outcomes,
    failed_assertions,
    missing_required,
    not_applicable,
    read_result,
    scenarios,
)
from rung.core import git
from rung.core.config import Check
from rung.core.errors import ToolMissing
from rung.drafting import routes
from rung.drafting.common import FILES, STRINGS, files_of, machine_path_problem, safe_write, schema
from rung.drafting.providers import Provider

VERIFY_DIR = Path(".rung") / "verify"
CONTEXT_FILES = (
    "README.md", "README.rst", "package.json", "pyproject.toml", "Makefile", "justfile", "Procfile",
    "docker-compose.yml", "compose.yaml", "manage.py", "main.py", "app.py", "go.mod", "Cargo.toml",
)  # fmt: skip
MAX_CONTEXT_BYTES = 60_000
SCHEMA = schema(files=FILES, sources=STRINGS, requirements=STRINGS, notes={"type": "string"})

FORMAT = """

Return `files` with `drive.py` and `FEATURES.md`, `sources` with globs of the source files the driven
feature depends on, `requirements` with the Python packages the script imports beyond the standard
library (e.g. `playwright`; empty if none), and `notes` with anything the developer should know.
`drive.py` reads `RUNG_SEED`, `RUNG_FAULTS` and `RUNG_SCENARIOS` and writes `result.json` with one
scenario per category (or a reason in `not_applicable`), as the guide describes."""

# What each `--require` suffix asks the script to record, so the model knows what to produce.
SUFFIX_MEANING = {
    ".webm": "a video of the browser session", ".zip": "a Playwright trace (trace.zip)",
    ".har": "a HAR network log", ".png": "screenshots", ".gif": "an animated recording",
    ".json": "a JSON record", ".txt": "a transcript",
}  # fmt: skip
ENV_VARS = ("RUNG_SEED", "RUNG_FAULTS", "RUNG_SCENARIOS")


def contract_problems(drive_source: str, result: dict | None) -> list[str]:
    """How a drive script falls short of the contract, from its source and the `result.json` it wrote.

    The script must take its seed, faults and scenario selection from Rung, and drive more than the
    happy path: one scenario per category, unless it says why a category does not apply.
    """
    problems = [f"drive.py never reads {v}" for v in ENV_VARS if v not in drive_source]
    if result is None:
        return [*problems, "drive.py wrote no result.json (an object with `seed` and `scenarios`)"]
    if "seed" not in result:
        problems.append("result.json has no `seed`; record the RUNG_SEED the run used")
    found = scenarios(result) if isinstance(result.get("scenarios"), list) else []
    if not found:
        return [*problems, "result.json has no `scenarios`"]
    if bad := sorted({s["category"] for s in found} - set(CATEGORIES)):
        problems.append(f"unknown scenario categories {', '.join(map(repr, bad))}; use {', '.join(CATEGORIES)}")
    covered = {s["category"] for s in found}
    excused = not_applicable(result)
    if "happy" not in covered:
        problems.append("no `happy` scenario: drive the feature's main path first")
    if missing := [c for c in CATEGORIES[1:] if c not in covered and c not in excused]:
        problems.append(
            f"no scenario in categories {', '.join(missing)}; add one per category, or give the reason it does not "
            "apply to this feature in `not_applicable`"
        )
    if covered <= {"happy"}:
        problems.append("result.json covers only the happy path; edge cases and faults are the point")
    return problems


def _flaky(first: dict | None, second: dict | None) -> list[str]:
    """Assertions whose outcome differs between two runs with the same seed."""
    a, b = dict(((s, n), ok) for s, n, ok in assertion_outcomes(first)), dict(
        ((s, n), ok) for s, n, ok in assertion_outcomes(second))  # fmt: skip
    return sorted(f"{s}: {n}" if s else n for s, n in set(a) | set(b) if a.get((s, n)) != b.get((s, n)))


def happy_path_advice(result: dict | None) -> str:
    """When the happy path itself failed: what it saw, and how to tell a misread page from a real bug.

    A failing happy path sends the script back, because it shows the script cannot yet drive the feature.
    But the app may really be wrong on its main path, and a model told only "do not weaken the check"
    keeps the assertion and fails every round. A real bug belongs in its own scenario, where Rung
    reports it to a person as a finding.
    """
    if result is None:
        return ""
    failed = [
        f"{s.get('name', '')}: {a.get('name', '')} "
        f"(expected {a.get('expected', '?')!r}, observed {a.get('observed', '?')!r})"
        for s in result.get("scenarios") or []
        if isinstance(s, dict) and s.get("category") == "happy"
        for a in s.get("assertions") or []
        if isinstance(a, dict) and a.get("passed") is not True
    ]
    if not failed:
        return ""
    return (
        "\n\nThe happy path failed on: " + "; ".join(failed[:8]) + ".\nThe happy path must pass on the app as it "
        "is: it shows the script can drive the feature at all. For each of these, decide which it is:\n"
        "- The script misreads the page (for example it treats a discount line or a refund shown as a negative "
        "amount as an error, or picks the wrong element): fix how the script reads the page, keeping the check "
        "on what the user must see.\n"
        "- The app really is wrong here: keep the assertion unchanged, but move it into its own scenario "
        "(category `input` or `state`, named for the bug). Rung reports a failing edge-case scenario to a person "
        "as a finding, and never asks you to make it pass."
    )


def _findings(check: Check, outcome: Outcome, result: dict | None) -> list[str]:
    """Failures that belong to the app, not the script: the script ran, recorded its evidence, passed
    its happy path, and only edge-case or fault scenarios failed. Those go to a person, like a failing
    property; sending them back to the model would invite it to make the app's bug pass."""
    if outcome.verdict != "fail" or result is None or not outcome.evidence:
        return []
    if missing_required(check, outcome.evidence):
        return []
    found = scenarios(result)
    failing = [s for s in found if not s["passed"]]
    if (
        not failing
        or any(s["category"] == "happy" for s in failing)
        or not any(s["category"] == "happy" for s in found)
    ):
        return []
    return failed_assertions(failing)


MIN_PACKAGE_AGE_DAYS = 30


def vet_requirements(requirements: list[str]) -> list[str]:
    """Problems with packages a model wants to install: missing from PyPI, or too new to trust.

    The same rule the `deps` check applies to a project's own dependencies: a model can invent a
    package name, and an attacker can register it.
    """
    problems = []
    for spec in requirements:
        name = deps.requirement_name(spec)
        if not name:
            problems.append(f"{spec!r} is not a package requirement")
            continue
        published = deps.first_published(deps.Package("pypi", name))
        if published is None:
            problems.append(f"{name} does not exist on PyPI")
        elif (age := (datetime.now(UTC) - published).days) < MIN_PACKAGE_AGE_DAYS:
            problems.append(f"{name} was first published {age} day(s) ago")
    return problems


def run_command(requirements: list[str]) -> str:
    """How Rung runs the drive script: plain Python, or an isolated uv environment with its packages."""
    script = (VERIFY_DIR / "drive.py").as_posix()
    if not requirements:
        return f"python3 {script}"
    if not shutil.which("uv"):
        raise ToolMissing(f"the drive script needs {', '.join(requirements)}; install uv (https://docs.astral.sh/uv/)")
    withs = " ".join(f"--with {shlex.quote(r)}" for r in requirements)
    return f"uv run --no-project {withs} python {script}"


@dataclass
class RuntimeDraft:
    ok: bool
    message: str
    files: list[Path] = field(default_factory=list)
    rounds: int = 0
    covers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    notes: str = ""
    run: str = ""
    seed: int | None = None
    findings: list[str] = field(default_factory=list)  # scenarios that failed on the real app: for a person
    report: str = ""  # the evidence page of the accepted run
    scenarios: list[dict] = field(default_factory=list)


# Files that usually show how an app starts and what a user sees first, per project directory.
ENTRY_GLOBS = (
    "README.md", "package.json", "pyproject.toml", "app/page.*", "src/app/page.*", "app/layout.*",
    "src/app/layout.*", "pages/index.*", "src/pages/index.*", "src/main.*", "src/index.*", "main.py", "app.py",
    "manage.py", "cmd/*/main.go", "src/main.rs",
)  # fmt: skip
MAX_LISTED_FILES = 400
SURVEY_KEYS = ("languages", "surfaces", "test_commands", "ci", "projects")


def _context_files(root: Path, info: Inspection, focus: list[str], files: list[str]) -> list[str]:
    """Focus files first, then each project's docs, manifests and entry points, then root build files."""
    chosen: list[str] = list(focus)
    for project in info.projects:
        prefix = "" if project.path == "." else f"{project.path}/"
        for pattern in ENTRY_GLOBS:
            chosen += [f for f in files if git.glob_match(prefix + pattern, f)]
    chosen += list(CONTEXT_FILES)
    return list(dict.fromkeys(chosen))


def gather_context(root: Path, info: Inspection, focus: list[str]) -> str:
    """What the model sees: the survey, the routes the app serves, a file list, and the project's docs.

    Files near the focus come first in the listing, so a sub-project (`frontend/`) is never hidden
    behind hundreds of unrelated files that sort earlier. The routes come from the code itself, so the
    model drives URLs that exist instead of guessing them.
    """
    files = git.list_files(root)
    focus_dirs = {str(Path(f).parent) for f in focus} - {"."}
    near = [f for f in files if any(f.startswith(d + "/") for d in focus_dirs)]
    listing = list(dict.fromkeys(near + files))[:MAX_LISTED_FILES]
    parts = [
        "Survey:",
        json.dumps({k: info.to_dict()[k] for k in SURVEY_KEYS}, indent=1),
        "",
        f"Files ({len(listing)} of {len(files)}, those near the focus first):",
        "\n".join(listing),
    ]
    if served := routes.prompt_block(routes.scan(root, files)):
        parts += ["", served]
    budget = MAX_CONTEXT_BYTES
    for rel in _context_files(root, info, focus, files):
        path = root / rel
        if not path.is_file() or budget <= 0:
            continue
        text = path.read_text(errors="ignore")[: min(budget, 12_000)]
        budget -= len(text)
        parts += ["", f"--- {rel} ---", text]
    return "\n".join(parts)


def draft_runtime(
    provider: Provider, root: Path, info: Inspection, *, focus: list[str], check_id: str = "runtime",
    intent: str = "", max_rounds: int = 4, require: tuple[str, ...] = (),
) -> RuntimeDraft:  # fmt: skip
    out_dir = root / VERIFY_DIR
    wants = ", ".join(f"{r} ({SUFFIX_MEANING[r.lower()]})" if r.lower() in SUFFIX_MEANING else r for r in require)
    prompt = (
        "Write the verification script and feature map for this project.\n"
        + (f"Drive this feature: {intent}\n" if intent else "")
        + (f"The evidence must include: {wants}.\n" if require else "")
        + "\n"
        + gather_context(root, info, focus)
    )
    files: list[Path] = []
    checker = EvidenceChecker()
    last_failure = ""
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=guides.load("runtime") + FORMAT, cwd=root)
        produced = files_of(reply)
        if "drive.py" not in produced or "FEATURES.md" not in produced:
            prompt += "\n\nYour reply must contain both `drive.py` and `FEATURES.md` in `files`."
            continue
        if problem := machine_path_problem(produced):
            prompt += problem
            continue
        files = safe_write(out_dir, produced)
        covers = [g.strip() for g in reply["sources"] if g.strip()]
        requirements = [p.strip() for p in reply["requirements"] if p.strip()]
        if problems := vet_requirements(requirements):
            prompt += (
                "\n\nRung will not install these requirements: "
                + "; ".join(problems)
                + ". Use only established packages (or none). Return the same fields."
            )
            continue
        run = run_command(requirements)
        check = Check(check_id, "evidence", covers, {"run": run, "timeout": 900, "require": list(require)})
        outcome = checker.run(Context(root=root), check)
        first = outcome.details["runs"][0]
        result = read_result(root / first["evidence_dir"])
        findings = _findings(check, outcome, result)
        if outcome.verdict != "pass" and not findings:
            last_failure = outcome.summary
            prompt += (
                f"\n\nRung ran your script and it did not prove the feature ({outcome.summary}):\n"
                + "\n".join(outcome.lines[-30:])
                + happy_path_advice(result)
                + "\nFix the script. Do not weaken what it checks. Return the same fields."
            )
            continue
        if problems := contract_problems(produced["drive.py"], result):
            prompt += (
                "\n\nThe script ran, but it does not meet the contract:\n- "
                + "\n- ".join(problems)
                + "\nFix the script and FEATURES.md. Return the same fields."
            )
            continue
        # The same seed must give the same outcome, or a failure could never be reproduced.
        seed = first["seed"]
        again = checker.run(Context(root=root, seed=seed), check)
        if flaky := _flaky(result, read_result(root / again.details["runs"][0]["evidence_dir"])):
            prompt += (
                f"\n\nRung ran your script twice with RUNG_SEED={seed} and the runs disagreed on: "
                + "; ".join(flaky[:10])
                + ". Derive every random choice and timing from RUNG_SEED, wait for observable state instead of "
                "sleeping, and give each scenario fresh state. Return the same fields."
            )
            continue
        draft = RuntimeDraft(True, outcome.summary, files, round_no, covers, list(outcome.evidence),
                             reply["notes"].strip(), run, seed, findings, outcome.details.get("report", ""),
                             first.get("scenarios", []))  # fmt: skip
        if findings:
            draft.message = f"the script works and the app failed {len(findings)} assertion(s); see the findings"
        return draft
    why = f"; the last run: {last_failure}" if last_failure else ""
    return RuntimeDraft(False, f"no passing verification script after {max_rounds} rounds{why}", files, max_rounds)
