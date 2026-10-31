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

from rung import draft, gitutil
from rung.checkers.base import Context
from rung.checkers.command import EvidenceChecker
from rung.config import Check
from rung.providers import Provider
from rung.survey import Inspection

VERIFY_DIR = Path(".rung") / "verify"
CONTEXT_FILES = (
    "README.md", "README.rst", "package.json", "pyproject.toml", "Makefile", "justfile", "Procfile",
    "docker-compose.yml", "compose.yaml", "manage.py", "main.py", "app.py", "go.mod", "Cargo.toml",
)  # fmt: skip
MAX_CONTEXT_BYTES = 60_000

SYSTEM = """You write a verification script that drives a real application the way a user does
and records evidence. A program, not a person, will run it, with no one watching.

The script's contract:
- Language: Python 3 standard library only (subprocess, urllib, json, time, pathlib, socket).
  Optional: Playwright for web UIs, guarded by an import check that exits 2 with a clear message.
- Start what it needs (dev server, CLI build) using the project's own documented commands,
  wait until the app is actually usable (a port answers AND a real request succeeds), and
  stop only what it started. Pick a free port; never kill processes by name.
- Exercise ONE user-facing feature end to end through its real interface (HTTP requests,
  CLI invocations, browser actions), never internal functions or test-only endpoints.
- Write evidence into the directory in the RUNG_EVIDENCE_DIR environment variable:
  transcripts of commands with their output, HTTP request/response pairs as JSON,
  screenshots or a video for UIs. Evidence must survive cleanup.
- Check the observable result (status codes, output text, persisted state) and exit 1 with a
  message naming what was expected and what happened if it is wrong. Exit 0 only on success.
- Never print or save secrets; read credentials only from environment variables.

Also write a feature map: FEATURES.md listing the top 3-5 user-facing features, each with
"How a user reaches it", "How the script drives it", "What proves it works", and the source
files that implement it.

Output fenced blocks only:
```python file=drive.py
...
```
```markdown file=FEATURES.md
...
```
Then one line: SOURCES: <comma-separated globs of the source files the driven feature depends on>"""


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
    files = gitutil.list_files(root)
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
        reply = provider.complete(prompt, system=SYSTEM, cwd=root)
        produced = draft.parse_files(reply)
        if "drive.py" not in produced or "FEATURES.md" not in produced:
            prompt += "\n\nYour reply must contain both ```python file=drive.py``` and ```markdown file=FEATURES.md```."
            continue
        files = draft.safe_write(out_dir, produced)
        covers = []
        for line in reply.splitlines():
            if line.startswith("SOURCES:"):
                covers = [g.strip() for g in line.removeprefix("SOURCES:").split(",") if g.strip()]

        run = f"python3 {(VERIFY_DIR / 'drive.py').as_posix()}"
        check = Check(check_id, "evidence", covers, {"run": run, "timeout": 600})
        outcome = EvidenceChecker().run(Context(root=root), check)
        if outcome.verdict == "pass":
            return RuntimeDraft(True, outcome.summary, files, round_no, covers, list(outcome.evidence))
        prompt += (
            f"\n\nRung ran your script and it did not prove the feature ({outcome.summary}):\n"
            + "\n".join(outcome.lines[-30:])
            + "\nFix the script. Do not weaken what it checks. Reply in the same format."
        )
    return RuntimeDraft(False, f"no passing verification script after {max_rounds} rounds", files, max_rounds)
