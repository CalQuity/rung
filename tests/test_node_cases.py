"""Node test cases are identified by their suite ancestry, so repeated names never collapse."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rung import cli
from rung.checks import prove

from .conftest import commit_all, run_git

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

NESTED = (
    "import { describe, test } from 'node:test';\n"
    "import assert from 'node:assert/strict';\n"
    "import { refund, charge } from './money.mjs';\n"
    "describe('refund', () => {\n"
    "  test('caps', () => assert.equal(refund(100, 150), 100));\n"
    "  describe('partial', () => { test('caps', () => assert.equal(refund(100, 120), 100)); });\n"
    "});\n"
    "describe('charge', () => { test('caps', () => assert.equal(charge(500), 100)); });\n"
    "for (const amount of [150, 200]) test('loop caps', () => assert.equal(refund(100, amount), 100));\n"
)
BUGGY = "export const refund = (c, r) => r;\nexport const charge = (a) => a;\n"
FIXED = "export const refund = (c, r) => Math.min(c, r);\nexport const charge = (a) => Math.min(a, 100);\n"


def test_a_passing_suite_with_repeated_names_counts_every_case(repo: Path, capsys) -> None:
    (repo / "money.mjs").write_text(FIXED)
    (repo / "money.test.mjs").write_text(NESTED)
    (repo / "rung.toml").write_text('[[check]]\nid = "unit"\nkind = "command"\nrun = "node --test money.test.mjs"\n')
    assert cli.main(["-C", str(repo), "check", "unit", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["verdict"] == "pass" and "5 passing test case(s)" in result["summary"]


def test_node_records_name_cases_by_their_suites(repo: Path, tmp_path: Path) -> None:
    (repo / "money.mjs").write_text(FIXED)
    (repo / "money.test.mjs").write_text(NESTED)
    report = tmp_path / "cases.jsonl"
    subprocess.run(["node", *prove.node_reporter_flags(report), "--test", "money.test.mjs"], cwd=repo, check=True,
                   capture_output=True)  # fmt: skip
    names = sorted(name for _, name, _ in prove.node_records(report, repo.resolve()))
    assert names == ["charge > caps", "loop caps", "loop caps", "refund > caps", "refund > partial > caps"]


def test_a_new_node_file_with_repeated_names_is_proven(repo: Path) -> None:
    (repo / "money.mjs").write_text(BUGGY)
    commit_all(repo, "buggy money")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.mjs").write_text(FIXED)
    (repo / "money.test.mjs").write_text(NESTED)
    result = prove.prove(repo, "node --test money.test.mjs", "main")
    assert result.verdict == "pass", (result.summary, result.cases)
    assert {c.test for c in result.cases} == {
        "money.test.mjs::refund > caps", "money.test.mjs::refund > partial > caps",
        "money.test.mjs::charge > caps", "money.test.mjs::loop caps",
    }  # fmt: skip
    assert all(c.proven for c in result.cases)
