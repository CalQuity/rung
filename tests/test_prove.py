from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rung.checks import prove
from rung.core import ledger

from .conftest import commit_all, run_git

BUGGY = "def refund(charged, requested):\n    return requested\n"
FIXED = "def refund(charged, requested):\n    return min(charged, requested)\n"
TEST = (
    "from money import refund\n\n"
    "def test_refund_never_exceeds_charge():\n"
    "    assert refund(100, 150) == 100, 'refund exceeded charge'\n"
)
WEAK_TEST = "from money import refund\n\ndef test_refund():\n    assert refund(100, 50) == 50\n"
PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"


def _branch(repo: Path, source: str, test: str) -> None:
    (repo / "money.py").write_text(BUGGY)
    commit_all(repo, "buggy refund")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.py").write_text(source)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_money.py").write_text(test)


def test_a_real_regression_test_is_proven(repo: Path) -> None:
    _branch(repo, FIXED, TEST)
    result = prove.prove(repo, PYTEST, "main", expect="refund exceeded charge")
    assert result.verdict == "pass", (result.summary, result.base, result.head)
    assert result.tests == ["tests/test_money.py"]
    entry = ledger.history(repo)[-1]
    assert entry["kind"] == "proof" and entry["verdict"] == "pass"
    assert "money.py" in entry["details"]["files"]


def test_a_test_that_passes_without_the_fix_proves_nothing(repo: Path) -> None:
    _branch(repo, FIXED, WEAK_TEST)
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "fail"
    assert "do not catch the bug" in result.summary


def test_a_fix_that_does_not_work_fails(repo: Path) -> None:
    _branch(repo, BUGGY, TEST)
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "fail" and "fail on this branch too" in result.summary


def test_expect_rejects_a_base_failure_for_the_wrong_reason(repo: Path) -> None:
    _branch(repo, FIXED, TEST)
    result = prove.prove(repo, PYTEST, "main", expect="some other assertion")
    assert result.verdict == "fail" and "another reason" in result.summary


def test_no_changed_tests_is_an_error(repo: Path) -> None:
    run_git(repo, "checkout", "-qb", "empty")
    assert prove.prove(repo, PYTEST, "main").verdict == "error"


def test_b11_a_base_failure_while_importing_is_not_a_proof(repo: Path) -> None:
    (repo / "money.py").write_text(BUGGY)
    commit_all(repo, "buggy refund")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "helpers.py").write_text("LIMIT = 100\n")  # new module the test imports
    (repo / "tests").mkdir()
    (repo / "tests" / "test_money.py").write_text("from helpers import LIMIT\n\ndef test_limit():\n    assert LIMIT\n")
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "fail" and "while loading" in result.summary


def test_b11_a_proof_goes_stale_in_the_report_when_its_files_change(repo: Path) -> None:
    from rung.analysis import report
    from rung.core import config

    _branch(repo, FIXED, TEST)
    (repo / "rung.toml").write_text('[project]\nname = "p"\n')
    assert prove.prove(repo, PYTEST, "main", expect="refund exceeded charge").verdict == "pass"
    assert report.build(config.load(repo))["proofs"][0]["verdict"] == "pass"
    (repo / "money.py").write_text(BUGGY)
    assert report.build(config.load(repo))["proofs"][0]["verdict"] == "stale"


# --- per test, for pytest -----------------------------------------------------------------------

TWO_TESTS = TEST + "\n\ndef test_refund_of_a_small_amount():\n    assert refund(100, 50) == 50\n"
OLD_TEST = "from money import refund\n\n\ndef test_refund_is_a_number():\n    assert refund(1, 1) == 1\n"


def test_a_new_test_that_passes_on_the_base_is_named_even_when_another_fails_there(repo: Path) -> None:
    _branch(repo, FIXED, TWO_TESTS)
    result = prove.prove(repo, PYTEST, "main", expect="refund exceeded charge")
    assert result.verdict == "fail"
    assert "prove nothing" in result.summary
    assert "tests/test_money.py::test_refund_of_a_small_amount" in result.summary
    assert "test_refund_never_exceeds_charge" not in result.summary
    by_name = {c.test.split("::")[-1]: (c.base, c.head, c.proven) for c in result.cases}
    assert by_name == {
        "test_refund_never_exceeds_charge": ("failed", "passed", True),
        "test_refund_of_a_small_amount": ("passed", "passed", False),
    }
    assert ledger.history(repo)[-1]["details"]["cases"][1]["base"] == "passed"


