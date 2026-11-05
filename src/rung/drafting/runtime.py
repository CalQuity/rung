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

import ast
import json
import math
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rung import guides
from rung.analysis.survey import LANGUAGES, Inspection
from rung.checks import deps
from rung.checks.base import Context, Outcome
from rung.checks.command import (
    CATEGORIES,
    EvidenceChecker,
    assertion_outcomes,
    failed_assertions,
    missing_artifacts,
    not_applicable,
    read_result,
    scenarios,
    structured_problem,
)
from rung.core import git, ledger, sandbox
from rung.core.config import Check, managed_path
from rung.core.errors import ToolMissing
from rung.drafting import feature_map, routes
from rung.drafting.common import FILES, STRINGS, files_of, machine_path_problem, safe_write, schema
from rung.drafting.providers import Provider
from rung.engines import mutate

VERIFY_DIR = Path(".rung") / "verify"
CONTEXT_FILES = (
    "README.md", "README.rst", "package.json", "pyproject.toml", "Makefile", "justfile", "Procfile",
    "docker-compose.yml", "compose.yaml", "manage.py", "main.py", "app.py", "go.mod", "Cargo.toml",
)  # fmt: skip
MAX_CONTEXT_BYTES = 120_000
SCHEMA = schema(files=FILES, sources=STRINGS, requirements=STRINGS, notes={"type": "string"})

FORMAT = """

Return `files` with `drive.py` and `FEATURES.md`, `sources` with globs of the source files the driven
feature depends on, `requirements` with the Python packages the script imports beyond the standard
library (e.g. `playwright`; empty if none), and `notes` with anything the developer should know.
`drive.py` reads `RUNG_SEED`, `RUNG_FAULTS` and `RUNG_SCENARIOS` and writes `result.json` with one
scenario per category (or a reason in `not_applicable`), as the guide describes."""

# With an approved feature map, the map is the feature document: the model returns the script only.
FORMAT_MAPPED = FORMAT.replace(
    "Return `files` with `drive.py` and `FEATURES.md`,", "Return `files` with `drive.py` only,"
)

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
    if problem := structured_problem(result):
        problems.append(problem)
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


def _findings(root: Path, check: Check, outcome: Outcome, result: dict | None) -> list[str]:
    """Failures that belong to the app, not the script: the script ran, recorded its evidence, passed
    its happy path, and only edge-case or fault scenarios failed. Those go to a person, like a failing
    property; sending them back to the model would invite it to make the app's bug pass."""
    if outcome.verdict != "fail" or result is None or not outcome.evidence:
        return []
    if missing_artifacts(check, (root / name for name in outcome.evidence)):
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


def run_command(requirements: list[str], script: str = ".rung/verify/drive.py") -> str:
    """How Rung runs the drive script: plain Python, or an isolated uv environment with its packages."""
    script = shlex.quote(script)
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
    sensitivity: str = ""


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


def app_focus(root: Path, info: Inspection, files: list[str] | None = None) -> list[str]:
    """The files a user-facing feature lives in, when none were named: those that serve requests and those
    that declare routes or pages, then the project's own modules they import (two levels: a page and the
    components it renders), so the model reads the real labels, fields and handlers instead of the README."""
    listed = [f for f in (files if files is not None else git.list_files(root)) if not (root / f).is_symlink()]
    route_files = [r.file for r in routes.scan(root, listed)]
    chosen = list(dict.fromkeys([*info.app_files, *route_files]))
    known = set(listed)
    frontier = chosen
    for _ in range(2):
        frontier = [f for f in _local_imports(root, frontier, known) if f not in chosen]
        chosen += list(dict.fromkeys(frontier))
    return chosen[:MAX_FOCUS_FILES]


