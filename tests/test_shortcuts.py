from __future__ import annotations

from pathlib import Path

from rung.checks import runner
from rung.core import config
from rung.engines import shortcuts

from .conftest import commit_all, run_git


def _rules(files: dict[str, str]) -> list[str]:
    return sorted(f.rule for f in shortcuts.find(files.items()))


def test_finds_silencers_and_skips_in_the_files_they_apply_to() -> None:
    files = {
        "src/a.ts": "const x = y as any // @ts-ignore\n// eslint-disable-next-line no-console\n",
        "src/a.test.ts": "it.only('adds', () => {})\nit.skip('subtracts', () => {})\n",
        "src/a.py": "import x  # type: ignore[import]\ny = 1  # noqa: E501\n",
        "tests/test_a.py": "@pytest.mark.skip(reason='flaky')\ndef test_a(): pass\n",
        "src/plain.ts": "it.only('in a non-test file is not a test', () => {})\n",
        "tsconfig.json": '{"compilerOptions": {"strict": false}}\n',
    }
    assert _rules(files) == sorted([
        "any-cast", "ts-ignore", "lint-disable", "focused-test", "skipped-test", "type-ignore", "noqa",
        "skipped-test", "relaxed-config",
    ])  # fmt: skip


def test_deleted_tests_and_a_lowered_threshold_are_found_against_the_base() -> None:
    base = [("tests/test_a.py", "def test_one(): pass\ndef test_two(): pass\n"),
            ("pyproject.toml", "[tool.coverage.report]\nfail_under = 90\n")]  # fmt: skip
    head = [
        ("tests/test_a.py", "def test_one(): pass\n"),
        ("pyproject.toml", "[tool.coverage.report]\nfail_under = 60\n"),
    ]
    found = {f.rule: f for f in shortcuts.compare(head, base)}
    assert "test_two" in found["tests-removed"].message
    assert "90 to 60" in found["coverage-lowered"].message


def test_renaming_a_test_is_not_deleting_one() -> None:
    base = [("a.test.ts", "test('adds numbers', () => {})\n")]
    head = [("b.test.ts", "test('adds two numbers', () => {})\n")]
    assert shortcuts.compare(head, base) == []


def _project(repo: Path) -> config.Config:
    (repo / "rung.toml").write_text('[[check]]\nid = "shortcuts"\nkind = "shortcuts"\n')
    return config.load(repo)


