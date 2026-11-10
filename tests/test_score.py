"""Public score command: real processes, honest freshness and safe offline output."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from rung import cli
from rung.core import config, ledger

from .conftest import commit_all


def configure(root: Path, commands: dict[str, str]) -> None:
    (root / "app.py").write_text("price = 10\n")
    config.save(
        config.Config(
            root,
            "Demo <shop>",
            config.LLMConfig(),
            [config.Check(name, "command", ["app.py"], {"run": command}) for name, command in commands.items()],
        )
    )


def invoke(root: Path, capsys, *args: str) -> tuple[int, dict, str]:
    capsys.readouterr()
    code = cli.main(["-C", str(root), "score-my-code", *args, "--json"])
    data = json.loads(capsys.readouterr().out)
    document = Path(data["report_path"]).read_text() if "report_path" in data else ""
    return code, data, document


def test_score_runs_all_checks_and_reports_failures_without_hiding_errors(repo, capsys):
    configure(
        repo,
        {
            "unit": f'{sys.executable} -c "pass"',
            "lint": f'{sys.executable} -c "raise SystemExit(1)"',
            "unavailable": "rung-score-missing-executable",
        },
    )
    code, data, document = invoke(repo, capsys)
    assert code == 1
    assert data["score"]["value"] == 33
    assert data["score"]["fresh_passing"] == 1 and data["score"]["configured"] == 3
    assert data["stats"]["failed"] == 1 and data["stats"]["errors"] == 1
    assert data["ran_checks"] and data["elapsed_seconds"] >= 0
    assert len(ledger.read_all(repo)) == 3
    assert "configured-check health" in document
    assert "not an overall code-quality score" in document
    assert "Demo &lt;shop&gt;" in document and "Demo <shop>" not in document
    assert "Not assessed" in document and "No check configured" in document
    assert "rung check lint" in document and "rung doctor --strict" in document
    assert all(row["elapsed_seconds"] >= 0 for row in data["checks"])


def test_no_run_reports_never_and_stale_without_executing(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    code, data, _ = invoke(repo, capsys, "--no-run")
    assert code == 1 and data["score"]["value"] == 0
    assert data["stats"]["never"] == 1 and not ledger.read_all(repo)
    assert invoke(repo, capsys)[0] == 0
    receipt = ledger.verdict_path(repo, "unit").read_bytes()
    (repo / "app.py").write_text("price = 20\n")
    code, data, document = invoke(repo, capsys, "--no-run")
    assert code == 1 and data["stats"]["stale"] == 1
    assert not data["ran_checks"] and data["elapsed_seconds"] is None
    assert data["checks"][0]["elapsed_seconds"] is None
    assert ledger.verdict_path(repo, "unit").read_bytes() == receipt
    assert "changed" in document and "Previous result" in document


def test_html_is_escaped_and_secret_output_is_redacted(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    invoke(repo, capsys)
    receipt = ledger.read_all(repo)["unit"]
    receipt["summary"] = '<script>alert("unsafe")</script> token=ghp_' + "a" * 36
    receipt["details"]["lines"] = ['<img src=x onerror="alert(1)">']
    receipt["hash"] = ledger._self_hash(receipt)
    ledger.verdict_path(repo, "unit").write_text(json.dumps(receipt))
    _, data, document = invoke(repo, capsys, "--no-run")
    assert "<script>" not in document and "<img src=x" not in document
    assert "&lt;script&gt;" in document
    assert "ghp_" + "a" * 36 not in document + json.dumps(data)
    assert "Content-Security-Policy" in document


@pytest.mark.parametrize("target", ["app.py", "rung.toml", ".rung/verdicts/unit.json", "report.html"])
def test_output_cannot_overwrite_project_inputs_before_running(repo, capsys, target):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    before = (repo / "app.py").read_bytes()
    code, data, _ = invoke(repo, capsys, "--out", str(repo / target))
    assert code == 2 and "error" in data
    assert (repo / "app.py").read_bytes() == before
    assert not ledger.read_all(repo)


def test_report_can_be_exported_outside_project_and_does_not_stale_checks(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    output = repo.parent / "export" / "report.html"
    code, data, document = invoke(repo, capsys, "--out", str(output))
    assert code == 0 and data["score"]["value"] == 100 and document
    assert data["report_path"] == str(output)
    assert invoke(repo, capsys, "--no-run", "--out", str(output))[0] == 0
    assert cli.main(["-C", str(repo), "status", "--require-fresh"]) == 0


def test_output_symlink_and_unrelated_existing_file_are_rejected(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    sentinel = repo.parent / "sentinel.html"
    sentinel.write_text("original")
    assert invoke(repo, capsys, "--out", str(sentinel))[0] == 2
    output = repo.parent / "link.html"
    output.symlink_to(sentinel)
    assert invoke(repo, capsys, "--out", str(output))[0] == 2
    assert sentinel.read_text() == "original" and not ledger.read_all(repo)


def test_empty_configuration_has_no_score(repo, capsys):
    configure(repo, {})
    code, data, document = invoke(repo, capsys)
    assert code == 1 and data["score"]["value"] is None
    assert "Not scored" in document and "No checks configured" in document


def test_corrupt_verdict_is_not_counted_as_passing(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    invoke(repo, capsys)
    ledger.verdict_path(repo, "unit").write_text("corrupt")
    code, data, document = invoke(repo, capsys, "--no-run")
    assert code == 1 and data["score"]["value"] == 0
    assert not data["ledger_intact"] and "corrupt" in document


def test_base_context_is_required_and_check_definition_changes_alter_fingerprint(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    (repo / "LAWS.md").write_text("## bounded\nTotals are bounded.\n- enforced by: unit\n- status: approved\n")
    commit_all(repo, "configured check")
    assert invoke(repo, capsys)[0] == 0
    code, data, _ = invoke(repo, capsys, "--no-run", "--base", "main")
    assert code == 1 and data["stats"]["stale"] == 1
    assert data["laws"][0]["state"] != "held" and data["summary"]["laws_held"] == 0
    code, data, _ = invoke(repo, capsys, "--base", "main")
    assert code == 0 and data["comparison_base"]
    digest = data["check_set_digest"]
    assert invoke(repo, capsys, "--no-run", "--base", "main")[1]["check_set_digest"] == digest
    cfg = config.load(repo)
    cfg.checks[0].options["run"] = f'{sys.executable} -c "print(1)"'
    config.save(cfg)
    assert invoke(repo, capsys, "--no-run")[1]["check_set_digest"] != digest


def test_new_scanner_findings_are_labeled_historical_when_inputs_change(repo, capsys):
    from .test_scan_checker import _setup

    _setup(repo)
    code, data, document = invoke(repo, capsys)
    assert code == 1 and data["new_findings"][0]["current"]
    assert "secret-like value" in document and "old.py:1" in document
    (repo / "old.py").write_text("KEY = 'fixed'\n")
    _, data, document = invoke(repo, capsys, "--no-run")
    assert not data["new_findings"][0]["current"]
    assert "Previous result; rerun required" in document


def test_runtime_failures_show_expected_observed_and_replay_command(repo, capsys):
    from .test_runtime_evidence import SCRIPT

    (repo / "drive.py").write_text(SCRIPT)
    (repo / "bad_seeds.txt").write_text("101")
    config.save(
        config.Config(
            repo,
            "shop",
            config.LLMConfig(),
            [
                config.Check(
                    "runtime",
                    "evidence",
                    ["drive.py", "bad_seeds.txt"],
                    {"run": f"{sys.executable} drive.py", "seed": 101},
                )
            ],
        )
    )
    code, data, document = invoke(repo, capsys)
    assert code == 1 and data["stats"]["failed"] == 1
    assert "reload" in document and "rung check runtime --seed 101" in document
    assert "$32.00" in document and "$27.00" in document


def test_destination_is_revalidated_after_checks_execute(repo, capsys):
    output = repo.parent / "result.html"
    script = f"from pathlib import Path; Path({str(output)!r}).write_text('user content')"
    configure(repo, {"custom": f"{sys.executable} -c {shlex.quote(script)}"})
    code, _, _ = invoke(repo, capsys, "--out", str(output))
    assert code == 2 and output.read_text() == "user content"


def test_plain_terminal_output_points_to_usable_html(repo, capsys):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    assert cli.main(["-C", str(repo), "score-my-code"]) == 0
    output = capsys.readouterr().out
    assert "100/100" in output and "HTML report:" in output
    assert (repo / ".rung/cache/score/index.html").is_file()


def test_report_leads_with_scope_and_discloses_missing_assessments(repo, capsys):
    configure(repo, {"tests": f'{sys.executable} -c "pass"'})
    _, data, document = invoke(repo, capsys)
    assert "1 of 1 configured checks passed" in document
    assert document.index("1 of 1 configured checks passed") < document.index("Verification health")
    assert "calquity-logo" in document and "data:image/png;base64," in document
    assert "#3A5C78" in document and "img-src data:" in document
    assert data["assessments"]["test_strength"] == "not_assessed"
    assert "Vulnerability scanning" in document and "Secret scanning" in document
    assert "No browser or runtime verification configured" in document


def test_shortcut_findings_are_review_requests_not_confirmed_bugs(repo, capsys):
    from .test_scan_checker import _setup

    cfg = _setup(repo)
    cfg.checks = [config.Check("shortcuts", "shortcuts", ["old.py"], {})]
    config.save(cfg)
    (repo / "old.py").write_text("import os  # noqa: F401\n")
    _, data, document = invoke(repo, capsys)
    assert data["checks"][0]["assessment"] == "review_required"
    assert "Review required" in document and "Confirmed application bug" not in document
    assert "lint rule" in document and "intentional" in document


def test_pytest_counts_include_failures_and_skips_and_remain_historical_when_stale(repo, capsys):
    configure(repo, {"tests": f"{sys.executable} -m pytest tests -q"})
    (repo / "tests").mkdir()
    (repo / "tests/test_demo.py").write_text(
        "import pytest\ndef test_pass(): assert True\n"
        "def test_fail(): assert False\n@pytest.mark.skip(reason='example skip')\ndef test_skip(): pass\n"
    )
    code, data, document = invoke(repo, capsys)
    assert code == 1
    assert data["test_results"] == {"passed": 1, "failed": 1, "errors": 0, "skipped": 1, "suites": 1}
    assert "1 passed" in document and "1 skipped" in document
    (repo / "app.py").write_text("price = 5\n")
    _, data, document = invoke(repo, capsys, "--no-run")
    assert data["test_results"] is None
    assert "Previous test results" in document


def test_shortcut_only_is_not_presented_as_a_lint_or_type_check(repo, capsys):
    configure(repo, {})
    cfg = config.load(repo)
    cfg.checks.append(config.Check("shortcuts", "shortcuts", [], {}))
    config.save(cfg)
    _, data, document = invoke(repo, capsys)
    assert data["assessments"]["static_analysis"] == "not_configured"
    assert "No linter or type checker configured" in document


def test_static_assessment_uses_tools_instead_of_custom_check_names(repo, capsys):
    configure(repo, {"health-check": f'{sys.executable} -c "print(1)"'})
    _, data, _ = invoke(repo, capsys)
    assert data["assessments"]["static_analysis"] == "not_configured"
    cfg = config.load(repo)
    cfg.checks[0].options["run"] = "python -m ruff check ."
    config.save(cfg)
    _, data, _ = invoke(repo, capsys, "--no-run")
    assert data["assessments"]["static_analysis"] == "configured"


def test_trivy_preset_declares_both_secret_and_vulnerability_scanning(repo, capsys):
    configure(repo, {})
    cfg = config.load(repo)
    cfg.checks.append(config.Check("scan", "scan", [], {"tool": "trivy"}))
    config.save(cfg)
    _, data, _ = invoke(repo, capsys, "--no-run")
    assert data["assessments"]["secret_scanning"] == "configured"
    assert data["assessments"]["vulnerability_scanning"] == "configured"
