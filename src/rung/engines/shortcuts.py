"""Shortcuts: the ways a change can make checks pass without making the code right.

A coding agent told to get to green has cheaper options than fixing the code: silence the type
checker (`@ts-ignore`, `# type: ignore`, `as any`), silence the linter, skip or focus a test,
delete a test, exclude code from coverage, lower or remove the coverage threshold, or relax the
type checker's configuration. Benchmarks of coding agents (EvilGenie, SpecBench) catch them doing
exactly this. Each is sometimes legitimate, which is why this engine only *finds* them; the
checker blocks the ones a change introduces, and a person can waive one with a reason.

Everything here is text matching with a small per-language lexer (strings, comments, regex
literals): no tool to install, and the same answer on every machine. It is a net for the common
forms, not a proof that a test suite was not weakened; replacing an assertion with `pass` keeps
every count the same.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from rung.core.redact import redact
from rung.engines.sarif import Finding

TOOL = "rung-shortcuts"
MAX_FILE_BYTES = 1_000_000

TS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
PY = (".py", ".pyi")
GO = (".go",)
RUST = (".rs",)
JVM = (".java", ".kt", ".kts")
SKIP_SUFFIXES = (".min.js", ".map", ".lock", "-lock.json", ".snap")
TS_CONFIGS = ("tsconfig*.json", "jsconfig*.json")
PY_CONFIGS = ("pyproject.toml", "setup.cfg", "mypy.ini", ".mypy.ini", "pyrightconfig.json", "tox.ini")
# Files that configure only a type checker; in the others, a setting counts only in the checker's own section.
CHECKER_ONLY = ("mypy.ini", ".mypy.ini", "pyrightconfig.json")


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
    sections: tuple[str, ...] = ()  # in INI/TOML files, only inside sections whose header names one of these

    def applies(self, path: str) -> bool:
        p = PurePosixPath(path)
        if self.names:
            return any(p.match(n) for n in self.names)
        return path.endswith(self.suffixes) and (not self.tests_only or is_test_file(path))


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.MULTILINE)


def _comment(rule_id: str, message: str, pattern: str, suffixes: tuple[str, ...]) -> Rule:
    """A rule for a directive that lives in a comment (`# noqa`, `// @ts-ignore`)."""
    return Rule(rule_id, message, _r(pattern), suffixes, where="comment")


def _tests(rule_id: str, message: str, pattern: str, suffixes: tuple[str, ...], level: str = "warning") -> Rule:
    """A rule for code that only matters in test files (`it.only`, `@pytest.mark.skip`)."""
    return Rule(rule_id, message, _r(pattern), suffixes, tests_only=True, level=level)


RULES: tuple[Rule, ...] = (
    # Silencing the type checker.
    _comment("ts-ignore", "silences the TypeScript checker", r"@ts-(?:ignore|nocheck)\b", TS),
    _comment("ts-expect-error", "expects a type error instead of fixing it", r"@ts-expect-error\b", TS),
    Rule("any-cast", "casts the type away", _r(r"\bas\s+any\b|\bas\s+unknown\s+as\b|<any>"), TS),
    _comment(
        "type-ignore",
        "silences the Python type checker",
        r"#\s*(?:type|pyright|mypy):\s*ignore|#\s*pyright:\s*(?:basic\b|report\w+\s*=\s*(?:false|none|off)\b)",
        PY,
    ),
    # Silencing the linter.
    _comment("lint-disable", "disables a lint rule", r"\b(?:eslint|oxlint)-disable|\bbiome-ignore\b", TS),
    _comment("lint-disable", "disables a lint rule", r"#\s*pylint:\s*disable", PY),
    _comment("noqa", "disables a lint rule", r"#\s*noqa\b", PY),
    _comment("nolint", "disables a lint rule", r"//\s*(?:nolint\b|lint:ignore\b)", GO),
    Rule("lint-allow", "allows a compiler or Clippy lint", _r(r"#!?\[(?:allow|expect)\("), RUST),
    Rule("suppress-warnings", "suppresses compiler warnings", _r(r"@SuppressWarnings\b|@Suppress\("), JVM),
    Rule("unsafe", "opts out of Rust's safety checks", _r(r"\bunsafe\s*\{"), RUST),
    # Excluding code from coverage.
    _comment(
        "no-cover", "excludes code from coverage", r"pragma:\s*no\s*cover|\b(?:istanbul|c8|v8)\s+ignore\b", PY + TS
    ),
    # Skipping or focusing tests.
    _tests(
        "focused-test",
        "runs only this test, silently skipping the rest",
        r"\b(?:describe|it|test)\.only\b|\bf(?:it|describe)\s*\(",
        TS,
        level="error",
    ),
    _tests(
        "skipped-test",
        "skips a test",
        r"\b(?:describe|it|test)\.(?:skip|todo|skipIf)\b|\bx(?:it|describe|test)\s*\(",
        TS,
    ),
    _tests(
        "skipped-test",
        "skips a test",
        r"@pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail)\(|@(?:unittest\.)?skip(?:If|Unless)?\s*\("
        r"|@unittest\.expectedFailure|\.skipTest\(",
        PY,
    ),
    _tests("skipped-test", "skips a test", r"\bt\.Skip(?:Now|f)?\(", GO),
    Rule("skipped-test", "skips a test", _r(r"#\[ignore\b"), RUST),
    _tests("skipped-test", "skips a test", r"@(?:Disabled\w*|Ignore)\b", JVM),
    # Relaxing the type checker's configuration.
    Rule(
        "relaxed-config",
        "turns off a TypeScript strictness option",
        _r(
            r'"(?:strict|noImplicitAny|strictNullChecks|strictFunctionTypes|noUncheckedIndexedAccess|noImplicitReturns'
            r'|noImplicitThis|alwaysStrict)"\s*:\s*false|"noCheck"\s*:\s*true'
        ),
        names=TS_CONFIGS,
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
        sections=("mypy", "pyright"),
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
_STRICT_TRUE = re.compile(r'"strict"\s*:\s*true')

# Test definitions, by name, for noticing deleted tests. The name is the group called `name`.
_TEST_DEFS: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    (PY, _r(r"^\s*(?:async\s+)?def\s+(?P<name>test\w*)\s*\(")),
    (TS, _r(r"\b(?:it|test)\s*\(\s*(?P<q>['\"`])(?P<name>.*?)(?P=q)")),
    (GO, _r(r"^func\s+(?P<name>Test\w+)\s*\(")),
    (RUST, _r(r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+(?P<name>\w+)")),
    (JVM, _r(r"@Test\b[^\n]*\n?\s*(?:public\s+)?(?:void|fun)\s+(?P<name>\w+)")),
)
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "e2e", "integrationTest", "androidTest", "testFixtures"}


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    if set(p.parts[:-1]) & _TEST_DIRS or any(part.endswith("Test") for part in p.parts[:-1]):
        return True
    return (
        name.startswith("test_")
        or name == "conftest.py"
        or name.endswith(("_test.py", "_test.go", "Test.java", "Test.kt", "Tests.java", "Tests.kt", "IT.java"))
        or ".test." in name
        or ".spec." in name
    )


def scannable(path: str) -> bool:
    """Whether any rule, test definition or coverage threshold could be in this file."""
    if path.endswith(SKIP_SUFFIXES) or path.startswith(".rung/"):
        return False
    name = PurePosixPath(path)
    return (
        path.endswith(TS + PY + GO + RUST + JVM)
        or any(name.match(n) for r in RULES for n in r.names)
        or any(name.match(n) for n in COVERAGE_FILES)
    )


_PROSE = re.compile(r"`[^`]*`")
_RUST_CHAR = re.compile(r"'(?:\\.[^']*|[^'\\])'")
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")


def split_lines(path: str, text: str) -> list[tuple[str, str]]:
    """Each line's code and comment text, with string, character and regex literals removed from both.

    Silencers only count in comments and test skips only in code, so the words in a docstring, a
    string or an error message never count as shortcuts. Triple-quoted strings, Java text blocks,
    template literals and block comments are followed across lines.
    """
    py, rust, js = path.endswith(PY), path.endswith(RUST), path.endswith(TS)
    if py:
        quotes: tuple[str, ...] = ('"""', "'''", '"', "'")
    elif rust:
        quotes = ('"',)
    elif path.endswith(JVM):
        quotes = ('"""', '"', "'")
    else:
        quotes = ("`", '"', "'")
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
                block = True
            elif rust and line[i] == "'":
                m = _RUST_CHAR.match(line, i)
                if m:  # a character literal such as '"' or '\''; otherwise a lifetime like 'a
                    i = m.end()
                else:
                    code.append("'")
                    i += 1
            elif js and line[i] == "/" and _regex_can_start(code):
                i = _skip_regex(line, i)
            elif opener := next((q for q in quotes if line.startswith(q, i)), ""):
                quote, i = opener, i + len(opener)
            else:
                code.append(line[i])
                i += 1
        if quote and quote not in multiline:
            quote = ""  # an ordinary string ends with its line
        out.append(("".join(code), comment))
    return out


