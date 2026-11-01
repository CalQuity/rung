"""`rung prove`: show that a new test actually catches the bug it claims to.

A regression test that passes before the fix proves nothing; agents write such
tests all the time. The proof is mechanical: take the test files changed on this
branch, put them on top of the merge base (the code *without* the fix), and run
the test command there. It must fail. Then run it on the branch. It must pass.
Both runs are recorded in the ledger.

When the base fails for the wrong reason, typically an import error because the
fix added a new function, `--expect` names text the base failure must contain,
such as the assertion or the test name.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rung.core import git, ledger
from rung.core.errors import RungError

TEST_PATTERNS = [
    "**/test_*.py", "**/*_test.py", "**/tests/**", "**/__tests__/**", "**/*.test.*",
    "**/*.spec.*", "**/*_test.go", "**/spec/**", "**/*Test.java", "**/*Tests.cs",
]  # fmt: skip


@dataclass
class Run:
    returncode: int
    tail: list[str]


@dataclass
class Proof:
    verdict: str  # pass | fail | error
    summary: str
    tests: list[str] = field(default_factory=list)
    base: Run | None = None
    head: Run | None = None


def is_test_file(path: str) -> bool:
    return any(git.glob_match(p, path) for p in TEST_PATTERNS)


def _run(command: str, cwd: Path, timeout: int) -> Run:
    proc = subprocess.run(shlex.split(command), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return Run(proc.returncode, (proc.stdout + proc.stderr).strip().splitlines()[-25:])


def prove(
    root: Path, command: str, base_ref: str, *, tests: list[str] | None = None,
    expect: str | None = None, timeout: int = 1800, cwd: str = ".",
) -> Proof:  # fmt: skip
    mb = git.merge_base(root, base_ref)
    changed = git.changed_files(root, base_ref)
    chosen = sorted(tests) if tests else sorted(f for f in changed if is_test_file(f))
    if not chosen:
        return Proof("error", "no changed test files on this branch; name them with --tests")
    missing = [t for t in chosen if not (root / t).is_file()]
    if missing:
        raise RungError(f"test file(s) not found: {', '.join(missing)}")

    with git.worktree(root, mb) as tree:
        for rel in chosen:
            dest = tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)
        base_run = _run(command, tree / cwd, timeout)
    head_run = _run(command, root / cwd, timeout)

    proof = Proof("pass", "", chosen, base_run, head_run)
    base_text = "\n".join(base_run.tail)
    if base_run.returncode == 0:
        proof.verdict, proof.summary = "fail", "the tests pass without the fix, so they do not catch the bug"
    elif expect and expect not in base_text:
        proof.verdict = "fail"
        proof.summary = f"the base run failed, but not with {expect!r}; it may be failing for another reason"
    elif head_run.returncode != 0:
        proof.verdict, proof.summary = "fail", "the tests fail on this branch too"
    else:
        proof.summary = f"fails on merge base {mb[:10]}, passes on this branch"

    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:40] or "tests"
    sources = sorted(f for f in changed if not is_test_file(f) and (root / f).is_file())
    ledger.append(
        root,
        check_id=f"proof:{slug}",
        kind="proof",
        verdict=proof.verdict,
        covers=ledger.snapshot(root, sources + chosen),
        specs={},
        summary=proof.summary,
        details={
            "command": command, "cwd": cwd, "base": mb, "tests": chosen, "expect": expect or "",
            "base_returncode": base_run.returncode, "head_returncode": head_run.returncode,
            "base_tail": base_run.tail, "head_tail": head_run.tail,
        },
    )  # fmt: skip
    return proof