def test_existing_shortcuts_pass_and_new_ones_block(repo: Path) -> None:
    (repo / "app.py").write_text("x = 1  # type: ignore\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    cfg = _project(repo)
    commit_all(repo, "existing code")
    assert runner.run_check(cfg, cfg.check("shortcuts")).verdict == "pass"

    run_git(repo, "checkout", "-qb", "agent")
    (repo / "app.py").write_text("x = 1  # type: ignore\ny = 2  # noqa\n")
    (repo / "tests" / "test_app.py").write_text("def test_a(): pass\n")
    result = runner.run_check(cfg, cfg.check("shortcuts"))
    assert result.verdict == "fail"
    rules = {f["rule"] for f in result.outcome.findings}
    assert rules == {"noqa", "tests-removed"}


def test_allow_turns_a_rule_off(repo: Path) -> None:
    (repo / "rung.toml").write_text('[[check]]\nid = "s"\nkind = "shortcuts"\nallow = ["noqa"]\n')
    commit_all(repo, "base")
    (repo / "app.py").write_text("y = 2  # noqa\n")
    cfg = config.load(repo)
    assert runner.run_check(cfg, cfg.check("s")).verdict == "pass"


def test_words_in_strings_and_docstrings_are_not_shortcuts() -> None:
    files = {
        "src/doc.py": '"""\nNever add `# type: ignore`.\n"""\nmsg = "use # noqa sparingly"\nx = 1  # noqa\n',
        "src/doc.ts": "const s = `as any // @ts-ignore`;\nconst t = y as any;\n/* eslint-disable */\n",
        "tests/test_doc.py": 'NOTE = "@pytest.mark.skip is banned"\n',
    }
    found = sorted((f.path, f.line, f.rule) for f in shortcuts.find(files.items()))
    assert found == [("src/doc.py", 5, "noqa"), ("src/doc.ts", 2, "any-cast"), ("src/doc.ts", 3, "lint-disable")]


def test_codex_false_positives_are_gone() -> None:
    files = {
        "src/doc.ts": "const documentation = /as any/;\nconst ratio = a / b / c;\n",
        "src/lib.rs": 'fn main() { let quote = \'"\'; let s = "unsafe {"; }\n',
        "src/A.java": 'String s = """\n  @SuppressWarnings("unchecked")\n  """;\n',
        "pyproject.toml": "[tool.jinja2]\nstrict = false\n\n[tool.mypy]\nwarn_unused_ignores = true\n",
        "src/notes.py": "x = 1  # never add `# type: ignore` here\n",
    }
    assert shortcuts.find(files.items()) == []


def test_docstring_examples_are_not_tests() -> None:
    base = [("tests/test_docs.py", 'GUIDE = """\ndef test_example():\n    pass\n"""\n\ndef test_real():\n    pass\n')]
    head = [("tests/test_docs.py", "def test_real():\n    pass\n")]
    assert shortcuts.compare(head, base) == []


def test_codex_misses_are_caught() -> None:
    files = {
        "tests/a.test.ts": "test.only.each([1])('x %d', () => {})\ntest.skip.each([1])('y', () => {})\n"
        "test.skipIf(true)('z', () => {})\n",
        "src/a.py": "# pyright: reportGeneralTypeIssues=false\nx = 1  # pylint: disable=all\n",
        "tests/test_b.py": "from unittest import skip\n\n@skip('later')\ndef test_b():\n    pass\n",
        "tsconfig.json": '{"compilerOptions": {"noCheck": true}}\n',
        "main.go": "x := 1 //lint:ignore SA4006 unused\n",
        "src/lib.rs": "#[expect(clippy::unwrap_used)]\nfn f() {}\n",
        "src/integrationTest/java/ExampleTests.java": '@DisabledIfEnvironmentVariable(named = "CI", matches = "1")\n',
    }
    assert sorted(f.rule for f in shortcuts.find(files.items())) == sorted([
        "focused-test", "skipped-test", "skipped-test", "type-ignore", "lint-disable", "skipped-test",
        "relaxed-config", "nolint", "lint-allow", "skipped-test",
    ])  # fmt: skip


def test_removing_a_threshold_or_strict_mode_is_weakening() -> None:
    base = [("pyproject.toml", "[tool.coverage.report]\nfail_under = 90\n"), ("tsconfig.json", '{"strict": true}\n')]
    head = [("pyproject.toml", "[tool.coverage.report]\n"), ("tsconfig.json", "{}\n")]
    found = {f.rule: f.message for f in shortcuts.compare(head, base)}
    assert "removed (was 90)" in found["coverage-lowered"]
    assert "strict" in found["relaxed-config"]


def test_explaining_an_existing_suppression_does_not_make_it_new() -> None:
    before = shortcuts.find([("a.py", "x = f()  # type: ignore[no-untyped-call]\n")])
    after = shortcuts.find([("a.py", "x = f()  # type: ignore[no-untyped-call]  # upstream issue 123\n")])
    assert before[0].fingerprint == after[0].fingerprint


def test_a_quote_as_a_test_name_does_not_crash() -> None:
    assert shortcuts.compare([], [("a.test.ts", 'test("`", () => {});')])[0].rule == "tests-removed"


def test_shortcut_findings_never_store_secrets() -> None:
    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    (found,) = shortcuts.find([("a.py", f'TOKEN = "{token}"  # type: ignore\n')])
    assert token not in found.message


def test_a_baseline_does_not_switch_off_the_deleted_test_check(repo: Path) -> None:
    from rung.checks import findings

    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    cfg = _project(repo)
    commit_all(repo, "base")
    findings.save_baseline(repo, "shortcuts", [], "x")
    run_git(repo, "checkout", "-qb", "agent")
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    result = runner.run_check(cfg, cfg.check("shortcuts"))
    assert result.verdict == "fail" and {f["rule"] for f in result.outcome.findings} == {"tests-removed"}
