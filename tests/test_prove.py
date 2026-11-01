from __future__ import annotations

import sys
from pathlib import Path

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
    assert prove.runs_pytest([".venv/bin/pytest"])
    assert not prove.runs_pytest(["npx", "vitest", "run"])
    assert not prove.runs_pytest(["python", "-m", "unittest"])