MAX_FOCUS_FILES = 32
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+\(?([\w ,]+)|import\s+([\w.]+))", re.M)
_JS_IMPORT = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*['"]([^'"]+)['"]""")
_JS_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")


def _ancestors(rel: str) -> list[str]:
    """`a/b/c.py` -> ["a/b/", "a/", ""]: the directories a module path may be relative to."""
    parts = rel.split("/")[:-1]
    return [("/".join(parts[:n]) + "/") if n else "" for n in range(len(parts), -1, -1)]


def _local_imports(root: Path, sources: list[str], listed: set[str]) -> list[str]:
    """The repository's own modules that `sources` import: Python (`from app.pricing import ...`, resolved
    against each enclosing directory, so `backend/app/pricing.py` is found), and TypeScript or JavaScript
    (relative paths and the `@/` alias, resolved against the nearest directory with a tsconfig or package.json)."""
    found: list[str] = []
    for rel in sources:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        candidates: list[str] = []
        if rel.endswith(".py"):
            for m in _PY_IMPORT.finditer(text):
                module = (m.group(1) or m.group(3)).replace(".", "/")
                # `from app import pricing` names a module too: try `app/pricing.py` for each imported name.
                names = [n.strip() for n in (m.group(2) or "").split(",") if n.strip().isidentifier()]
                for target in [*(f"{module}/{n}" for n in names), module]:
                    candidates += [f"{b}{target}{ext}" for b in _ancestors(rel) for ext in (".py", "/__init__.py")]
        elif rel.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
            here = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
            for m in _JS_IMPORT.finditer(text):
                spec = m.group(1)
                if spec.startswith("."):
                    base = os.path.normpath(here + spec).replace(os.sep, "/")
                    candidates += [base + ext for ext in _JS_SUFFIXES]
                elif spec.startswith("@/"):
                    for anc in _ancestors(rel):
                        if f"{anc}tsconfig.json" in listed or f"{anc}package.json" in listed:
                            for prefix in (anc, f"{anc}src/"):
                                candidates += [prefix + spec[2:] + ext for ext in _JS_SUFFIXES]
                            break
        for c in candidates:
            # An empty `__init__.py` or a design-system primitive (`components/ui/button.tsx`) says nothing
            # about the app's features; leave the room to code that does.
            if c in listed and c not in sources and c not in found and "/ui/" not in f"/{c}" \
                    and (root / c).stat().st_size > 40:  # fmt: skip
                found.append(c)
    return found


def gather_context(root: Path, info: Inspection, focus: list[str]) -> str:
    """What the model sees: the survey, the routes the app serves, a file list, and the project's docs.

    Files near the focus come first in the listing, so a sub-project (`frontend/`) is never hidden
    behind hundreds of unrelated files that sort earlier. The routes come from the code itself, so the
    model drives URLs that exist instead of guessing them.
    """
    files = [name for name in git.list_files(root) if not (root / name).is_symlink()]
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
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or budget <= 0:
            continue
        text = path.read_text(errors="ignore")[: min(budget, 12_000)]
        budget -= len(text)
        parts += ["", f"--- {rel} ---", text]
    return "\n".join(parts)


def _trial_context(root: Path, *, seed: int | None, guarded: bool, network: bool) -> Context:
    prefix = sandbox.write_guard(root, network=network) if guarded else ()
    return Context(root=root, seed=seed, command_prefix=prefix, environment=sandbox.trial_environment(root))


# A function named like startup code (an app factory, a config loader, an entry point) is never
# mutated: breaking it stops the app from starting, which any startup-only drive notices.
BOOTSTRAP_NAME = re.compile(
    r"^(?:main|run|serve|app|application|wsgi|asgi|create_\w+|make_app|get_app|build_app|lifespan)$"
    r"|(?:^|_)(?:config|configure|settings|setup|bootstrap|factory|init|load_env|startup)(?:_|$)"
    r"|^__\w+__$",
    re.I,
)
# How many single-function or single-script variants one sensitivity check may run.
MAX_BEHAVIOR_VARIANTS = 8
# The control runs get a multiple of the accepted run's duration, never less than the floor.
CONTROL_TIMEOUT_FACTOR = 3
CONTROL_TIMEOUT_FLOOR = 60


@dataclass
class Variant:
    """One deliberate change to one application file: a function's return values, or one script."""

    source: Path
    content: str
    label: str
    hinted: bool = False  # the drive script names it, so it is tried first


@dataclass
class Sensitivity:
    """Whether the drive noticed a deliberate change to the app, and which control showed it."""

    detected: bool
    control: str
    problem: str = ""  # why a control could not decide (a timeout), instead of "did not detect"


