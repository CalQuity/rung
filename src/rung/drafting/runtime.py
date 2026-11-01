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
from dataclasses import dataclass, field
from pathlib import Path

from rung import guides
from rung.analysis.survey import Inspection
from rung.checks.base import Context
from rung.checks.command import EvidenceChecker
from rung.core import git
from rung.core.config import Check
from rung.drafting.common import FILES, STRINGS, files_of, safe_write, schema
from rung.drafting.providers import Provider

VERIFY_DIR = Path(".rung") / "verify"
CONTEXT_FILES = (
    "README.md", "README.rst", "package.json", "pyproject.toml", "Makefile", "justfile", "Procfile",
    "docker-compose.yml", "compose.yaml", "manage.py", "main.py", "app.py", "go.mod", "Cargo.toml",
)  # fmt: skip
MAX_CONTEXT_BYTES = 60_000
SCHEMA = schema(files=FILES, sources=STRINGS, notes={"type": "string"})

FORMAT = """

Return `files` with `drive.py` and `FEATURES.md`, `sources` with globs of the source files the driven
feature depends on, and `notes` with anything the developer should know."""


@dataclass
class RuntimeDraft:
    ok: bool
    message: str
    files: list[Path] = field(default_factory=list)
    rounds: int = 0
    covers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def gather_context(root: Path, info: Inspection, focus: list[str]) -> str:
    """What the model sees: the survey, a file list, the project's own docs and entry points."""
    files = git.list_files(root)
    parts = [
        "Survey:",
        json.dumps({k: info.to_dict()[k] for k in ("languages", "surfaces", "test_commands", "ci")}, indent=1),
        "",
        "Files (first 300):",
        "\n".join(files[:300]),
    ]
    budget = MAX_CONTEXT_BYTES
    for rel in [*focus, *CONTEXT_FILES]:
        path = root / rel
        if not path.is_file() or budget <= 0:
            continue
        text = path.read_text(errors="ignore")[: min(budget, 15_000)]
        budget -= len(text)
        parts += ["", f"--- {rel} ---", text]
    return "\n".join(parts)


def draft_runtime(
    provider: Provider, root: Path, info: Inspection, *, focus: list[str], check_id: str = "runtime",
    intent: str = "", max_rounds: int = 4,
) -> RuntimeDraft:  # fmt: skip
    out_dir = root / VERIFY_DIR
    prompt = (
        "Write the verification script and feature map for this project.\n"
        + (f"Drive this feature: {intent}\n" if intent else "")
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

        run = f"python3 {(VERIFY_DIR / 'drive.py').as_posix()}"
        check = Check(check_id, "evidence", covers, {"run": run, "timeout": 600})
        outcome = EvidenceChecker().run(Context(root=root), check)
        if outcome.verdict == "pass":
            return RuntimeDraft(True, outcome.summary, files, round_no, covers, list(outcome.evidence))
        prompt += (
            f"\n\nRung ran your script and it did not prove the feature ({outcome.summary}):\n"
            + "\n".join(outcome.lines[-30:])
            + "\nFix the script. Do not weaken what it checks. Return the same fields."
        )
    return RuntimeDraft(False, f"no passing verification script after {max_rounds} rounds", files, max_rounds)
