"""Shortcuts: the ways a change can make checks pass without making the code right.

A coding agent told to get to green has cheaper options than fixing the code: silence the type
checker (`@ts-ignore`, `# type: ignore`, `as any`), silence the linter, skip or focus a test,
delete a test, exclude code from coverage, lower the coverage threshold, or relax the type
checker's configuration. Benchmarks of coding agents (EvilGenie, SpecBench) catch them doing
exactly this. Each is sometimes legitimate, which is why this engine only *finds* them; the
checker blocks the ones a change introduces, and a person can waive one with a reason.

Everything here is plain text matching over the repository's files: no tool to install, and the
same answer on every machine.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from rung.engines.sarif import Finding

TOOL = "rung-shortcuts"
MAX_FILE_BYTES = 1_000_000

TS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
PY = (".py", ".pyi")
GO = (".go",)
RUST = (".rs",)
JVM = (".java", ".kt", ".kts")
SKIP_SUFFIXES = (".min.js", ".map", ".lock", "-lock.json", ".snap")


@dataclass(frozen=True)
class Rule:
    id: str
    message: str
    pattern: re.Pattern[str]
    suffixes: tuple[str, ...] = ()  # source files with these suffixes
    names: tuple[str, ...] = ()  # or config files with these names (globs on the file name)
    tests_only: bool = False
    level: str = "warning"
    where: str = "code"  # code | comment | line: which part of a line the pattern must match in

    def applies(self, path: str) -> bool:
        p = PurePosixPath(path)
        if self.names:
            return any(p.match(n) for n in self.names)
        return path.endswith(self.suffixes) and (not self.tests_only or is_test_file(path))


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.MULTILINE)


def _comment(rule_id: str, message: str, pattern: re.Pattern[str], suffixes: tuple[str, ...]) -> Rule:
    """A rule for a directive that lives in a comment (`# noqa`, `// @ts-ignore`)."""
    return Rule(rule_id, message, pattern, suffixes, where="comment")


PY_CONFIGS = ("pyproject.toml", "setup.cfg", "mypy.ini", ".mypy.ini", "pyrightconfig.json", "tox.ini")

RULES: tuple[Rule, ...] = (
    # Silencing the type checker.
    _comment("ts-ignore", "silences the TypeScript checker", _r(r"@ts-(?:ignore|nocheck)\b"), TS),
    Rule(
        "ts-expect-error", "expects a type error instead of fixing it", _r(r"@ts-expect-error\b"), TS, where="comment"
    ),
    Rule("any-cast", "casts the type away", _r(r"\bas\s+any\b|\bas\s+unknown\s+as\b|<any>"), TS),
    Rule(
        "type-ignore",
        "silences the Python type checker",
        _r(r"#\s*(?:type|pyright|mypy):\s*ignore"),
        PY,
        where="comment",
    ),
    # Silencing the linter.
    Rule(
        "lint-disable", "disables a lint rule", _r(r"\b(?:eslint|oxlint)-disable|\bbiome-ignore\b"), TS, where="comment"
    ),
    _comment("noqa", "disables a lint rule", _r(r"#\s*noqa\b"), PY),
    _comment("nolint", "disables a lint rule", _r(r"//\s*nolint\b"), GO),
    Rule("lint-allow", "allows a compiler or Clippy lint", _r(r"#!?\[allow\("), RUST),
    Rule("suppress-warnings", "suppresses compiler warnings", _r(r"@SuppressWarnings\b|@Suppress\("), JVM),
    Rule("unsafe", "opts out of Rust's safety checks", _r(r"\bunsafe\s*\{"), RUST),
    # Excluding code from coverage.
    Rule(
        "no-cover",
        "excludes code from coverage",
        _r(r"pragma:\s*no\s*cover|\b(?:istanbul|c8|v8)\s+ignore\b"),
        PY + TS,
        where="comment",
    ),
    # Skipping or focusing tests.
    Rule(
        "focused-test",
        "runs only this test, silently skipping the rest",
        _r(r"\b(?:describe|it|test)\.only\s*\(|\bf(?:it|describe)\s*\("),
        TS,
        tests_only=True,
        level="error",
    ),
    Rule(
        "skipped-test",
        "skips a test",
        _r(r"\b(?:describe|it|test)\.(?:skip|todo)\s*\(|\bx(?:it|describe|test)\s*\("),
        TS,
        tests_only=True,
    ),
    Rule(
        "skipped-test",
        "skips a test",
        _r(
            r"@pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail)\(|@unittest\.(?:skip|expectedFailure)"
            r"|\.skipTest\("
        ),
        PY,
        tests_only=True,
    ),
    Rule("skipped-test", "skips a test", _r(r"\bt\.Skip(?:Now|f)?\("), GO, tests_only=True),
    Rule("skipped-test", "skips a test", _r(r"#\[ignore\b"), RUST),
    Rule("skipped-test", "skips a test", _r(r"@(?:Disabled|Ignore)\b"), JVM, tests_only=True),
    # Relaxing the type checker's configuration.
    Rule(
        "relaxed-config",
        "turns off a TypeScript strictness option",
        _r(
            r'"(?:strict|noImplicitAny|strictNullChecks|strictFunctionTypes|noUncheckedIndexedAccess|noImplicitReturns'
            r'|noImplicitThis|alwaysStrict)"\s*:\s*false'
        ),
        names=("tsconfig*.json", "jsconfig*.json"),
        level="error",
        where="line",
    ),
    Rule(
        "relaxed-config",
        "relaxes the Python type checker",
        _r(
            r'\bignore_errors\s*=\s*[Tt]rue|typeCheckingMode"?\s*[=:]\s*"(?:off|basic)"'
            r"|\bdisallow_untyped_defs\s*=\s*[Ff]alse|^\s*strict\s*=\s*[Ff]alse"
        ),
        names=PY_CONFIGS,
        level="error",
        where="line",
    ),
)

# Where coverage thresholds live, and how they are spelled.
COVERAGE_FILES = (
    *PY_CONFIGS,
    ".coveragerc",
    "pytest.ini",
    "package.json",
    "jest.config.*",
    "vitest.config.*",
    "vite.config.*",
    ".nycrc*",
    ".c8rc*",
)
_THRESHOLD = re.compile(
    r"(fail_under|fail-under|cov-fail-under|\b(?:lines|branches|functions|statements)\b)[\"']?\s*[=:]?\s*(\d+(?:\.\d+)?)"
)

# Test definitions, by name, for noticing deleted tests.
_TEST_DEFS: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    (PY, _r(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")),
    (TS, _r(r"\b(?:it|test)\s*\(\s*(['\"`])(.+?)\1")),
    (GO, _r(r"^func\s+(Test\w+)\s*\(")),
    (RUST, _r(r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+(\w+)|^\s*(?:async\s+)?fn\s+(test_\w+)")),
    (JVM, _r(r"@Test\b[^\n]*\n?\s*(?:public\s+)?(?:void|fun)\s+(\w+)")),
)


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    if set(p.parts[:-1]) & {"test", "tests", "__tests__", "spec", "e2e"}:
        return True
    return (
        name.startswith("test_")
        or name == "conftest.py"
        or name.endswith(("_test.py", "_test.go", "Test.java", "Test.kt"))
        or ".test." in name
        or ".spec." in name
    )


def _scannable(path: str) -> bool:
    """Whether any rule, test definition or coverage threshold could be in this file."""
    if path.endswith(SKIP_SUFFIXES) or path.startswith(".rung/"):
        return False
    name = PurePosixPath(path)
    return (
        path.endswith(TS + PY + GO + RUST + JVM)
        or any(name.match(n) for r in RULES for n in r.names)
        or any(name.match(n) for n in COVERAGE_FILES)
    )


def split_lines(path: str, text: str) -> list[tuple[str, str]]:
    """Each line's code and comment text, with string literals removed from both.

    Silencers only count in comments and test skips only in code, so the words in a docstring, a
    string or an error message never count as shortcuts. Triple-quoted strings, template literals and
    block comments are followed across lines.
    """
    py = path.endswith(PY)
    quotes = ('"""', "'''", '"', "'") if py else ("`", '"') if path.endswith(RUST) else ("`", '"', "'")
    multiline = ('"""', "'''", "`")
    out: list[tuple[str, str]] = []
    quote, block = "", False
    for line in text.splitlines():
        code: list[str] = []
        comment = ""
        i = 0
        while i < len(line):
            if block:
                end = line.find("*/", i)
                comment += line[i:] if end < 0 else line[i : end + 2]
                if end < 0:
                    break
                block, i = False, end + 2
            elif quote:
                if line[i] == "\\":
                    i += 2
                elif line.startswith(quote, i):
                    i, quote = i + len(quote), ""
                else:
                    i += 1
            elif line.startswith("#" if py else "//", i):
                comment += line[i:]
                break
            elif not py and line.startswith("/*", i):
                block, i = True, i
            elif opener := next((q for q in quotes if line.startswith(q, i)), ""):
                quote, i = opener, i + len(opener)
            else:
                code.append(line[i])
                i += 1
        if quote and quote not in multiline:
            quote = ""  # an ordinary string ends with its line
        out.append(("".join(code), comment))
    return out


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def find(files: Iterable[tuple[str, str]], ignore: Iterable[str] = ()) -> list[Finding]:
    """Shortcuts in `(path, text)` pairs. A rule id in `ignore` is not reported."""
    skip = set(ignore)
    out: list[Finding] = []
    for path, text in files:
        if not _scannable(path):
            continue
        rules = [r for r in RULES if r.id not in skip and r.applies(path)]
        if not rules:
            continue
        parts = split_lines(path, text)
        for number, line in enumerate(text.splitlines(), 1):
            code, comment = parts[number - 1]
            for rule in rules:
                if rule.pattern.search({"code": code, "comment": comment}.get(rule.where, line)):
                    normal = " ".join(line.split())
                    out.append(
                        Finding(
                            TOOL,
                            rule.id,
                            rule.level,
                            path,
                            number,
                            f"{rule.message}: {normal[:120]}",
                            _fingerprint(rule.id, path, normal),
                        )
                    )
    return out


