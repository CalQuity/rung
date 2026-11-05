"""`rung prove`: show that a new test actually catches the bug it claims to.

A regression test that passes before the fix proves nothing; agents write such
tests all the time. The proof is mechanical: take the test files changed on this
branch, put them on top of the merge base (the code *without* the fix), and run
the test command there. It must fail. Then run it on the branch. It must pass.
Both runs are recorded in the ledger.

A base run that fails while loading (an import or collection error) is rejected, because the
tests never reached an assertion. When that failure *is* the bug, or the runner cannot report
individual tests, `--expect` names text the base failure must contain, such as the assertion
message or the missing name in an `ImportError`. It is matched against the whole base output,
stdout and stderr, on a line that reads as a failure (an error or exception name, `assert`,
`fail`, `panic`, or a `want`/`expected`/`got` comparison). A matching `--expect` also accepts new
tests that the base could not run at all.

A file can hold several new tests, and one failing on the base says nothing about the
others. When the command runs pytest, both runs also write a JUnit XML report, and each
test the branch adds or changes is judged on its own: it must fail on the merge base and
pass on the branch. Node's built-in test runner reports each case in a new test file
and lets the caller name cases in edited files (`--case FILE::NAME`, where a case inside
`describe` blocks is named with its suites, `refund > caps`). Other runners are judged by
the whole run.

A proof covers the project's files except its documentation (`*.md`, `*.rst` and `docs/`):
editing code, configuration or fixtures stales it, editing a README does not.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rung.core import git, launch, ledger
from rung.core.config import check_command
from rung.core.errors import RungError
from rung.core.proc import run as run_process
from rung.core.redact import plain_text, redact_lines

TEST_PATTERNS = [
    "**/test_*.py", "**/*_test.py", "**/tests/**", "**/__tests__/**", "**/*.test.*",
    "**/*.spec.*", "**/*_test.go", "**/spec/**", "**/*Test.java", "**/*Tests.cs",
]  # fmt: skip


@dataclass
class Run:
    returncode: int
    tail: list[str]
    stderr: list[str] = field(default_factory=list)
    output: str = ""  # everything the run printed, stdout then stderr, without terminal escapes


@dataclass
class Case:
    """One test the branch adds or changes, and its outcome on each side."""

    test: str  # `tests/test_money.py::test_refund` or `...::TestRefund::test_cap`
    base: str  # passed | failed | skipped | error (fixture) | missing (did not run)
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
    return Run(result.returncode, (result.stdout + result.stderr).strip().splitlines()[-25:],
               result.stderr.strip().splitlines()[-25:],
               plain_text(result.stdout + "\n" + result.stderr))  # fmt: skip


def runs_pytest(argv: list[str]) -> bool:
    """Whether a command runs pytest: `pytest ...`, `uv run pytest ...`, `python -m pytest ...`."""
    cwd = Path(".")
    while inner := launch.delegated(argv, cwd):
        argv, cwd = inner.argv, inner.cwd
    return bool(argv) and (
        Path(argv[0]).name in {"pytest", "py.test", "pytest.exe"}
        or launch.python_entrypoint(argv) == ("module", "pytest")
    )


def _runs_node(argv: list[str]) -> bool:
    return Path(argv[0]).name in {"node", "node.exe"} and "--test" in argv


def node_records(path: Path, root: Path) -> list[tuple[str, str, str]]:
    """Every case the Node reporter wrote, as (file relative to `root`, `suite > name`, status).

    Repeats are kept: a test defined in a loop reports once per iteration under the same name.
    """
    try:
        records = [json.loads(line) for line in path.read_text().splitlines()]
    except (OSError, ValueError):
        return []
    found = []
    for case in records:
        source = (root / case["file"]).resolve()
        if source.is_relative_to(root):
            found.append((source.relative_to(root).as_posix(), case["name"], case["status"]))
    return found


def node_outcomes(path: Path, root: Path) -> dict[tuple[str, str], str]:
    """(file, `suite > name`) -> the worst outcome of the cases with that identity, as for pytest parameters."""
    outcomes: dict[tuple[str, str], str] = {}
    for file, name, status in node_records(path, root):
        key = (file, name)
        outcomes[key] = max(outcomes.get(key, "passed"), status, key=OUTCOME_ORDER.index)
    return outcomes


def node_reporter_flags(report: Path) -> list[str]:
    reporter = Path(__file__).with_name("node_reporter.mjs").resolve().as_uri()
    return ["--test-reporter=spec", f"--test-reporter={reporter}", "--test-reporter-destination=stdout",
            f"--test-reporter-destination={report}"]  # fmt: skip


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


OUTCOME_ORDER = ("passed", "skipped", "failed", "error")


def junit_outcomes(path: Path) -> dict[tuple[str, str], str]:
    """Summarize a test by its worst case; a fixture error never proves a regression."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return {}
    outcomes: dict[tuple[str, str], str] = {}
    for case in tree.iter("testcase"):
        key = (case.get("classname", ""), case.get("name", "").split("[", 1)[0])
        tags = {child.tag for child in case}
        outcome = (
            "error"
            if "error" in tags
            else "failed"
            if "failure" in tags
            else ("skipped" if "skipped" in tags else "passed")
        )
        outcomes[key] = max(outcomes.get(key, "passed"), outcome, key=OUTCOME_ORDER.index)
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
        return max(hits, key=OUTCOME_ORDER.index) if hits else "missing"

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
# A line that reports a failure: an error or exception name (`ImportError:`, `TypeError`), an assertion,
# `fail`/`panic`, or a comparison such as Go's `got X, want Y` or `expected X`.
FAILURE_LINE = re.compile(
    r"\b(?:\w*Error|\w*Exception|assert\w*|fail(?:s|ed|ure|ures)?|panic\w*|want|wanted|expected|got)\b", re.I
)
# Documentation never changes what a proof is about: editing it must not stale the proof.
DOC_SUFFIXES = (".md", ".rst")
DOC_DIRS = ("docs/",)


