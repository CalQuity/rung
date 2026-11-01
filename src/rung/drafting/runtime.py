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
from rung.checks.base import Context
from rung.checks.command import EvidenceChecker
from rung.core import git
from rung.core.config import Check
from rung.core.errors import ToolMissing
from rung.drafting.common import FILES, STRINGS, files_of, safe_write, schema
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
library (e.g. `playwright`; empty if none), and `notes` with anything the developer should know."""


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
    """What the model sees: the survey, a file list, and the project's own docs and entry points.

    Files near the focus come first in the listing, so a sub-project (`frontend/`) is never hidden
    behind hundreds of unrelated files that sort earlier.
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
    prompt = (
        "Write the verification script and feature map for this project.\n"
        + (f"Drive this feature: {intent}\n" if intent else "")
        + (f"The evidence must include: {', '.join(require)} (e.g. `.webm` means a video).\n" if require else "")
        + "\n"
        + gather_context(root, info, focus)
    )
    files: list[Path] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=guides.load("runtime") + FORMAT, cwd=root)
        produced = files_of(reply)
        if "drive.py" not in produced or "FEATURES.md" not in produced:
            prompt += "\n\nYour reply must contain both `drive.py` and `FEATURES.md` in `files`."
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
        outcome = EvidenceChecker().run(Context(root=root), check)
        if outcome.verdict == "pass":
            return RuntimeDraft(True, outcome.summary, files, round_no, covers, list(outcome.evidence),
                                reply["notes"].strip(), run)  # fmt: skip
        prompt += (
            f"\n\nRung ran your script and it did not prove the feature ({outcome.summary}):\n"
            + "\n".join(outcome.lines[-30:])
            + "\nFix the script. Do not weaken what it checks. Return the same fields."
        )
    return RuntimeDraft(False, f"no passing verification script after {max_rounds} rounds", files, max_rounds)
