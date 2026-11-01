"""`rung prove`: show that a new test actually catches the bug it claims to.

A regression test that passes before the fix proves nothing; agents write such
tests all the time. The proof is mechanical: take the test files changed on this
branch, put them on top of the merge base (the code *without* the fix), and run
the test command there. It must fail. Then run it on the branch. It must pass.
Both runs are recorded in the ledger.

When the base fails for the wrong reason, typically an import error because the
fix added a new function, `--expect` names text the base failure must contain,
such as the assertion or the test name.

A file can hold several new tests, and one failing on the base says nothing about the
others. When the command runs pytest, both runs also write a JUnit XML report, and each
test the branch adds or changes is judged on its own: it must fail on the merge base and
pass on the branch. A new test that passes on the base proves nothing about the fix, so
the proof fails and names it. Other runners are judged by the whole run, as before.
"""

from __future__ import annotations

import ast
import itertools
import re
import shlex
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rung.core import git, ledger
from rung.core.errors import RungError
from rung.core.proc import run as run_process
from rung.core.redact import redact_lines

TEST_PATTERNS = [
    "**/test_*.py", "**/*_test.py", "**/tests/**", "**/__tests__/**", "**/*.test.*",
    "**/*.spec.*", "**/*_test.go", "**/spec/**", "**/*Test.java", "**/*Tests.cs",
]  # fmt: skip


@dataclass
class Run:
    returncode: int
    tail: list[str]


@dataclass
class Case:
    """One test the branch adds or changes, and its outcome on each side."""

    test: str  # `tests/test_money.py::test_refund` or `...::TestRefund::test_cap`
    base: str  # passed | failed | skipped | missing (did not run)
    head: str

    @property
    def proven(self) -> bool:
        return self.base == "failed" and self.head == "passed"


@dataclass
class Proof:
    verdict: str  # pass | fail | error
    summary: str
    tests: list[str] = field(default_factory=list)
    base: Run | None = None
    head: Run | None = None
    cases: list[Case] = field(default_factory=list)  # per test, when the runner reports them (pytest)


def is_test_file(path: str) -> bool:
    return any(git.glob_match(p, path) for p in TEST_PATTERNS)


def _run(argv: list[str], cwd: Path, timeout: int) -> Run:
    result = run_process(argv, cwd=cwd, timeout=timeout)
    return Run(result.returncode, (result.stdout + result.stderr).strip().splitlines()[-25:])


def runs_pytest(argv: list[str]) -> bool:
    """Whether a command runs pytest: `pytest ...`, `uv run pytest ...`, `python -m pytest ...`."""
    if any(PurePosixPath(a.replace("\\", "/")).name in ("pytest", "py.test", "pytest.exe") for a in argv):
        return True
    return any(a == "-m" and b == "pytest" for a, b in itertools.pairwise(argv))


def python_tests(source: str) -> dict[str, str]:
    """Each pytest test in `source` (`test_x`, `TestY.test_z`) -> a dump of its code, decorators included."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    tests = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            tests[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name.startswith("test"):
                    tests[f"{node.name}.{item.name}"] = ast.dump(item)
    return tests


def new_or_changed_tests(root: Path, base: str, files: list[str]) -> dict[str, list[str]]:
    """file -> the tests in it that the branch adds or whose code it changes (Python test files only)."""
    found = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        try:
            before = python_tests(git.show(root, base, rel))
        except git.GitError:
            before = {}
        after = python_tests((root / rel).read_text(errors="ignore"))
        if changed := [name for name, code in after.items() if before.get(name) != code]:
            found[rel] = changed
    return found


def junit_outcomes(path: Path) -> dict[tuple[str, str], str]:
    """(classname, test name without parameters) -> passed | failed | skipped, from a JUnit XML report.

    A parametrized test failed if any case failed; it passed if none failed and one passed.
    """
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return {}
    outcomes: dict[tuple[str, str], str] = {}
    rank = {"skipped": 0, "passed": 1, "failed": 2}
    for case in tree.iter("testcase"):
        key = (case.get("classname", ""), case.get("name", "").split("[", 1)[0])
        tags = {child.tag for child in case}
        outcome = "failed" if tags & {"failure", "error"} else "skipped" if "skipped" in tags else "passed"
        if rank[outcome] >= rank.get(outcomes.get(key, "skipped"), 0):
            outcomes[key] = outcome
    return outcomes


def _qualname(classname: str, name: str, module: list[str]) -> str | None:
    """The test's name inside its module, if the JUnit classname belongs to the module at path `module`."""
    for start in range(len(module)):
        dotted = ".".join(module[start:])
        if classname == dotted:
            return name
        if classname.startswith(dotted + "."):
            return f"{classname[len(dotted) + 1 :]}.{name}"
    return None


