"""Source-bound changed-line coverage, using real temporary Git repositories."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from rung.analysis import coverage
from rung.core.config import Check, Config, LLMConfig


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def project(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tests@example.com")
    git(tmp_path, "config", "user.name", "Tests")
    root = tmp_path / "nested project"
    (root / "src").mkdir(parents=True)
    (root / "src/a.py").write_text("value = 1\n")
    (root / "rung.toml").write_text('name = "example"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    check = Check("tests", "command", options={"run": "pytest tests"})
    return Config(root, "example", LLMConfig(), [check]), check, git(tmp_path, "rev-parse", "HEAD")


def record(executed, missing=(), excluded=()):
    return {"executed_lines": list(executed), "missing_lines": list(missing), "excluded_lines": list(excluded)}


def fake_run(monkeypatch, files, returncode=0, callback=None):
    calls = []

    def run(ctx, check, env=None, *, argv, timeout=None):
        calls.append((ctx, check, argv, env))
        target = next(x.split("json:", 1)[1] for x in argv if x.startswith("--cov-report=json:"))
        if callback:
            callback()
        if files is not None:
            Path(target).write_text(json.dumps({"files": files}))
        return subprocess.CompletedProcess(argv, returncode, "stdout evidence", "stderr evidence")

    monkeypatch.setattr(coverage, "_run", run)
    return calls


def run(project, **options):
    config, check, base = project
    return coverage.run(config, check, sources=["src"], base=base, **options)


def test_counts_statement_opportunities_and_keeps_exclusions(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/a.py").write_text("value = 2\n# comment\nother = 3\nexcluded = 4\n")
    calls = fake_run(monkeypatch, {"src/a.py": record([1], [3], [4])})
    result = run(project)
    assert result["state"] == "complete"
    assert result["exit_code"] == 0
    assert result["metrics"] == {"covered": 1, "total": 2, "percent": 50.0, "changed_files": 1, "unmeasured_files": 0}
    assert result["base"] == project[2]
    assert result["files"][0]["changed_lines"] == [1, 2, 3, 4]
    assert result["files"][0]["excluded_lines"] == [4]
    assert f"--cov={config.root / 'src'}" in calls[0][2]
    assert "--cov-report=" not in calls[0][2]
    assert (config.root / result["manifest_path"]).is_file()


def test_untracked_unicode_and_deleted_files(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/a.py").unlink()
    (config.root / "src/space ü.py").write_text("# comment\nnew = 3\n")
    fake_run(monkeypatch, {"src/space ü.py": record([], [2])})
    result = run(project, fail_under=50)
    assert result["exit_code"] == 1
    assert [f["path"] for f in result["files"]] == ["src/space ü.py"]
    assert result["metrics"]["total"] == 1


def test_missing_file_record_is_incomplete(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/new.py").write_text("uncovered = 1\n")
    fake_run(monkeypatch, {})
    result = run(project)
    assert result["exit_code"] == 2
    assert result["state"] == "incomplete"
    assert result["files"][0]["unmeasured"] is True


def test_no_executable_changes_is_not_applicable(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/a.py").write_text("value = 1\n# comment\n")
    fake_run(monkeypatch, {"src/a.py": record([1])})
    result = run(project, fail_under=100)
    assert result["state"] == "not_applicable"
    assert result["metrics"]["percent"] is None
    assert result["exit_code"] == 0


@pytest.mark.parametrize(
    "files",
    [
        None,
        {"src/a.py": record([True])},
        {"src/a.py": record([0])},
        {"src/a.py": record([1], [1])},
        {"../outside.py": record([1])},
        {"src/a.py": {"executed_lines": [1]}},
        {"src/a.py": record([1]), "./src/a.py": record([1])},
    ],
)
def test_rejects_missing_malformed_and_external_reports(project, monkeypatch, files):
    fake_run(monkeypatch, files)
    result = run(project)
    assert result["exit_code"] == 2
    assert result["errors"]


@pytest.mark.parametrize("source", ["/tmp", "../src", "missing", "src/a.py"])
def test_source_requires_project_relative_directory(project, source):
    config, check, base = project
    result = coverage.run(config, check, sources=[source], base=base)
    assert result["exit_code"] == 2


def test_rejects_source_symlinks(project, tmp_path):
    config, check, base = project
    (config.root / "linked").symlink_to(config.root / "src", target_is_directory=True)
    result = coverage.run(config, check, sources=["linked"], base=base)
    assert result["exit_code"] == 2


@pytest.mark.parametrize("value", [True, -1, 101, float("inf"), float("nan")])
def test_threshold_validation(project, value):
    assert run(project, fail_under=value)["exit_code"] == 2


def test_failing_tests_cannot_be_success(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/a.py").write_text("value = 2\n")
    fake_run(monkeypatch, {"src/a.py": record([1])}, returncode=1)
    result = run(project)
    assert result["exit_code"] == 2
    assert result["state"] == "incomplete"
    assert "pytest" in " ".join(result["errors"])


@pytest.mark.parametrize(
    "command", ["pytest --cov=src", "pytest --cov-append", "pytest --cov-report=html", "pytest --no-cov", "echo pytest"]
)
def test_unsupported_coverage_options_do_not_execute(project, command, monkeypatch):
    project[1].options["run"] = command
    monkeypatch.setattr(coverage, "_run", lambda *a, **k: pytest.fail("must not execute"))
    assert run(project)["exit_code"] == 2


def test_existing_addopts_preserved_as_unsupported(project, monkeypatch):
    (project[0].root / "pytest.ini").write_text("[pytest]\naddopts = --cov=src --cov-fail-under=90\n")
    monkeypatch.setattr(coverage, "_run", lambda *a, **k: pytest.fail("must not execute"))
    result = run(project)
    assert result["exit_code"] == 2
    assert "coverage" in " ".join(result["errors"]).lower()


@pytest.mark.parametrize(
    "command",
    ["env MODE=test pytest tests -- -special", "uv run --no-project python -m pytest tests", "python -m pytest tests"],
)
def test_preserves_wrapped_command_and_pytest_separator(project, monkeypatch, command):
    project[1].options["run"] = command
    calls = fake_run(monkeypatch, {"src/a.py": record([1])})
    run(project)
    argv = calls[0][2]
    original = shlex.split(command)
    assert [x for x in argv if not x.startswith("--cov")] == original
    if "--" in original:
        assert argv.index(next(x for x in argv if x.startswith("--cov="))) < argv.index("--")


def test_timeout_retains_partial_output(project, monkeypatch):
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired("pytest", 1, output=b"partial output", stderr=b"partial error")

    monkeypatch.setattr(coverage, "_run", expired)
    result = run(project, timeout=1)
    assert result["exit_code"] == 2
    assert "timed out" in " ".join(result["errors"])
    assert any(
        "partial output" in p.read_text() for p in (project[0].root / result["manifest_path"]).parent.glob("*.txt")
    )


def test_real_pytest_cov_reports_unimported_source_as_missing(project):
    config, check, _ = project
    (config.root / "src/a.py").write_text("value = 2\n")
    (config.root / "src/unimported.py").write_text("missing = 1\n")
    (config.root / "tests").mkdir()
    (config.root / "tests/test_a.py").write_text(
        'import runpy\ndef test_a():\n    assert runpy.run_path("src/a.py")["value"] == 2\n'
    )
    check.options["run"] = f"{shlex.quote(sys.executable)} -m pytest tests -q"
    result = run(project, timeout=600.0)
    assert result["state"] == "complete", result["errors"]
    assert result["metrics"]["covered"] == 1
    assert result["metrics"]["total"] == 2
    assert result["metrics"]["percent"] == 50


@pytest.mark.parametrize(
    "command",
    [
        "pytest -oaddopts=--cov=src",
        "pytest --override-ini=addopts=--cov-report=html",
        'pytest -o "addopts=--cov-append"',
    ],
)
def test_inline_addopts_cannot_mask_coverage(project, monkeypatch, command):
    project[1].options["run"] = command
    monkeypatch.setattr(coverage, "_run", lambda *a, **k: pytest.fail("must not execute"))
    assert run(project)["exit_code"] == 2


def test_duplicate_json_keys_are_invalid(project, monkeypatch):
    calls = fake_run(monkeypatch, {"src/a.py": record([1])})
    original = coverage._run

    def duplicate(*args, **kwargs):
        completed = original(*args, **kwargs)
        report = next(x.split("json:", 1)[1] for x in calls[0][2] if x.startswith("--cov-report=json:"))
        Path(report).write_text(
            '{"files":{"src/a.py":{"executed_lines":[],"executed_lines":[1],"missing_lines":[],"excluded_lines":[]}}}'
        )
        return completed

    monkeypatch.setattr(coverage, "_run", duplicate)
    assert run(project)["exit_code"] == 2


def test_report_cannot_name_nonexistent_statement_lines(project, monkeypatch):
    fake_run(monkeypatch, {"src/a.py": record([200])})
    assert run(project)["exit_code"] == 2


def test_source_changed_during_execution_is_incomplete(project, monkeypatch):
    config, _, _ = project
    fake_run(
        monkeypatch, {"src/a.py": record([1])}, callback=lambda: (config.root / "src/a.py").write_text("value = 55\n")
    )
    result = run(project)
    assert result["exit_code"] == 2
    assert result["current"] is False


def test_check_cwd_report_paths_and_source_paths_are_distinct(project, monkeypatch):
    config, check, _ = project
    (config.root / "tests").mkdir()
    check.options["cwd"] = "tests"
    (config.root / "src/a.py").write_text("value = 2\n")
    fake_run(monkeypatch, {"../src/a.py": record([1])})
    result = run(project)
    assert result["state"] == "complete"
    assert result["files"][0]["path"] == "src/a.py"


def test_report_symlink_is_rejected(project, monkeypatch):
    config, _, _ = project
    (config.root / "src/link.py").symlink_to(config.root / "src/a.py")
    fake_run(monkeypatch, {"src/link.py": record([1])})
    assert run(project)["exit_code"] == 2


def test_unrelated_symlink_is_not_a_changed_source(project, monkeypatch):
    config, _, _ = project
    (config.root / "unrelated.txt").symlink_to("/tmp/no-such-file")
    fake_run(monkeypatch, {"src/a.py": record([1])})
    assert run(project)["exit_code"] == 0


def test_binary_coverage_database_is_discarded_after_json(project, monkeypatch):
    def write_db():
        directories = list((project[0].root / ".rung/evidence/diagnostics/coverage").iterdir())
        (directories[0] / ".coverage").write_bytes(b"SQLite format 3\x00binary")

    fake_run(monkeypatch, {"src/a.py": record([1])}, callback=write_db)
    result = run(project)
    assert result["exit_code"] == 0
    assert not (project[0].root / result["manifest_path"]).with_name(".coverage").exists()


def test_parent_pytest_configuration_cannot_silently_override_coverage(project, monkeypatch):
    (project[0].root.parent / "pytest.ini").write_text("[pytest]\naddopts = --cov-report=html\n")
    monkeypatch.setattr(coverage, "_run", lambda *a, **k: pytest.fail("must not execute"))
    result = run(project)
    assert result["exit_code"] == 2


def test_saved_coverage_revalidates_original_comparison_ref(project, monkeypatch):
    from rung.analysis.diagnostics import saved

    config, check, base = project
    git(config.root, "branch", "comparison-target", base)
    fake_run(monkeypatch, {"src/a.py": record([1])})
    result = coverage.run(config, check, sources=["src"], base="comparison-target")
    assert result["parameters"]["base_ref"] == "comparison-target"
    assert result["parameters"]["base"] == base
    assert saved(config)[0]["current"] is True
    # A commit on the current branch can later become the target's new merge
    # base. Moving only the comparison ref must invalidate saved evidence.
    (config.root / "src/a.py").write_text("value = 2\n")
    git(config.root, "add", "src/a.py")
    git(config.root, "commit", "-qm", "advance current branch")
    fake_run(monkeypatch, {"src/a.py": record([1])})
    result = coverage.run(config, check, sources=["src"], base="comparison-target")
    assert saved(config)[0]["current"] is True
    git(config.root, "branch", "-f", "comparison-target", "HEAD")
    assert saved(config)[0]["current"] is False


def test_real_pytest_fractional_timeout_is_enforced(project):
    config, check, _ = project
    (config.root / "tests").mkdir()
    (config.root / "tests/test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(10)\n")
    check.options["run"] = f"{shlex.quote(sys.executable)} -m pytest tests -q"
    result = run(project, timeout=0.5)
    assert result["exit_code"] == 2
    assert "timed out after 0.5s" in " ".join(result["errors"])


@pytest.mark.parametrize(
    "command",
    [
        "env -i pytest tests",
        "env --ignore-environment pytest tests",
        "env -u COVERAGE_FILE pytest tests",
        "env --unset=COVERAGE_FILE pytest tests",
        "env COVERAGE_FILE=.coverage pytest tests",
        "uv run --env-file=.env pytest tests",
        "uv run --env-file .env pytest tests",
    ],
)
def test_wrapper_cannot_overwrite_existing_coverage_database(project, monkeypatch, command):
    config, check, _ = project
    check.options["run"] = command
    original = b"user coverage database bytes"
    (config.root / ".coverage").write_bytes(original)
    monkeypatch.setattr(coverage, "_run", lambda *a, **k: pytest.fail("must not execute"))
    result = run(project)
    assert result["exit_code"] == 2
    assert (config.root / ".coverage").read_bytes() == original
