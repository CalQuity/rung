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
