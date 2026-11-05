"""How command checks read pytest and Cargo results when the command is wrapped or configured."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from rung import cli


def rung(root: Path, *args: str) -> int:
    return cli.main(["-C", str(root), *args])


def _check(root: Path, command: str) -> None:
    (root / "rung.toml").write_text(f'[[check]]\nid = "unit"\nkind = "command"\nrun = {json.dumps(command)}\n')


def _result(capsys) -> dict:
    return json.loads(capsys.readouterr().out)["results"][0]


def test_pytest_keeps_the_users_junit_report(repo: Path, capsys) -> None:
    (repo / ".gitignore").write_text("reports/\n")
    (repo / "reports").mkdir()
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    _check(repo, f"{sys.executable} -m pytest -q -p no:cacheprovider --junitxml=reports/junit.xml")
    assert rung(repo, "check", "unit", "--json") == 0
    assert "1 passing test case" in _result(capsys)["summary"]
    assert "test_ok" in (repo / "reports" / "junit.xml").read_text()


def test_a_leftover_junit_report_does_not_pass_a_run_that_writes_none(repo: Path, capsys) -> None:
    (repo / ".gitignore").write_text("reports/\n")
    (repo / "reports").mkdir()
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    # A passing report from an earlier run, and a command that exits 0 without running any test.
    (repo / "reports" / "junit.xml").write_text(
        '<testsuites><testsuite tests="1"><testcase classname="t" name="old"/></testsuite></testsuites>'
    )
    _check(repo, f"{sys.executable} -m pytest --version -p no:cacheprovider --junitxml=reports/junit.xml")
    assert rung(repo, "check", "unit", "--json") == 1
    assert _result(capsys)["verdict"] != "pass"


@pytest.fixture
def fake_cargo(tmp_path: Path, monkeypatch) -> Path:
    """A `cargo` on PATH that prints the summary in `summary.txt`, with Cargo's colors."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text(f"#!{sys.executable}\nimport pathlib\nprint(pathlib.Path('summary.txt').read_text())\n")
    cargo.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.mark.parametrize("command", ["cargo test", "env RUST_LOG=info cargo test", "time cargo test"])
def test_wrapped_cargo_test_requires_a_passing_case(fake_cargo: Path, capsys, command: str) -> None:
    _check(fake_cargo, command)
    (fake_cargo / "summary.txt").write_text("test result: \x1b[32mok\x1b[0m. 0 passed; 0 failed; 0 ignored\n")
    assert rung(fake_cargo, "check", "unit", "--json") == 1
    assert "Cargo reported no passing test cases" in _result(capsys)["summary"]
    (fake_cargo / "summary.txt").write_text("test result: \x1b[32mok\x1b[0m. 2 passed; 0 failed; 0 ignored\n")
    assert rung(fake_cargo, "check", "unit", "--json") == 0
    assert _result(capsys)["verdict"] == "pass"