def _regex_can_start(code: list[str]) -> bool:
    """A `/` starts a regex literal (not a division) after an operator, a bracket or the line start."""
    prior = "".join(code).rstrip()
    return not prior or prior[-1] in _REGEX_BEFORE or prior.endswith(("return", "typeof", "case"))


def _skip_regex(line: str, i: int) -> int:
    j, in_class = i + 1, False
    while j < len(line):
        ch = line[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return j + 1
        j += 1
    return len(line)  # not a regex after all (an unclosed `/`): the rest of the line is skipped


def _sections(path: str, text: str) -> list[str]:
    """The INI/TOML section header in force on each line (empty outside any section)."""
    current, out = "", []
    for line in text.splitlines():
        m = re.match(r"\s*\[+([^\]]+)\]+\s*$", line)
        if m:
            current = m.group(1).strip().lower()
        out.append(current)
    return out


def _directive(text: str, match: re.Match[str]) -> str:
    """The directive and its arguments (`# type: ignore[attr-defined]`), without trailing explanation."""
    tail = re.split("\\s+(?:#|--?|\u2013|\u2014)\\s+", text[match.start() :], maxsplit=1)[0]
    return " ".join(tail.split())


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def find(files: Iterable[tuple[str, str]], ignore: Iterable[str] = ()) -> list[Finding]:
    """Shortcuts in `(path, text)` pairs. A rule id in `ignore` is not reported."""
    skip = set(ignore)
    out: list[Finding] = []
    for path, text in files:
        if not scannable(path):
            continue
        # A rule that cannot match anywhere in the file cannot match in its code or comments either;
        # most files have none, and only they skip the (slower) comment and string splitting.
        rules = [r for r in RULES if r.id not in skip and r.applies(path) and r.pattern.search(text)]
        if not rules:
            continue
        parts = split_lines(path, text)
        sections = _sections(path, text) if any(r.sections for r in rules) else []
        for number, line in enumerate(text.splitlines(), 1):
            code, comment = parts[number - 1]
            for rule in rules:
                outside = not any(s in sections[number - 1] for s in rule.sections) if rule.sections else False
                if outside and PurePosixPath(path).name not in CHECKER_ONLY:
                    continue
                # Backticks in comments quote an example without activating the directive.
                target = {"code": code, "comment": _PROSE.sub("", comment)}.get(rule.where, line)
                if m := rule.pattern.search(target):
                    # The fingerprint is the directive plus the code it sits on, so editing the
                    # explanation after `# type: ignore[x]` does not make it look new.
                    key = _directive(target, m) if rule.where == "comment" else ""
                    normal = " ".join(line.split())
                    message = redact(f"{rule.message}: {normal[:120]}")
                    fingerprint = _fingerprint(rule.id, path, key, " ".join(code.split()))
                    out.append(Finding(TOOL, rule.id, rule.level, path, number, message, fingerprint))
    return out


def thresholds(files: Iterable[tuple[str, str]]) -> dict[tuple[str, str, int], float]:
    """Coverage thresholds, keyed by (file, setting, occurrence), so nested settings stay apart."""
    out: dict[tuple[str, str, int], float] = {}
    for path, text in files:
        if any(PurePosixPath(path).match(n) for n in COVERAGE_FILES):
            seen: Counter[str] = Counter()
            for m in _THRESHOLD.finditer(text):
                seen[m.group(1)] += 1
                out[(path, m.group(1), seen[m.group(1)])] = float(m.group(2))
    return out


def test_names(files: Iterable[tuple[str, str]]) -> Counter[str]:
    """Every executable test definition's name, counted (a name in two files counts twice)."""
    names: Counter[str] = Counter()
    for path, text in files:
        if not is_test_file(path) and not path.endswith(RUST):
            continue
        for suffixes, pattern in _TEST_DEFS:
            if not path.endswith(suffixes) or not pattern.search(text):
                continue
            parts = split_lines(path, text)
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start())
                code = parts[line][0] if line < len(parts) else ""
                # A definition inside a string or comment (a docstring example) is not a test.
                if re.search(r"\b(?:def|it|test|func|fn|void|fun)\b", code):
                    names[m.group("name")] += 1
    return names


