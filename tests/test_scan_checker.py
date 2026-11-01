"""The scan checker end to end, with a fake scanner that reports one finding per `TODO-SECRET` line."""

from __future__ import annotations

import sys
from pathlib import Path

from rung.checks import findings, runner
from rung.checks.scan import ScanChecker
from rung.core import config, git

from .conftest import commit_all, run_git

FAKE_SCANNER = r"""
import json, pathlib, sys
results = []
for path in sorted(pathlib.Path(".").rglob("*.py")):
    if ".rung" in path.parts or path.name == "fake_scanner.py":
        continue
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if "TODO-SECRET" in line:
            results.append({"ruleId": "hardcoded-secret", "level": "error",
                            "message": {"text": "secret-like value"},
                            "locations": [{"physicalLocation": {"artifactLocation": {"uri": path.as_posix()},
                                           "region": {"startLine": n, "snippet": {"text": line.strip()}}}}]})
json.dump({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "fake"}}, "results": results}]}, sys.stdout)
"""


def _setup(repo: Path) -> config.Config:
    (repo / "fake_scanner.py").write_text(FAKE_SCANNER)
    (repo / "old.py").write_text("KEY = 'TODO-SECRET legacy'\n")
    (repo / "rung.toml").write_text(
        f'[project]\nname = "demo"\n\n[[check]]\nid = "secrets"\nkind = "scan"\n'
        f'run = "{sys.executable} fake_scanner.py"\nfail_on = "warning"\ncovers = ["**/*.py"]\n'
    )
    commit_all(repo, "add scanner and a legacy finding")
    run_git(repo, "checkout", "-qb", "feature")
    return config.load(repo)


def test_only_findings_introduced_by_the_branch_block(repo: Path) -> None:
    cfg = _setup(repo)
    base = git.merge_base(repo, "main")
    assert runner.run_check(cfg, cfg.check("secrets"), base=base).verdict == "pass"

    (repo / "new.py").write_text("TOKEN = 'TODO-SECRET added by an agent'\n")
    result = runner.run_check(cfg, cfg.check("secrets"), base=base)
    assert result.verdict == "fail"
    new = [f for f in result.outcome.findings if f["status"] == "new"]
    assert [f["path"] for f in new] == ["new.py"]


def test_without_a_base_every_finding_counts_until_baselined(repo: Path) -> None:
    cfg = _setup(repo)
    check = cfg.check("secrets")
    assert runner.run_check(cfg, check).verdict == "fail"

    existing = ScanChecker().collect(repo, check, timeout=60)
    findings.save_baseline(repo, "secrets", existing, git.head(repo))
    assert runner.run_check(cfg, check).verdict == "pass"


def test_a_waiver_unblocks_one_finding(repo: Path) -> None:
    cfg = _setup(repo)
    check = cfg.check("secrets")
    (repo / "new.py").write_text("TOKEN = 'TODO-SECRET test fixture'\n")
    base = git.merge_base(repo, "main")
    fingerprint = next(f["fingerprint"] for f in runner.run_check(cfg, check, base=base).outcome.findings)
    from datetime import date

    findings.add_waiver(repo, findings.Waiver("secrets", fingerprint, "test fixture", "Dev", date(2099, 1, 1)))
    assert runner.run_check(cfg, check, base=base).verdict == "pass"


def test_scanner_without_sarif_is_an_error_not_a_pass(repo: Path) -> None:
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "broken"\nkind = "scan"\nrun = "{sys.executable} -c \\"print(1)\\""\n'
    )
    cfg = config.load(repo)
    result = runner.run_check(cfg, cfg.check("broken"))
    assert result.verdict == "error"
    assert "without a valid SARIF result" in result.outcome.summary


def test_missing_scanner_names_how_to_install(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    (repo / "rung.toml").write_text('[[check]]\nid = "secrets"\nkind = "scan"\ntool = "gitleaks"\n')
    cfg = config.load(repo)
    result = runner.run_check(cfg, cfg.check("secrets"))
    assert result.verdict == "error" and "brew install gitleaks" in result.outcome.summary