def test_only_new_or_changed_tests_are_judged_and_the_proof_names_them(repo: Path) -> None:
    (repo / "money.py").write_text(BUGGY)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_money.py").write_text(OLD_TEST)
    commit_all(repo, "buggy refund with an old test")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.py").write_text(FIXED)
    new = "\n\ndef test_refund_never_exceeds_charge():\n    assert refund(100, 150) == 100, 'refund exceeded charge'\n"
    (repo / "tests" / "test_money.py").write_text(OLD_TEST + new)
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "pass", result.summary
    assert [c.test for c in result.cases] == ["tests/test_money.py::test_refund_never_exceeds_charge"]
    assert "1 new or changed test(s) fail on merge base" in result.summary
    assert "tests/test_money.py::test_refund_never_exceeds_charge" in result.summary


def test_class_and_parametrized_tests_are_matched_to_their_outcomes(repo: Path) -> None:
    test = (
        "import pytest\nfrom money import refund\n\n\nclass TestRefund:\n"
        "    @pytest.mark.parametrize('asked', [150, 200])\n"
        "    def test_capped(self, asked):\n        assert refund(100, asked) == 100\n\n"
        "    @pytest.mark.parametrize('asked', [10, 500])\n"
        "    def test_mixed(self, asked):\n        assert refund(100, asked) == min(100, asked)\n"
    )
    _branch(repo, FIXED, test)
    result = prove.prove(repo, PYTEST, "main")
    cases = {c.test: (c.base, c.head) for c in result.cases}
    assert cases == {
        "tests/test_money.py::TestRefund::test_capped": ("failed", "passed"),
        "tests/test_money.py::TestRefund::test_mixed": ("failed", "passed"),  # one parameter fails on the base
    }
    assert result.verdict == "pass", result.summary


def test_other_runners_are_judged_by_the_whole_run(repo: Path) -> None:
    (repo / "run_tests.py").write_text(
        "import sys\nimport pytest\nsys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests']))\n"
    )
    _branch(repo, FIXED, TWO_TESTS)
    result = prove.prove(repo, f"{sys.executable} run_tests.py", "main", expect="refund exceeded charge")
    assert result.cases == [] and result.verdict == "pass"  # the file-level rule, as before


def test_pytest_commands_are_recognised() -> None:
    assert prove.runs_pytest(["uv", "run", "pytest", "-q"])
    assert prove.runs_pytest(["/venv/bin/python", "-m", "pytest"])
    assert prove.runs_pytest(["/venv/bin/python", "-Bm", "pytest"])
    assert prove.runs_pytest(["env", "NAME=value", "python", "-X", "utf8", "-mpytest"])
    assert prove.runs_pytest([".venv/bin/pytest"])
    assert not prove.runs_pytest(["npx", "vitest", "run"])
    assert not prove.runs_pytest(["python", "-m", "unittest"])


def test_proof_uses_exact_workspace_name_at_base(repo: Path) -> None:
    for name, implementation in ((" lead", FIXED), ("lead", BUGGY)):
        (repo / name).mkdir()
        (repo / name / "money.py").write_text(implementation)
    commit_all(repo, "different sibling implementations")
    run_git(repo, "switch", "-qc", "fix")
    workspace = repo / " lead"
    (workspace / "test_money.py").write_text(TEST)
    result = prove.prove(workspace, f"{sys.executable} -m pytest -q", "main")
    assert result.verdict == "fail" and "pass without the fix" in result.summary


@pytest.mark.parametrize("existing", [False, True])
def test_node_proof_distinguishes_a_real_regression_from_a_vacuous_case(repo, existing):
    app = repo / "app.cjs"
    app.write_text("module.exports = () => 1;\n")
    test = repo / "app.test.cjs"
    fixture = (
        "const { test } = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "const increment = require('./app.cjs');\n"
    )
    weak = "test('already passes', () => assert.equal(1, 1));\n"
    if existing:
        test.write_text(fixture + weak)
    commit_all(repo, "buggy baseline")
    run_git(repo, "checkout", "-qb", "fix")
    app.write_text("module.exports = () => 2;\n")
    test.write_text(fixture + weak + "test('catches bug', () => assert.equal(increment(), 2));\n")
    cmd = "node --test app.test.cjs"
    result = prove.prove(repo, cmd, "main")
    if existing:
        assert result.verdict == "error" and "--case" in result.summary
        result = prove.prove(repo, cmd, "main", node_cases=["app.test.cjs::catches bug"])
        assert result.verdict == "pass", result.summary
    else:
        assert result.verdict == "fail" and "already passes" in result.summary
        assert result.cases[0].base == "passed"
    regression = next(case for case in result.cases if case.test == "app.test.cjs::catches bug")
    assert regression.proven


