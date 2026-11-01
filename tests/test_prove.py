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
    entry = ledger.read(repo)[-1]
    assert entry["kind"] == "proof" and entry["verdict"] == "pass"
    assert "money.py" in entry["covers"]


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