def thresholds(files: Iterable[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """Coverage thresholds, keyed by (file, setting)."""
    out: dict[tuple[str, str], float] = {}
    for path, text in files:
        if any(PurePosixPath(path).match(n) for n in COVERAGE_FILES):
            for m in _THRESHOLD.finditer(text):
                out[(path, m.group(1))] = float(m.group(2))
    return out


def test_names(files: Iterable[tuple[str, str]]) -> Counter[str]:
    """Every test definition's name, counted (a name in two files counts twice)."""
    names: Counter[str] = Counter()
    for path, text in files:
        if not is_test_file(path) and not path.endswith(RUST):
            continue
        for suffixes, pattern in _TEST_DEFS:
            if path.endswith(suffixes):
                for m in pattern.finditer(text):
                    # The name is the last group that matched (the TS pattern's first group is the quote).
                    names[next(g for g in reversed(m.groups()) if g and g not in "'\"`")] += 1
    return names


def compare(head: list[tuple[str, str]], base: list[tuple[str, str]], ignore: Iterable[str] = ()) -> list[Finding]:
    """Findings that only make sense against a base: deleted tests and lowered coverage thresholds."""
    skip = set(ignore)
    out: list[Finding] = []
    if "tests-removed" not in skip:
        before, after = test_names(base), test_names(head)
        if sum(after.values()) < sum(before.values()):
            removed = sorted((before - after).elements())
            shown = ", ".join(removed[:6]) + (" ..." if len(removed) > 6 else "")
            out.append(
                Finding(
                    TOOL,
                    "tests-removed",
                    "error",
                    "",
                    0,
                    f"{sum(before.values()) - sum(after.values())} fewer test(s) than the base: {shown}",
                    _fingerprint("tests-removed", *removed),
                )
            )
    if "coverage-lowered" not in skip:
        then = thresholds(base)
        for (path, key), value in thresholds(head).items():
            if (path, key) in then and value < then[(path, key)]:
                out.append(
                    Finding(
                        TOOL,
                        "coverage-lowered",
                        "error",
                        path,
                        0,
                        f"coverage threshold `{key}` lowered from {then[(path, key)]:g} to {value:g}",
                        _fingerprint("coverage-lowered", path, key, str(value)),
                    )
                )
    return out


def read_all(paths: Iterable[str], read: Callable[[str], str | None]) -> list[tuple[str, str]]:
    """`(path, text)` for every readable, reasonably sized text file among `paths`."""
    out = []
    for path in paths:
        if not _scannable(path):
            continue
        text = read(path)
        if text is not None and len(text) <= MAX_FILE_BYTES and "\0" not in text[:4096]:
            out.append((path, text))
    return out