def per_test(
    wanted: dict[str, list[str]], base: dict[tuple[str, str], str], head: dict[tuple[str, str], str]
) -> list[Case]:
    """Match the new or changed tests to the outcomes of both runs."""

    def outcome(results: dict[tuple[str, str], str], rel: str, test: str) -> str:
        module = list(PurePosixPath(rel).with_suffix("").parts)
        hits = [o for (cls, name), o in results.items() if _qualname(cls, name, module) == test]
        return max(hits, key=["skipped", "passed", "failed"].index) if hits else "missing"

    return [
        Case(f"{rel}::{test.replace('.', '::')}", outcome(base, rel, test), outcome(head, rel, test))
        for rel, tests in sorted(wanted.items())
        for test in tests
    ]


def _names(cases: list[Case]) -> str:
    return ", ".join(c.test for c in cases)


# Output that means the tests never reached an assertion on the base: they could not even load.
SETUP_FAILURE = re.compile(
    r"ModuleNotFoundError|ImportError|SyntaxError|NameError: name .* is not defined|error collecting|"
    r"ERROR collecting|errors? during collection|no tests ran|collected 0 items|Cannot find module|"
    r"is not exported|has no exported member|TS2307|TS2305|undefined: |cannot find package",
)


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
    sources = sorted(f for f in changed if not is_test_file(f) and (root / f).is_file())
    before = ledger.snapshot(root, sources + chosen)

    argv = shlex.split(command)
    wanted = new_or_changed_tests(root, mb, chosen) if runs_pytest(argv) else {}
    with tempfile.TemporaryDirectory(prefix="rung-prove-") as tmp:
        report = {side: Path(tmp) / f"{side}.xml" for side in ("base", "head")}
        extra = {side: [f"--junitxml={path}"] if wanted else [] for side, path in report.items()}
        with git.worktree(root, mb) as tree:
            for rel in chosen:
                dest = tree / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, dest)
            base_run = _run(argv + extra["base"], tree / cwd, timeout)
        head_run = _run(argv + extra["head"], root / cwd, timeout)
        cases = per_test(wanted, junit_outcomes(report["base"]), junit_outcomes(report["head"])) if wanted else []
    if cases and all(c.head == "missing" for c in cases):
        cases = []  # the report did not name them (an unusual rootdir or plugin): judge the whole run

    proof = Proof("pass", "", chosen, base_run, head_run, cases)
    base_text = "\n".join(base_run.tail)
    weak = [c for c in cases if c.base in ("passed", "skipped")]
    not_run = [c for c in cases if c.base == "missing"]
    not_passing = [c for c in cases if c.head != "passed"]
    if base_run.returncode == 0:
        proof.verdict, proof.summary = "fail", "the tests pass without the fix, so they do not catch the bug"
    elif weak:
        proof.verdict = "fail"
        proof.summary = (
            f"{len(weak)} new or changed test(s) also pass (or skip) on the merge base, so they prove nothing "
            f"about the fix: {_names(weak)}"
        )
    elif expect and expect not in base_text:
        proof.verdict = "fail"
        proof.summary = f"the base run failed, but not with {expect!r}; it may be failing for another reason"
    elif not expect and SETUP_FAILURE.search(base_text):
        proof.verdict = "fail"
        proof.summary = (
            "on the merge base the tests failed while loading (an import or collection error), not on an "
            "assertion. If that failure is the bug, re-run with --expect '<text of the failing assertion>'"
        )
    elif not expect and not_run:
        proof.verdict = "fail"
        proof.summary = f"{len(not_run)} new or changed test(s) did not run on the merge base: {_names(not_run)}"
    elif head_run.returncode != 0:
        proof.verdict = "fail"
        proof.summary = "the tests fail on this branch too" + (f": {_names(not_passing)}" if not_passing else "")
    elif not_passing:
        proof.verdict = "fail"
        proof.summary = f"{len(not_passing)} new or changed test(s) did not pass on this branch: {_names(not_passing)}"
    elif cases:
        proof.summary = (
            f"{len(cases)} new or changed test(s) fail on merge base {mb[:10]} and pass on this branch: "
            + _names(cases)
        )
    else:
        proof.summary = f"fails on merge base {mb[:10]}, passes on this branch"
    if ledger.snapshot(root, sources + chosen) != before:
        proof.verdict, proof.summary = "error", "files changed while the proof ran; re-run it"

    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:40] or "tests"
    ledger.record(
        root,
        check_id=f"proof:{slug}",
        kind="proof",
        verdict=proof.verdict,
        covers=before,
        specs={},
        summary=proof.summary,
        context=mb,
        details={
            "command": command, "cwd": cwd, "base": mb, "tests": chosen, "expect": expect or "",
            "files": sorted(before),
            "base_returncode": base_run.returncode, "head_returncode": head_run.returncode,
            "base_tail": redact_lines(base_run.tail), "head_tail": redact_lines(head_run.tail),
            "cases": [c.__dict__ for c in cases],
        },
    )  # fmt: skip
    return proof
