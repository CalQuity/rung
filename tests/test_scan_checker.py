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


def test_findings_in_ignored_files_are_not_project_findings(repo: Path) -> None:
    cfg = _setup(repo)
    (repo / ".gitignore").write_text("cache/\n")
    (repo / "cache").mkdir()
    (repo / "cache" / "compiled.py").write_text("TOKEN = 'TODO-SECRET in a build artifact'\n")
    base = git.merge_base(repo, "main")
    assert runner.run_check(cfg, cfg.check("secrets"), base=base).verdict == "pass"


# Recorded from osv-scanner 2.7.0 on a requirements.txt naming a package that is not on PyPI: without
# `--no-resolve` it exits 127 with an empty SARIF; with it, the manifest is scanned as written.
FAKE_OSV = r"""#!{python}
import pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--output-file") + 1])
out.write_text('{{"version": "2.1.0", "runs": [{{"tool": {{"driver": {{"name": "osv-scanner"}}}}, "results": []}}]}}')
if "--no-resolve" not in args and "stripe-checkout-retry-pro" in pathlib.Path("requirements.txt").read_text():
    sys.stderr.write("Error during extraction: (extracting as transitivedependency/requirements) failed "
                     "resolution for requirements.txt: package System(7):stripe-checkout-retry-pro: not found\n")
    sys.exit(127)
"""


def test_osv_preset_scans_a_manifest_that_names_an_unresolvable_package(repo: Path, monkeypatch) -> None:
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "osv-scanner"
    fake.write_text(FAKE_OSV.format(python=sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    (repo / "requirements.txt").write_text("stripe-checkout-retry-pro>=1.2\n")
    (repo / "rung.toml").write_text('[[check]]\nid = "vulnerable-deps"\nkind = "scan"\ntool = "osv-scanner"\n')
    cfg = config.load(repo)
    result = runner.run_check(cfg, cfg.check("vulnerable-deps"))
    assert result.verdict == "pass", result.outcome.summary  # the `deps` check is what flags the invented package


def test_partial_sarif_from_failed_scanner_is_an_error(repo: Path) -> None:
    cfg = _setup(repo)
    scanner = repo / "fake_scanner.py"
    scanner.write_text(FAKE_SCANNER + '\nsys.stderr.write("fatal error during scan\\n")\nsys.exit(2)\n')
    result = runner.run_check(cfg, cfg.check("secrets"), base=git.merge_base(repo, "main"))
    assert result.verdict == "error"
    assert "did not complete the scan (exit 2)" in result.outcome.summary