def is_documentation(path: str) -> bool:
    """`*.md` and `*.rst` anywhere, and everything under the top-level `docs/` directory."""
    return path.lower().endswith(DOC_SUFFIXES) or path.startswith(DOC_DIRS)


def proof_scope(root: Path) -> list[str]:
    """Glob patterns for the project's files except documentation, which a proof's verdict covers.

    The patterns stay compact (one per top-level directory and file suffix, plus top-level and
    suffix-less files by name), so checking a proof's freshness does not glob every file
    against every other. A new file of a covered kind stales the proof too.
    """
    patterns: set[str] = set()
    for rel in git.expand(root, ["**/*"]):
        if is_documentation(rel):
            continue
        top, nested, _ = rel.partition("/")
        suffix = PurePosixPath(rel).suffix
        if nested and suffix and PurePosixPath(rel).stem and not any(c in rel for c in "*?["):
            patterns.add(f"{top}/**/*{suffix}")
        else:
            patterns.add(rel)
    return sorted(patterns)


def expect_found(expect: str, run: Run) -> bool:
    """Whether the run's output reports a failure containing `expect`, on stdout or stderr."""
    stderr = set(run.stderr)
    return any(expect in line and (line in stderr or FAILURE_LINE.search(line)) for line in run.output.splitlines())