def _own_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """The nodes of `function`'s own body, not those of the functions, lambdas or classes nested in it."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        found.append(node)
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _called_names(nodes: list[ast.AST]) -> set[str]:
    """Names of what `nodes` call (`f()`, `obj.f()`) or apply as decorators."""
    names: set[str] = set()
    for node in nodes:
        targets = [node.func] if isinstance(node, ast.Call) else []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            targets += [d.func if isinstance(d, ast.Call) else d for d in node.decorator_list]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _main_guard(node: ast.stmt) -> bool:
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _import_time_names(tree: ast.Module) -> set[str]:
    """Functions that run while the module loads, and the functions they call, transitively.

    Module and class bodies run at import (the `if __name__ == "__main__":` block does not), and
    so do decorators. Startup-named functions count too: an app factory runs when the app starts.
    """
    loading: list[ast.AST] = []
    pending: list[ast.AST] = [stmt for stmt in tree.body if not _main_guard(stmt)]
    while pending:
        node = pending.pop()
        loading.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue  # its body runs when called, not when defined; its decorators are in `loading`
        pending.extend(ast.iter_child_nodes(node))
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    names = _called_names(loading) | {f.name for f in functions if BOOTSTRAP_NAME.search(f.name)}
    while True:
        reached = set().union(*(_called_names(_own_nodes(f)) for f in functions if f.name in names)) - names
        if not reached:
            return names
        names |= reached


def _python_variants(source: Path, hints: str) -> list[Variant]:
    """One variant per application function: that function alone returns None.

    Startup code is left alone (see `_import_time_names`), so a variant changes what the app
    does, not whether it starts.
    """
    try:
        text = source.read_bytes()
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        return []
    startup = _import_time_names(tree)
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    variants = []
    for index, function in enumerate(functions):
        if function.name in startup:
            continue
        returns = [
            node
            for node in _own_nodes(function)
            if isinstance(node, ast.Return)
            and node.value is not None
            and not (isinstance(node.value, ast.Constant) and node.value.value is None)
        ]
        if not returns:
            continue
        copy = ast.parse(text)
        target = [n for n in ast.walk(copy) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)][index]
        for node in _own_nodes(target):
            if isinstance(node, ast.Return) and node.value is not None:
                node.value = ast.Constant(None)
        hinted = re.search(rf"\b{re.escape(function.name)}\b", hints) is not None
        label = f"{function.name}() in {source.name} returns None"
        variants.append(Variant(source, ast.unparse(copy) + "\n", label, hinted))
    return variants


_SCRIPT = re.compile(r"(<script\b[^>]*>)(.*?)(</script\s*>)", re.I | re.S)


def _html_variants(source: Path) -> list[Variant]:
    """One variant per inline script: that script alone is emptied."""
    content = source.read_text(errors="replace")
    variants = []
    for number, match in enumerate(_SCRIPT.finditer(content), 1):
        if not match.group(2).strip():
            continue
        changed = content[: match.start(2)] + content[match.end(2) :]
        variants.append(Variant(source, changed, f"inline script {number} in {source.name} removed"))
    return variants


def _js_variants(source: Path) -> list[Variant]:
    """One-token mutants of a script, each on its own."""
    variants = []
    for candidate in mutate.mutants(source, limit=3):
        lines = source.read_text().splitlines(keepends=True)
        line = lines[candidate.line - 1]
        lines[candidate.line - 1] = candidate.mutated + line[len(line.rstrip("\r\n")) :]
        variants.append(Variant(source, "".join(lines), f"mutant {candidate.describe}"))
    return variants


def _behavior_variants(sources: list[Path], hints: str = "") -> list[Variant]:
    """Changes to observable behavior that leave the application able to start, one change each.

    Functions the drive script names come first, then the rest in file order, up to
    `MAX_BEHAVIOR_VARIANTS`.
    """
    found: list[Variant] = []
    for source in sources:
        if source.suffix == ".py":
            found += _python_variants(source, hints)
        elif source.suffix == ".html":
            found += _html_variants(source)
        elif source.suffix in mutate.JS:
            found += _js_variants(source)
    return sorted(found, key=lambda v: not v.hinted)[:MAX_BEHAVIOR_VARIANTS]


def control_timeout(check: Check, baseline_seconds: float) -> int:
    """How long a control run may take: a multiple of the accepted run, at least the floor, at most the check's."""
    limit = check.get_int("timeout", 1800)
    return max(1, min(limit, max(CONTROL_TIMEOUT_FLOOR, math.ceil(baseline_seconds * CONTROL_TIMEOUT_FACTOR))))


