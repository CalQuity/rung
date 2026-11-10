"""Public diagnostic commands are explicit and preserve advisory/strict exits."""

from __future__ import annotations

import json

import pytest

from rung import cli


def test_flaky_command_defaults_and_explicit_policy(project, capsys, monkeypatch):
    from rung.analysis import flaky

    calls = []

    def diagnose(cfg, check, **kwargs):
        calls.append((check.id, kwargs))
        return {
            "exit_code": int(kwargs["fail_on_flaky"]),
            "state": "complete",
            "summary": "one intermittent case",
            "metrics": {"flaky_cases": 1},
            "manifest_path": ".rung/evidence/diagnostics/run.json",
        }

    monkeypatch.setattr(flaky, "run", diagnose)
    assert cli.main(["-C", str(project), "flaky", "unit", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["flaky_cases"] == 1
    assert calls[-1] == ("unit", {"attempts": 5, "budget_seconds": 600, "fail_on_flaky": False})
    assert (
        cli.main(
            ["-C", str(project), "flaky", "unit", "--attempts", "3", "--budget", "60", "--fail-on-flaky", "--json"]
        )
        == 1
    )
    capsys.readouterr()
    assert calls[-1][1] == {"attempts": 3, "budget_seconds": 60, "fail_on_flaky": True}
    assert not (project / ".rung/verdicts").exists()


@pytest.mark.parametrize("options", [["--attempts", "1"], ["--budget", "0"], ["--budget", "nan"]])
def test_flaky_rejects_invalid_limits_without_running(project, capsys, monkeypatch, options):
    from rung.analysis import flaky

    def unexpected(*args, **kwargs):
        raise AssertionError("invalid configuration must not run repository commands")

    monkeypatch.setattr(flaky, "run", unexpected)
    assert cli.main(["-C", str(project), "flaky", "unit", *options, "--json"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_coverage_requires_source_and_validates_threshold_before_running(project, capsys, monkeypatch):
    from rung.analysis import coverage

    def unexpected(*args, **kwargs):
        raise AssertionError("invalid configuration must not run repository commands")

    monkeypatch.setattr(coverage, "run", unexpected)
    for options in ([], ["--source", "src", "--fail-under", "101"], ["--source", "src", "--timeout", "inf"]):
        assert cli.main(["-C", str(project), "coverage", "unit", *options, "--json"]) == 2
        assert "error" in json.loads(capsys.readouterr().out)


def test_coverage_command_resolves_base_and_keeps_measurement_separate(repo, capsys, monkeypatch):
    from rung.analysis import coverage

    (repo / "rung.toml").write_text('[[check]]\nid="unit"\nkind="command"\nrun="python -m pytest"\n')
    calls = []

    def diagnose(cfg, check, **kwargs):
        calls.append(kwargs)
        return {
            "exit_code": 0,
            "state": "complete",
            "summary": "1 of 2 changed lines exercised",
            "metrics": {"covered": 1, "total": 2, "percent": 50.0},
        }

    monkeypatch.setattr(coverage, "run", diagnose)
    assert cli.main(["-C", str(repo), "coverage", "unit", "--source", "src", "--base", "HEAD", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["percent"] == 50.0
    assert calls[-1]["sources"] == ["src"] and calls[-1]["base"] == "HEAD"
    assert calls[-1]["fail_under"] is None