def prove(
    root: Path, command: str, base_ref: str, *, tests: list[str] | None = None,
    expect: str | None = None, timeout: int = 1800, cwd: str = ".",
    node_cases: list[str] | None = None,
) -> Proof:  # fmt: skip
    mb = git.merge_base(root, base_ref)
    changed = git.changed_files(root, base_ref)
    chosen = sorted(tests) if tests else sorted(f for f in changed if is_test_file(f))
    if not chosen:
        return Proof("error", "no changed test files on this branch; name them with --tests")
    root = root.resolve()
    for rel in [cwd, *chosen]:
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts or (root / path).resolve() != root / path:
            raise RungError(f"use a path inside the project without symlinks: {rel}")
    missing = [t for t in chosen if not (root / t).is_file()]
    if missing:
        raise RungError(f"test file(s) not found: {', '.join(missing)}")
    scope = proof_scope(root)
    before = ledger.snapshot(root, scope)
    check_command("prove", "command", command)
    argv = shlex.split(command)
    pytest_run = runs_pytest(argv)
    node_run = _runs_node(argv)
    wanted = new_or_changed_tests(root, mb, chosen) if pytest_run else {}
    if node_cases and not node_run:
        return Proof("error", "--case requires a direct node --test command", chosen)
    if not (pytest_run or node_run or expect):
        return Proof("error", "name the expected failure with --expect when the runner cannot report individual tests")
    if pytest_run and not wanted:
        return Proof("error", "no new or changed pytest test functions found in the selected files", chosen)
    named: set[tuple[str, str]] = set()
    new_files: set[str] = set()
    if node_run:
        if any(arg.startswith("--test-reporter") for arg in argv):
            return Proof("error", "remove custom Node reporters so Rung can read individual results", chosen)
        for selector in node_cases or []:
            file, _, name = selector.partition("::")
            if file not in chosen or not name:
                return Proof("error", f"use --case FILE::NAME with a selected test file: {selector!r}", chosen)
            named.add((file, name))
        new_files = set(chosen) - set(git.files_at(root, mb))
        if unnamed := sorted(set(chosen) - new_files - {file for file, _ in named}):
            return Proof("error", "name cases in edited Node test files with --case FILE::NAME: " + ", ".join(unnamed),
                         chosen)  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="rung-prove-") as tmp:
        suffix = ".jsonl" if node_run else ".xml"
        report = {side: Path(tmp) / f"{side}{suffix}" for side in ("base", "head")}
        extra = {side: [f"--junitxml={path}"] if wanted else [] for side, path in report.items()}
        if node_run:
            extra = {side: node_reporter_flags(path) for side, path in report.items()}
        with git.worktree(root, mb) as tree:
            if (tree / cwd).resolve() != tree.resolve() / cwd:
                raise RungError(f"the merge base contains a symlink in the working directory: {cwd}")
            for rel in chosen:
                dest = tree / rel
                if dest.resolve() != tree.resolve() / rel:
                    raise RungError(f"the merge base contains a symlink in the test path: {rel}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, dest)
            base_argv = [argv[0], *extra["base"], *argv[1:]] if node_run else argv + extra["base"]
            base_run = _run(base_argv, tree / cwd, timeout)
            base_node = node_outcomes(report["base"], tree.resolve()) if node_run else {}
        head_argv = [argv[0], *extra["head"], *argv[1:]] if node_run else argv + extra["head"]
        head_run = _run(head_argv, root / cwd, timeout)
        cases = per_test(wanted, junit_outcomes(report["base"]), junit_outcomes(report["head"])) if wanted else []
        if node_run:
            head_node = node_outcomes(report["head"], root)
            for file in new_files:
                found = {key for key in head_node if key[0] == file}
                named.update(found or {(file, "(no reported cases)")})
            cases = [
                Case(f"{file}::{name}", base_node.get((file, name), "missing"), head_node.get((file, name), "missing"))
                for file, name in sorted(named)
            ]
    proof = Proof("pass", "", chosen, base_run, head_run, cases)
    base_text = "\n".join(base_run.tail)
    weak = [c for c in cases if c.base in ("passed", "skipped")]
    not_run = [c for c in cases if c.base in ("missing", "error")]
    not_passing = [c for c in cases if c.head != "passed"]
    if base_run.returncode == 0:
        proof.verdict, proof.summary = "fail", "the tests pass without the fix, so they do not catch the bug"
    elif weak:
        proof.verdict = "fail"
        proof.summary = (
            f"{len(weak)} new or changed test(s) also pass (or skip) on the merge base, so they prove nothing "
            f"about the fix: {_names(weak)}"
        )
    elif expect and not expect_found(expect, base_run):
        proof.verdict = "fail"
        proof.summary = f"the base run failed, but not with {expect!r}; it may be failing for another reason"
    elif not expect and SETUP_FAILURE.search(base_text):
        proof.verdict = "fail"
        proof.summary = (
            "on the merge base the tests failed while loading (an import or collection error), not on an "
            "assertion. If that failure is the bug, re-run with --expect '<text from that error that shows "
            "the bug, e.g. the missing name>'"
        )
    elif not_run and not expect:
        proof.verdict = "fail"
        proof.summary = f"{len(not_run)} test(s) did not reach a test failure on the merge base: {_names(not_run)}"
    elif head_run.returncode != 0:
        proof.verdict = "fail"
        proof.summary = "the tests fail on this branch too" + (f": {_names(not_passing)}" if not_passing else "")
    elif not_passing:
        proof.verdict = "fail"
        proof.summary = f"{len(not_passing)} new or changed test(s) did not pass on this branch: {_names(not_passing)}"
    elif not_run:
        proof.summary = (
            f"the merge base failed with {expect!r} before reaching {len(not_run)} test(s), which pass on this "
            f"branch: {_names(not_run)}"
        )
    elif cases:
        proof.summary = (
            f"{len(cases)} new or changed test(s) fail on merge base {mb[:10]} and pass on this branch: "
            + _names(cases)
        )
    else:
        proof.summary = f"suite fails on merge base {mb[:10]} and passes on this branch"
    # Only files that existed before the run count: reports and caches the tests create
    # (coverage.xml, junit, .coverage) are not edits to the code under proof.
    after = ledger.snapshot(root, scope)
    if moved := sorted(path for path, digest in before.items() if after.get(path) != digest):
        proof.verdict = "error"
        proof.summary = "files changed while the proof ran; re-run it: " + ", ".join(moved[:5])
    covered = before if moved else after

    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:40] or "tests"
    ledger.record(
        root,
        check_id=f"proof:{slug}",
        kind="proof",
        verdict=proof.verdict,
        covers=covered,
        specs={},
        summary=proof.summary,
        context=mb,
        details={
            "command": command, "cwd": cwd, "base": mb, "tests": chosen, "expect": expect or "",
            "files": sorted(covered), "covers": scope,
            "base_returncode": base_run.returncode, "head_returncode": head_run.returncode,
            "base_tail": redact_lines(base_run.tail), "head_tail": redact_lines(head_run.tail),
            "scope": "case" if cases else "suite",
            "cases": [c.__dict__ for c in cases],
        },
    )  # fmt: skip
    return proof