def _timed_out(outcome: Outcome) -> bool:
    return any(
        r.get("verdict") == "error" and "timed out after" in r.get("summary", "")
        for r in outcome.details.get("runs", [])
    )


def _swap(context: Context, source: Path, content: str) -> Path:
    """Put `content` in place of `source`, keeping the original in the trial's stash; return the stash path."""
    stash = context.root / ".rung" / "cache" / "trial" / "source-canary" / source.relative_to(context.root)
    stash.parent.mkdir(parents=True, exist_ok=True)
    source.rename(stash)
    source.write_text(content)
    source.chmod(stash.stat().st_mode & 0o777)
    return stash


STARTUP_FAILURE = {
    ".py": "raise RuntimeError('RUNG_SOURCE_CANARY')\n",
    ".sh": "#!/bin/sh\nexit 97\n",
    ".js": "throw new Error('RUNG_SOURCE_CANARY');\n",
    ".ts": "throw new Error('RUNG_SOURCE_CANARY');\n",
}


def _source_sensitivity(
    checker: EvidenceChecker,
    check: Check,
    context: Context,
    paths: list[str],
    baseline: dict | None,
    *,
    hints: str = "",
    timeout: int = CONTROL_TIMEOUT_FLOOR,
) -> Sensitivity:
    """Change the app one function (or script) at a time, and see whether the drive notices.

    A variant counts only when the app still starts under it, shown by an assertion that passed on
    the real app still passing, and an assertion that passed on the real app now fails. Without
    any variant to make, every source is broken outright: a startup control.
    """
    files = [context.root / name for name in paths]
    files = [source for source in files if source.is_file() and not source.is_symlink()]
    if not files:
        return Sensitivity(False, "startup control (no regular application source file could be changed)")
    negative = Check(check.id, check.kind, check.covers, {**check.options, "timeout": timeout})
    variants = _behavior_variants(files, hints)
    if not variants:
        moved = []
        try:
            for source in files:
                failure = STARTUP_FAILURE.get(source.suffix, "RUNG_SOURCE_CANARY: intentionally unusable input\n")
                moved.append((_swap(context, source, failure), source))
            outcome = checker.run(context, negative)
        finally:
            for stash, source in reversed(moved):
                stash.replace(source)
        control = f"startup control with {len(moved)} deliberately broken source file(s)"
        if _timed_out(outcome):
            return Sensitivity(False, control, f"the {control} timed out after {timeout}s")
        return Sensitivity(outcome.verdict == "fail", control)
    passed = {(scenario, name) for scenario, name, ok in assertion_outcomes(baseline) if ok}
    timed_out: list[str] = []
    for variant in variants:
        stash = _swap(context, variant.source, variant.content)
        try:
            outcome = checker.run(context, negative)
        finally:
            stash.replace(variant.source)
        if _timed_out(outcome):
            timed_out.append(variant.label)
            continue
        observed = assertion_outcomes(read_result(context.root / outcome.details["runs"][0]["evidence_dir"]))
        caught = any((scenario, name) in passed and not ok for scenario, name, ok in observed)
        started = any((scenario, name) in passed and ok for scenario, name, ok in observed)
        if outcome.verdict == "fail" and caught and started:
            return Sensitivity(True, f"behavior mutation ({variant.label})")
    control = f"behavior mutation ({len(variants)} single-function or single-script change(s) tried)"
    if timed_out and len(timed_out) == len(variants):
        return Sensitivity(False, control, f"every behavior mutation run timed out after {timeout}s")
    return Sensitivity(False, control)


def draft_runtime(
    provider: Provider, root: Path, info: Inspection, *, focus: list[str], check_id: str = "runtime",
    intent: str = "", max_rounds: int = 4, require: tuple[str, ...] = (), guarded: bool = False,
    require_source: bool = False,
) -> RuntimeDraft:  # fmt: skip
    if guarded:
        sandbox.probe_write_guard(root)
    with sandbox.copy_of(root) as trial:
        return _draft_in_copy(
            provider,
            root,
            trial.resolve(),
            info,
            focus=focus,
            check_id=check_id,
            intent=intent,
            max_rounds=max_rounds,
            require=require,
            guarded=guarded,
            require_source=require_source,
        )