def compare(head: list[tuple[str, str]], base: list[tuple[str, str]], ignore: Iterable[str] = ()) -> list[Finding]:
    """Findings that only make sense against a base: deleted tests, and weakened coverage or strictness."""
    skip = set(ignore)
    out: list[Finding] = []
    if "tests-removed" not in skip:
        before, after = test_names(base), test_names(head)
        if sum(after.values()) < sum(before.values()):
            removed = sorted((before - after).elements())
            shown = redact(", ".join(removed[:6]) + (" ..." if len(removed) > 6 else ""))
            message = f"{sum(before.values()) - sum(after.values())} fewer test(s) than the base: {shown}"
            out.append(Finding(TOOL, "tests-removed", "error", "", 0, message, _fingerprint("tests-removed", *removed)))
    if "coverage-lowered" not in skip:
        then, now = thresholds(base), thresholds(head)
        head_paths = {p for p, _ in head}
        for (path, key, n), value in then.items():
            current = now.get((path, key, n))
            if current is not None and current >= value:
                continue
            if current is None and path not in head_paths:
                continue  # the whole file went away; its own deletion shows in review
            change = f"lowered from {value:g} to {current:g}" if current is not None else f"removed (was {value:g})"
            out.append(Finding(TOOL, "coverage-lowered", "error", path, 0, f"coverage threshold `{key}` {change}",
                               _fingerprint("coverage-lowered", path, key, str(n), str(current))))  # fmt: skip
    if "relaxed-config" not in skip:
        now_text = dict(head)
        for path, text in base:
            is_ts_config = any(PurePosixPath(path).match(n) for n in TS_CONFIGS)
            if (
                is_ts_config
                and _STRICT_TRUE.search(text)
                and path in now_text
                and not _STRICT_TRUE.search(now_text[path])
            ):
                out.append(Finding(TOOL, "relaxed-config", "error", path, 0,
                                       'TypeScript `"strict": true` was removed',
                                       _fingerprint("relaxed-config", path, "strict-removed")))  # fmt: skip
    return out


def read_all(paths: Iterable[str], read: Callable[[str], str | None]) -> list[tuple[str, str]]:
    """`(path, text)` for every readable, reasonably sized text file among `paths`."""
    out = []
    for path in paths:
        if not scannable(path):
            continue
        text = read(path)
        if text is not None and len(text) <= MAX_FILE_BYTES and "\0" not in text[:4096]:
            out.append((path, text))
    return out
