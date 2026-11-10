"""Timing and test metrics must describe measured work, including saved-result limits."""

from __future__ import annotations

import json
import sys

import pytest

from rung import cli
from rung.checks import runner
from rung.core import ledger

from .test_score import configure, invoke


def timer(monkeypatch, *ticks):
    values = iter(ticks)
    monkeypatch.setattr(runner, "perf_counter", lambda: next(values), raising=False)


def rewrite_receipt(repo, value):
    path = ledger.verdict_path(repo, "unit")
    entry = json.loads(path.read_text())
    if value is None:
        entry["details"].pop("execution_seconds", None)
    else:
        entry["details"]["execution_seconds"] = value
    entry["hash"] = ledger._self_hash(entry)
    path.write_text(json.dumps(entry))


def test_regular_checks_record_timing_for_saved_html(repo, capsys, monkeypatch):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    timer(monkeypatch, 10, 12.5)
    assert cli.main(["-C", str(repo), "check", "unit"]) == 0
    receipt = ledger.verdict_path(repo, "unit").read_bytes()
    code, data, html = invoke(repo, capsys, "--no-run")
    assert code == 0
    timing = data["timing"]
    assert timing["mode"] == "recorded" and timing["wall_seconds"] is None
    assert timing["measured_checks"] == 1 and timing["total_check_seconds"] == 2.5
    assert timing["average_seconds"] == timing["median_seconds"] == 2.5
    assert data["checks"][0]["elapsed_seconds"] is None
    assert data["checks"][0]["recorded_elapsed_seconds"] == 2.5
    assert ledger.verdict_path(repo, "unit").read_bytes() == receipt
    assert "Recorded check timings" in html and "different runs" in html


def test_current_metrics_rank_checks_and_include_failed_execution_time(repo, capsys, monkeypatch):
    configure(
        repo,
        {
            "fast": f'{sys.executable} -c "pass"',
            "slow": f'{sys.executable} -c "raise SystemExit(1)"',
            "unavailable": "rung-metrics-missing-command",
        },
    )
    timer(monkeypatch, 1, 3, 10, 16, 20, 24)
    code, data, html = invoke(repo, capsys)
    assert code == 1
    timing = data["timing"]
    assert timing["mode"] == "current" and timing["wall_seconds"] >= 0
    assert timing["measured_checks"] == 3 and timing["unmeasured_checks"] == 0
    assert timing["total_check_seconds"] == 12
    assert timing["average_seconds"] == timing["median_seconds"] == 4
    assert timing["failed_check_seconds"] == 10
    assert [(r["id"], r["seconds"]) for r in timing["checks"]] == [("slow", 6), ("unavailable", 4), ("fast", 2)]
    assert timing["checks"][0]["share_percent"] == 50
    assert timing["slowest_check"] == {"id": "slow", "seconds": 6}
    assert "Execution timing" in html and "Slowest check" in html
    assert "6.00s" in html and "50.0%" in html


def test_stale_timing_is_kept_as_history_but_excluded_from_summary(repo, capsys, monkeypatch):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    timer(monkeypatch, 10, 13)
    invoke(repo, capsys)
    (repo / "app.py").write_text("price = 20\n")
    _, data, html = invoke(repo, capsys, "--no-run")
    assert data["timing"]["measured_checks"] == 0
    assert data["timing"]["unmeasured_checks"] == 1
    assert data["timing"]["total_check_seconds"] is None
    assert data["checks"][0]["recorded_elapsed_seconds"] == 3
    assert "Previous execution time" in html
    assert "No current timings available" in html


@pytest.mark.parametrize("value", [None, -1, float("inf"), float("nan"), True, "2.5"])
def test_legacy_or_invalid_timing_is_not_invented(repo, capsys, value):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    invoke(repo, capsys)
    rewrite_receipt(repo, value)
    _, data, html = invoke(repo, capsys, "--no-run")
    assert data["timing"]["measured_checks"] == 0
    assert data["timing"]["average_seconds"] is None
    assert data["checks"][0]["recorded_elapsed_seconds"] is None
    assert "No current timings available" in html


def test_zero_duration_is_measured_without_division_by_zero(repo, capsys, monkeypatch):
    configure(repo, {"unit": f'{sys.executable} -c "pass"'})
    timer(monkeypatch, 4, 4)
    _, data, _ = invoke(repo, capsys)
    assert data["timing"]["measured_checks"] == 1
    assert data["timing"]["total_check_seconds"] == 0
    assert data["timing"]["checks"][0]["share_percent"] == 0


def test_test_metrics_exclude_skips_from_pass_rate_and_include_errors(repo, capsys):
    configure(repo, {"tests": f"{sys.executable} -m pytest tests -q"})
    (repo / "tests").mkdir()
    (repo / "tests/test_demo.py").write_text(
        "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('fixture error')\n"
        "def test_error(broken): pass\ndef test_pass(): assert True\ndef test_fail(): assert False\n"
        "@pytest.mark.skip(reason='unsupported platform')\ndef test_skip(): pass\n"
    )
    _, data, html = invoke(repo, capsys)
    assert data["test_metrics"] == {
        "total_cases": 4,
        "executed_cases": 3,
        "pass_rate_percent": 33.3,
        "skip_rate_percent": 25.0,
        "reporting_suites": 1,
    }
    assert "33.3%" in html and "25.0%" in html
    assert "excluding skipped cases" in html and "overlapping suites" in html


def test_empty_project_has_no_timing_or_test_percentages(repo, capsys):
    configure(repo, {})
    _, data, html = invoke(repo, capsys)
    assert data["timing"]["total_check_seconds"] is None
    assert data["timing"]["slowest_check"] is None
    assert data["test_metrics"] is None
    assert "No current timings available" in html


def test_all_skipped_cases_do_not_become_a_hundred_percent_pass_rate(repo, capsys):
    configure(repo, {"tests": f"{sys.executable} -m pytest tests -q"})
    (repo / "tests").mkdir()
    (repo / "tests/test_demo.py").write_text(
        "import pytest\n@pytest.mark.skip(reason='unsupported platform')\ndef test_skip(): pass\n"
    )
    code, data, _ = invoke(repo, capsys)
    assert code == 1
    assert data["test_metrics"]["executed_cases"] == 0
    assert data["test_metrics"]["pass_rate_percent"] is None
    assert data["test_metrics"]["skip_rate_percent"] == 100


def test_attested_capture_does_not_claim_a_checker_was_executed(repo, capsys, monkeypatch):
    from rung.core import config

    configure(repo, {})
    cfg = config.load(repo)
    cfg.checks = [config.Check("ui", "evidence", ["app.py"], {"require": [".gif"]})]
    config.save(cfg)
    gif = repo / "shot.gif"
    gif.write_bytes(
        bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
    )
    assert runner.attest(cfg, cfg.check("ui"), [gif], by="human", note="local capture").verdict == "pass"
    timer(monkeypatch)
    code, data, _ = invoke(repo, capsys)
    assert code == 0
    assert data["timing"]["measured_checks"] == 0
    assert data["checks"][0]["elapsed_seconds"] is None