def _draft_in_copy(
    provider: Provider, root: Path, trial: Path, info: Inspection, *, focus: list[str], check_id: str,
    intent: str, max_rounds: int, require: tuple[str, ...], guarded: bool, require_source: bool,
) -> RuntimeDraft:  # fmt: skip
    verify_dir = VERIFY_DIR if check_id == "runtime" else VERIFY_DIR / "drives" / check_id
    out_dir = managed_path(root, verify_dir)
    script = (verify_dir / "drive.py").as_posix()
    wants = ", ".join(f"{r} ({SUFFIX_MEANING[r.lower()]})" if r.lower() in SUFFIX_MEANING else r for r in require)
    # With an approved feature map, the map is the plan: the script drives exactly its approved features.
    mapped, map_ids, map_sources = feature_map.approved_block(root)
    needed = ("drive.py",) if mapped else ("drive.py", "FEATURES.md")
    prompt = (
        ("Write the verification script for the approved features of this project.\n" if mapped
         else "Write the verification script and feature map for this project.\n")
        + "Return each complete file in the JSON reply. Rung writes those files after receiving the reply.\n"
        + f"The script path is {script}; Rung launches it with the project root as the working directory.\n"
        + f"Use these exact names for `files[].path`: {', '.join(needed)}.\n"
        + (f"Drive this feature: {intent}\n" if intent else "")
        + (f"The evidence must include: {wants}.\n" if require else "")
        + "\n"
        + gather_context(root, info, focus or app_focus(root, info))
        + (f"\n\n{mapped}\n\nReturn only `drive.py` in `files`; the map above is the feature map." if mapped else "")
    )  # fmt: skip
    files: list[Path] = []
    checker = EvidenceChecker()
    last_failure = ""
    produced: dict[str, str] = {}
    round_no = 0
    for round_no in range(1, max_rounds + 1):
        system = guides.load("runtime") + (FORMAT_MAPPED if mapped else FORMAT)
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        returned = files_of(reply)
        produced = {}
        for name in needed:
            body = returned.get(name, "")
            if not body.strip():
                body = returned.get((verify_dir / name).as_posix(), "")
            if body.strip():
                produced[name] = body
        if missing_files := [name for name in needed if name not in produced]:
            last_failure = f"model response did not include nonempty files named {', '.join(missing_files)}"
            prompt += f"\n\nThe {last_failure}. Return their contents in `files`, using only the filenames as paths."
            continue
        if problem := machine_path_problem(produced):
            last_failure = "the script hard-coded paths specific to the drafting machine"
            prompt += problem
            continue
        safe_write(managed_path(trial, verify_dir), produced)
        covers = [g.strip() for g in reply["sources"] if g.strip()]
        if mapped:  # the approved map and its features' code stale the verdict too
            covers = list(dict.fromkeys([*covers, *map_sources, f"{feature_map.MAP_DIR.as_posix()}/*.md"]))
        requirements = [p.strip() for p in reply["requirements"] if p.strip()]
        if problems := vet_requirements(requirements):
            last_failure = "the model requested dependencies that Rung could not approve"
            prompt += (
                "\n\nRung will not install these requirements: "
                + "; ".join(problems)
                + ". Use only established packages (or none). Return the same fields."
            )
            continue
        run = run_command(requirements, script)
        check = Check(check_id, "evidence", covers, {"run": run, "timeout": 900, "require": list(require)})
        context = _trial_context(trial, seed=None, guarded=guarded, network=bool(requirements))
        drive_inputs = [*covers, script]
        before = ledger.snapshot(trial, drive_inputs)
        started = time.monotonic()
        outcome = checker.run(context, check)
        baseline_seconds = time.monotonic() - started
        if ledger.snapshot(trial, drive_inputs) != before:
            last_failure = "the drive modified a covered source or its own script"
            break
        first = outcome.details["runs"][0]
        result = read_result(trial / first["evidence_dir"])
        findings = _findings(trial, check, outcome, result)
        if outcome.verdict != "pass" and not findings:
            last_failure = outcome.summary
            prompt += (
                f"\n\nRung ran your script and it did not prove the feature ({outcome.summary}):\n"
                + "\n".join(outcome.lines[-30:])
                + happy_path_advice(result)
                + "\nFix the script. Do not weaken what it checks. Return the same fields."
            )
            continue
        problems = contract_problems(produced["drive.py"], result)
        if missing := feature_map.uncovered(map_ids, result):
            problems.append(
                "these approved sub-features have no scenario: " + ", ".join(missing) + ". Name a scenario after "
                "each ID (or list it under the scenario's `covers`), or report it in `result.json` under "
                "`unreachable` with the precondition the run lacked"
            )
        if problems:
            last_failure = problems[0]
            prompt += (
                "\n\nThe script ran, but it does not meet the contract:\n- "
                + "\n- ".join(problems)
                + (
                    "\nFix the script. Return the same fields."
                    if mapped
                    else "\nFix the script and FEATURES.md. Return the same fields."
                )
            )
            continue
        # The same seed must give the same outcome, or a failure could never be reproduced.
        seed = first["seed"]
        context.seed = seed
        again = checker.run(context, check)
        flaky = _flaky(result, read_result(trial / again.details["runs"][0]["evidence_dir"]))
        if ledger.snapshot(trial, drive_inputs) != before:
            last_failure = "the replay modified a covered source or its own script"
            break
        if again.verdict != outcome.verdict:
            flaky.append("overall verdict or covered source state")
        if flaky:
            last_failure = "repeating the same seed produced different results"
            prompt += (
                f"\n\nRung ran your script twice with RUNG_SEED={seed} and the runs disagreed on: "
                + "; ".join(flaky[:10])
                + ". Derive every random choice and timing from RUNG_SEED, wait for observable state instead of "
                "sleeping, and give each scenario fresh state. Return the same fields."
            )
            continue
        candidates = set(focus or app_focus(root, info)) | set(git.expand(root, covers))
        app_sources = sorted(
            file
            for file in candidates
            if not file.startswith(".rung/")
            and Path(file).suffix in {*LANGUAGES, ".html", ".jsx", ".sh"}
            and (trial / file).is_file()
        )
        sensitivity = "no application inputs identified"
        if app_sources:
            found = _source_sensitivity(
                checker, check, context, app_sources, result,
                hints=produced["drive.py"], timeout=control_timeout(check, baseline_seconds),
            )  # fmt: skip
            if found.problem:
                last_failure = found.problem
                prompt += (
                    f"\n\nRung could not check that the drive notices a change to the app: {found.problem}. "
                    "Bound every wait in the drive and record a failed assertion instead of hanging."
                )
                continue
            if not found.detected:
                last_failure = f"the drive did not detect the {found.control}"
                prompt += (
                    f"\n\nThe drive did not detect the {found.control}. Rung changes one application function "
                    "or script at a time, leaving startup code alone, and expects an assertion that passed on the "
                    "real app to fail while another one, showing the app still runs, keeps passing. Exercise the "
                    "real app boundary and record a named assertion with the expected and observed behavior, "
                    "including when the observed result is wrong."
                )
                continue
            if (guarded or require_source) and found.control.startswith("startup control"):
                last_failure = (
                    "the drive passed a startup control but needs a behavioral assertion control before registration"
                )
                break
            sensitivity = f"detected {found.control}"
        elif guarded or require_source:
            last_failure = sensitivity
            prompt += "\n\nName the actual application files this drive depends on in the sources list."
            continue
        if report := outcome.details.get("report"):
            sandbox.retain_evidence(root, trial, report)
        files = safe_write(out_dir, produced)
        draft = RuntimeDraft(True, outcome.summary, files, round_no, covers, list(outcome.evidence),
                             reply["notes"].strip(), run, seed, findings, outcome.details.get("report", ""),
                             first.get("scenarios", []), sensitivity)  # fmt: skip
        if findings:
            draft.message = f"the script works and the app failed {len(findings)} assertion(s); see the findings"
        return draft
    if produced:
        files = safe_write(managed_path(root, Path(".rung/cache/drafts") / check_id), produced)
    why = f"; the last run: {last_failure}" if last_failure else ""
    return RuntimeDraft(False, f"no passing verification script after {round_no} round(s){why}", files, round_no)
