"""Diagnostic evidence stays separate from verdicts and expires with its inputs."""

from __future__ import annotations

import json

import pytest

from rung.analysis import diagnostics
from rung.core import config

from .conftest import commit_all, run_git


def _session(project):
    cfg = config.load(project)
    return cfg, diagnostics.Session(cfg, cfg.check("unit"), "flaky", {"attempts": 5})


def test_diagnostic_artifacts_are_redacted_bound_and_do_not_write_verdicts(project):
    cfg, session = _session(project)
    session.path("output.txt").write_text("password=example-password\n")
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed", "metrics": {}})
    assert result["current"] is True
    assert "example-password" not in session.path("output.txt").read_text()
    assert not (project / ".rung/verdicts").exists()
    saved = diagnostics.saved(cfg)
    assert len(saved) == 1 and saved[0]["current"] is True
    session.path("output.txt").write_text("edited evidence")
    assert diagnostics.saved(cfg)[0]["current"] is False


def test_changed_test_or_check_makes_diagnostics_historical(project):
    test = project / "test_sample.py"
    test.write_text("value = 1\n")
    cfg, session = _session(project)
    session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    test.write_text("value = 2\n")
    assert diagnostics.saved(cfg)[0]["current"] is False
    test.write_text("value = 1\n")
    cfg.check("unit").options["run"] = "python -m pytest"
    assert diagnostics.saved(cfg)[0]["current"] is False


def test_inputs_changing_during_diagnostic_produce_incomplete_evidence(project):
    cfg, session = _session(project)
    (project / "src/core.py").write_text("value = 2\n")
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    assert result["state"] == "incomplete" and result["exit_code"] == 2
    assert result["current"] is False
    assert diagnostics.saved(cfg)[0]["current"] is False


@pytest.mark.parametrize("name", ["rung.toml", "LAWS.md"])
def test_configuration_changing_during_diagnostic_is_immediately_incomplete(project, name):
    cfg, session = _session(project)
    with (project / name).open("a") as stream:
        stream.write("\n# changed during execution\n")
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    assert result["current"] is False and result["exit_code"] == 2
    assert diagnostics.saved(cfg)[0]["current"] is False


def test_corrupt_manifest_is_visible_as_unusable(project):
    cfg, session = _session(project)
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    manifest = project / result["manifest_path"]
    data = json.loads(manifest.read_text())
    data["summary"] = "forged summary"
    manifest.write_text(json.dumps(data))
    loaded = diagnostics.saved(cfg)
    assert loaded and loaded[0]["current"] is False
    assert "unusable" in loaded[0]["summary"].lower()


def test_diagnostic_paths_reject_links_and_escape(project):
    cfg, session = _session(project)
    with pytest.raises(config.ConfigError):
        session.path("../escape")
    outside = project.with_name(project.name + "-outside")
    outside.mkdir()
    session.path("link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(config.ConfigError):
        session.path("link/output.txt")
    (session.directory / "link").unlink()
    (project / ".rung/evidence/diagnostics/flaky").rename(project / ".rung/evidence/old-flaky")
    (project / ".rung/evidence/diagnostics/flaky").symlink_to(outside, target_is_directory=True)
    with pytest.raises(config.ConfigError):
        diagnostics.Session(cfg, cfg.check("unit"), "flaky", {})


def test_latest_result_uses_recorded_time_not_random_directory_order(project, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(diagnostics.uuid, "uuid4", lambda: SimpleNamespace(hex="z" * 32))
    cfg, older = _session(project)
    older.finish({"state": "complete", "exit_code": 0, "summary": "older"})
    monkeypatch.setattr(diagnostics.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    _, newer = _session(project)
    newer.finish({"state": "complete", "exit_code": 0, "summary": "newer"})
    assert diagnostics.saved(cfg)[0]["summary"] == "newer"


def test_moving_comparison_target_stales_saved_coverage(repo):
    run_git(repo, "branch", "target")
    base = run_git(repo, "rev-parse", "target").strip()
    (repo / "source.py").write_text("value = 2\n")
    commit_all(repo, "source change")
    (repo / "rung.toml").write_text('[[check]]\nid="unit"\nkind="command"\nrun="python -m pytest"\n')
    cfg = config.load(repo)
    session = diagnostics.Session(cfg, cfg.check("unit"), "coverage", {"base": base, "base_ref": "target"})
    session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    assert diagnostics.saved(cfg)[0]["current"] is True
    run_git(repo, "branch", "-f", "target", "HEAD")
    assert diagnostics.saved(cfg)[0]["current"] is False


def test_moving_comparison_target_during_run_is_immediately_incomplete(repo):
    run_git(repo, "branch", "target")
    base = run_git(repo, "rev-parse", "target").strip()
    (repo / "source.py").write_text("value = 2\n")
    commit_all(repo, "source change")
    (repo / "rung.toml").write_text('[[check]]\nid="unit"\nkind="command"\nrun="python -m pytest"\n')
    cfg = config.load(repo)
    session = diagnostics.Session(cfg, cfg.check("unit"), "coverage", {"base": base, "base_ref": "target"})
    run_git(repo, "branch", "-f", "target", "HEAD")
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    assert result["current"] is False and result["exit_code"] == 2


def test_explicit_diagnostic_deadline_preserves_fractional_seconds(project, monkeypatch):
    import subprocess

    from rung.checks.base import Context
    from rung.checks.command import _run

    observed = []

    def execute(argv, **kwargs):
        observed.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("rung.core.proc.run", execute)
    cfg = config.load(project)
    _run(Context(project), cfg.check("unit"), timeout=0.25)
    assert observed == [0.25]


def test_binary_artifact_is_not_corrupted_by_text_redaction(project):
    cfg, session = _session(project)
    original = b"\x00\xff\xfe\x80binary"
    session.path("capture.bin").write_bytes(original)
    result = session.finish({"state": "complete", "exit_code": 0, "summary": "observed"})
    assert session.path("capture.bin").read_bytes() == original
    assert result["unredacted_binary_artifacts"]
    assert diagnostics.saved(cfg)[0]["current"] is True