# --- --expect, files written by the run, and what a proof covers ---------------------------------


def test_expect_accepts_a_base_that_cannot_import_the_fix(repo: Path) -> None:
    (repo / "money.py").write_text(BUGGY)
    commit_all(repo, "buggy refund")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.py").write_text(FIXED + "\n\ndef cap(charged, requested):\n    return min(charged, requested)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_money.py").write_text(
        "from money import cap\n\ndef test_cap():\n    assert cap(100, 150) == 100\n"
    )
    without = prove.prove(repo, PYTEST, "main")
    assert without.verdict == "fail" and "--expect" in without.summary and "missing name" in without.summary
    result = prove.prove(repo, PYTEST, "main", expect="cannot import name 'cap'")
    assert result.verdict == "pass", (result.summary, result.base)
    assert "before reaching 1 test(s)" in result.summary and result.cases[0].head == "passed"
    assert prove.prove(repo, PYTEST, "main", expect="no such text").verdict == "fail"


def test_expect_matches_a_go_style_comparison_on_stdout(repo: Path) -> None:
    (repo / "money.py").write_text(BUGGY)
    commit_all(repo, "buggy refund")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.py").write_text(FIXED)
    (repo / "refund_test.py").write_text(
        "import sys\nfrom money import refund\n"
        "got = refund(100, 150)\n"
        "if got != 100:\n"
        "    print(f'refund_test.go:12: refund(100, 150) = {got}, want 100')\n"
        "    sys.exit(1)\n"
        "print('ok')\n"
    )
    result = prove.prove(repo, f"{sys.executable} refund_test.py", "main", expect="want 100")
    assert result.verdict == "pass", (result.summary, result.base)


def test_reports_the_tests_write_do_not_void_the_proof(repo: Path) -> None:
    (repo / "fixture.json").write_text("{}\n")
    writer = TEST.replace(
        "def test_refund_never_exceeds_charge():\n",
        "def test_refund_never_exceeds_charge():\n    open('coverage.xml', 'w').write('<coverage/>')\n",
    )
    _branch(repo, FIXED, writer)
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "pass", result.summary
    assert (repo / "coverage.xml").is_file()
    editor = writer.replace("open('coverage.xml', 'w')", "open('fixture.json', 'a')")
    (repo / "tests" / "test_money.py").write_text(editor)
    result = prove.prove(repo, PYTEST, "main")
    assert result.verdict == "error" and "fixture.json" in result.summary


def test_documentation_edits_do_not_stale_a_proof(repo: Path) -> None:
    from rung.analysis import report
    from rung.core import config

    (repo / "docs").mkdir()
    (repo / "docs" / "guide.txt").write_text("how refunds work\n")
    (repo / "fixtures").mkdir()
    (repo / "fixtures" / "cases.json").write_text("[150]\n")
    commit_all(repo, "docs and fixtures")
    _branch(repo, FIXED, TEST)
    (repo / "rung.toml").write_text('[project]\nname = "p"\n')
    assert prove.prove(repo, PYTEST, "main").verdict == "pass"

    def verdict() -> str:
        return report.build(config.load(repo))["proofs"][0]["verdict"]

    (repo / "README.md").write_text("demo, now with refunds\n")
    (repo / "docs" / "guide.txt").write_text("how refunds work, revised\n")
    assert verdict() == "pass"
    (repo / "fixtures" / "cases.json").write_text("[150, 200]\n")
    assert verdict() == "stale"


def test_a_file_added_after_the_proof_stales_it(repo: Path) -> None:
    from rung.analysis import report
    from rung.core import config

    _branch(repo, FIXED, TEST)
    (repo / "rung.toml").write_text('[project]\nname = "p"\n')
    assert prove.prove(repo, PYTEST, "main").verdict == "pass"

    def verdict() -> str:
        return report.build(config.load(repo))["proofs"][0]["verdict"]

    (repo / "notes.md").write_text("a new document\n")
    assert verdict() == "pass"
    (repo / "plugins").mkdir()
    (repo / "plugins" / "discount.toml").write_text("rate = 2\n")  # a new directory and file type
    assert verdict() == "stale"
