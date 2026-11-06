"""The merge gate's reviewed settings: base `[gate]` table, approval label, policy scope and PR head."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rung.core import ledger
from rung.core.config import ConfigError, GateConfig, load, parse_gate, save

from .conftest import commit_all, run_git
from .test_verification_workflow import rung

CHECK = '[[check]]\nid = "unit"\nkind = "command"\nrun = {run}\ncovers = ["check.py"]\n'


def _kit(repo: Path, gate: str = "") -> None:
    """A passing check on main, with an optional reviewed [gate] table, then a feature branch."""
    (repo / "check.py").write_text("raise SystemExit(0)\n")
    run = json.dumps(f"{sys.executable} -c 'import runpy; runpy.run_path(\"check.py\")'")
    (repo / "rung.toml").write_text(gate + CHECK.format(run=run))
    commit_all(repo, "kit")
    run_git(repo, "checkout", "-qb", "feature")


def _gate(repo: Path, capsys, *args: str) -> tuple[int, dict]:
    capsys.readouterr()
    code = rung(repo, "gate", "--base", "main", "--json", *args)
    return code, json.loads(capsys.readouterr().out)


def _edit_policy(repo: Path) -> None:
    (repo / ".gitleaksignore").write_text("reviewed\n")
    commit_all(repo, "policy edit")


def test_base_gate_table_overrides_a_workflow_that_asks_to_warn(repo, capsys) -> None:
    _kit(repo, '[gate]\npolicy_changes = "fail"\n\n')
    _edit_policy(repo)
    code, gate = _gate(repo, capsys, "--allow-policy-changes")
    assert code == 1 and gate["results"] == []
    assert gate["blocked"] == ["policy changed: .gitleaksignore"]
    assert gate["gate_settings"]["source"] == "base" and gate["gate_settings"]["policy_changes"] == "fail"


def test_workflow_options_apply_only_when_the_base_has_no_gate_table(repo, capsys) -> None:
    _kit(repo)
    _edit_policy(repo)
    assert _gate(repo, capsys)[0] == 1
    code, gate = _gate(repo, capsys, "--allow-policy-changes")
    assert code == 0 and gate["gate_settings"]["source"] == "options"
    assert gate["policy_changes"] == [".gitleaksignore"] and gate["results"][0]["verdict"] == "pass"


def test_base_warn_mode_runs_the_checks_and_reports_policy(repo, capsys) -> None:
    _kit(repo, '[gate]\npolicy_changes = "warn"\n\n')
    _edit_policy(repo)
    code, gate = _gate(repo, capsys)
    assert code == 0 and gate["policy_changes"] == [".gitleaksignore"] and not gate["policy_approved"]


def test_editing_the_gate_table_blocks_even_in_warn_mode(repo, capsys) -> None:
    _kit(repo, '[gate]\npolicy_changes = "warn"\n\n')
    toml = repo / "rung.toml"
    toml.write_text(toml.read_text().replace('policy_changes = "warn"', 'policy_changes = "warn"\npolicy_paths = []'))
    commit_all(repo, "loosen the gate")
    code, gate = _gate(repo, capsys)
    assert code == 1 and gate["results"] == []
    assert any("[gate] table" in reason for reason in gate["blocked"])


def test_a_pull_request_cannot_adopt_its_own_gate_table(repo, capsys) -> None:
    _kit(repo)
    toml = repo / "rung.toml"
    toml.write_text('[gate]\npolicy_changes = "warn"\n\n' + toml.read_text())
    commit_all(repo, "try to relax the gate")
    code, gate = _gate(repo, capsys)
    assert code == 1 and gate["gate_settings"]["policy_changes"] == "fail"
    assert "policy changed: rung.toml" in gate["blocked"]
    assert any("[gate] table" in reason for reason in gate["blocked"])


def test_the_approval_label_lets_reviewed_policy_changes_run(repo, capsys) -> None:
    _kit(repo)
    _edit_policy(repo)
    code, gate = _gate(repo, capsys, "--pr-label", "bug")
    assert code == 1 and gate["results"] == [] and not gate["policy_approved"]
    code, gate = _gate(repo, capsys, "--pr-label", "bug", "--pr-label", "rung-policy-approved")
    assert code == 0 and gate["policy_approved"]
    assert gate["policy_changes"] == [".gitleaksignore"] and gate["results"][0]["verdict"] == "pass"


def test_the_base_names_the_approval_label(repo, capsys) -> None:
    _kit(repo, '[gate]\napproval_label = "policy-reviewed"\n\n')
    _edit_policy(repo)
    assert _gate(repo, capsys, "--pr-label", "rung-policy-approved")[0] == 1
    code, gate = _gate(repo, capsys, "--pr-label", "policy-reviewed")
    assert code == 0 and gate["policy_approved"]


def test_approval_still_runs_the_checks(repo, capsys) -> None:
    _kit(repo)
    (repo / "check.py").write_text("raise SystemExit(1)\n")
    _edit_policy(repo)
    code, gate = _gate(repo, capsys, "--pr-label", "rung-policy-approved")
    assert code == 1 and gate["results"][0]["verdict"] == "fail"


def test_base_policy_paths_replace_workflow_paths(repo, capsys) -> None:
    (repo / "rules.yml").write_text("rules: []\n")
    (repo / "other.yml").write_text("rules: []\n")
    _kit(repo, '[gate]\npolicy_paths = ["rules.yml"]\n\n')
    (repo / "rules.yml").write_text("rules: [weakened]\n")
    (repo / "other.yml").write_text("rules: [changed]\n")
    commit_all(repo, "edit rules")
    code, gate = _gate(repo, capsys, "--policy-path", "other.yml")
    assert code == 1 and gate["policy_changes"] == ["rules.yml"]


def test_policy_paths_are_project_relative(repo, capsys) -> None:
    project = repo / "service"
    project.mkdir()
    (project / "rules.yml").write_text("rules: []\n")
    (repo / "shared.yml").write_text("rules: []\n")
    (project / "check.py").write_text("raise SystemExit(0)\n")
    run = json.dumps(f"{sys.executable} -c 'import runpy; runpy.run_path(\"check.py\")'")
    (project / "rung.toml").write_text(CHECK.format(run=run))
    commit_all(repo, "service kit")
    run_git(repo, "checkout", "-qb", "feature")
    (project / "rules.yml").write_text("rules: [weakened]\n")
    (repo / "shared.yml").write_text("rules: [weakened]\n")
    commit_all(repo, "edit rules")
    code, gate = _gate(project, capsys, "--policy-path", "rules.yml", "--policy-path", "../shared.yml")
    assert code == 1 and gate["policy_changes"] == ["service/rules.yml", "shared.yml"]
    code, gate = _gate(project, capsys, "--policy-path", "service/rules.yml")
    assert code == 0 and gate["policy_changes"] == []


def test_require_coverage_from_the_base_is_project_relative(repo, capsys) -> None:
    _kit(repo, '[gate]\nrequire_coverage = ["ops/**"]\n\n')
    (repo / "ops").mkdir()
    (repo / "ops" / "migrate.sql").write_text("select 1;\n")
    commit_all(repo, "migration")
    code, gate = _gate(repo, capsys)
    assert code == 1 and gate["required_coverage_missing"] == ["ops/migrate.sql"]


def _custom(repo: Path, checks: dict[str, str]) -> None:
    """Custom verifier checks on main; app.py then changes on a feature branch."""
    (repo / "app.py").write_text("refund = 100\n")
    (repo / "helper.py").write_text("from app import refund\nassert refund <= 100\n")
    body = '[project]\nname = "fixture"\n'
    for check_id, script in checks.items():
        (repo / script).write_text("import helper\n")
        body += f'\n[[check]]\nid = "{check_id}"\nkind = "command"\nverifies = ["app.py"]\n'
        body += f'run = "python3 {script}"\ncovers = []\n'
    (repo / "rung.toml").write_text(body)
    commit_all(repo, "custom verifiers")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "app.py").write_text("refund = 50\n")
    commit_all(repo, "change app")


def test_an_unrelated_policy_file_does_not_exempt_custom_verifiers(repo, capsys) -> None:
    # Regression: any existing --policy-path file used to switch the declaration rule off for every check.
    _custom(repo, {"verify": "verify.py"})
    code, gate = _gate(repo, capsys, "--policy-path", "README.md")
    assert code == 1 and gate["verifier_policy_missing"] == ["verify"] and gate["results"] == []


def test_policy_paths_exempt_only_the_checks_they_cover(repo, capsys) -> None:
    _custom(repo, {"first": "first.py", "second": "second.py"})
    code, gate = _gate(repo, capsys, "--policy-path", "first.py")
    assert code == 1 and gate["verifier_policy_missing"] == ["second"]
    code, gate = _gate(repo, capsys, "--policy-path", "first.py", "--policy-path", "second.py")
    assert gate["verifier_policy_missing"] == []


def test_gate_runs_only_on_the_pull_request_head(repo, capsys) -> None:
    _kit(repo)
    (repo / "new.py").write_text("x = 1\n")
    commit_all(repo, "feature work")
    head = run_git(repo, "rev-parse", "HEAD").strip()
    assert _gate(repo, capsys, "--pr-head", head)[0] == 0
    # pull_request_target checks out the base by default: the gate must not pass code it never saw.
    run_git(repo, "checkout", "-q", "main")
    code, gate = _gate(repo, capsys, "--pr-head", head)
    assert code == 1 and gate["results"] == []
    assert "not the pull request head" in gate["blocked"][0]


def test_gate_accepts_the_merge_commit_of_the_pull_request_head(repo, capsys) -> None:
    _kit(repo)
    (repo / "new.py").write_text("x = 1\n")
    commit_all(repo, "feature work")
    head = run_git(repo, "rev-parse", "HEAD").strip()
    run_git(repo, "checkout", "-q", "main")
    (repo / "base.txt").write_text("base moved\n")
    commit_all(repo, "base moves on")
    run_git(repo, "checkout", "-q", "--detach")
    run_git(repo, "merge", "-q", "--no-ff", "--no-edit", head)
    code, gate = _gate(repo, capsys, "--pr-head", head)
    assert code == 0, gate["blocked"]
    run_git(repo, "checkout", "-q", "main")
    (repo / "on-top.txt").write_text("not a merge\n")
    commit_all(repo, "single parent")
    assert _gate(repo, capsys, "--pr-head", head)[0] == 1


def test_gate_table_validation_and_round_trip(repo) -> None:
    assert parse_gate({}) == GateConfig()
    for table, message in (
        ({"policy_changes": "skip"}, "policy_changes"),
        ({"policy_paths": "rules.yml"}, "policy_paths"),
        ({"approval_label": ""}, "approval_label"),
        ({"policy_change": "fail"}, "did you mean `policy_changes`"),
    ):
        with pytest.raises(ConfigError, match=message):
            parse_gate({"gate": table})
    (repo / "rung.toml").write_text('[gate]\npolicy_changes = "fail"\napproval_label = "ok"\n')
    cfg = load(repo)
    save(cfg)
    assert load(repo).gate == GateConfig(policy_changes="fail", approval_label="ok")


def test_reuse_ignores_session_noise_but_not_the_toolchain(monkeypatch) -> None:
    entry = {"hash": "fixed"}
    monkeypatch.setenv("TERM_SESSION_ID", "one")
    first = ledger._environment_hash(entry, {})
    monkeypatch.setenv("TERM_SESSION_ID", "two")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    assert ledger._environment_hash(entry, {}) == first
    for name, value in (("PATH", "/elsewhere"), ("PYTHONPATH", "x"), ("NODE_OPTIONS", "--x"), ("UV_PYTHON", "3.9")):
        monkeypatch.setenv(name, value)
        assert ledger._environment_hash(entry, {}) != first
        first = ledger._environment_hash(entry, {})
    names = [name for name, _ in ledger.tool_environment({"LC_ALL": "C", "GOOGLE_TOKEN": "x", "GOFLAGS": "-v"})]
    assert names == ["GOFLAGS", "LC_ALL"]


def _action_steps() -> list[dict]:
    yaml = pytest.importorskip("yaml")
    action = Path(__file__).resolve().parents[1] / "action.yml"
    return yaml.safe_load(action.read_text())["runs"]["steps"]


def _run_step(tmp_path: Path, cwd: Path, env: dict[str, str], rung_exit: int, labels: str = "") -> tuple[int, str]:
    """Run the action's `Run checks` script with stand-in `rung` and `gh` commands."""
    import os
    import subprocess

    step = next(step for step in _action_steps() if step.get("id") == "check")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "rung").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "$RUNNER_TEMP/args"\nexit {rung_exit}\n')
    (bindir / "gh").write_text(f"#!/bin/sh\nprintf '{labels}'\n")
    for tool in ("rung", "gh"):
        (bindir / tool).chmod(0o755)
    output = tmp_path / "output"
    output.write_text("")
    base_env = {
        "PATH": os.pathsep.join((str(bindir), os.environ["PATH"])),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "POLICY_CHANGES": "fail",
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=cwd, env={**base_env, **env}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return proc.returncode, output.read_text()


@pytest.mark.parametrize("rung_exit", [0, 1, 2])
def test_action_fails_in_the_step_that_ran_the_gate(tmp_path, rung_exit) -> None:
    code, output = _run_step(tmp_path, tmp_path, {"BASE_REF": ""}, rung_exit)
    assert code == rung_exit
    assert f"passed={'true' if rung_exit == 0 else 'false'}" in output


def test_action_publishes_after_the_gate_but_never_decides_it() -> None:
    steps = _action_steps()
    names = [step.get("name") for step in steps]
    assert "Gate" not in names
    later = steps[[step.get("id") for step in steps].index("check") + 1 :]
    assert later and all(str(step.get("if", "")).startswith("always()") for step in later)
    # The step that runs repository code must not let a later step turn a failure into a pass.
    check = next(step for step in steps if step.get("id") == "check")
    assert 'exit "$code"' in check["run"]


def test_action_passes_the_label_only_when_it_was_just_applied_and_is_still_present(repo, tmp_path) -> None:
    run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    env = {
        "BASE_REF": "main",
        "PR_HEAD": "abc123",
        "PR_NUMBER": "7",
        "REPO": "fixture/project",
        "EVENT_ACTION": "labeled",
        "EVENT_LABEL": "rung-policy-approved",
    }
    args = tmp_path / "args"
    assert _run_step(tmp_path, repo, env, 0, labels="bug\\nrung-policy-approved\\n")[0] == 0
    passed = args.read_text().splitlines()
    assert "--pr-label=rung-policy-approved" in passed and "--pr-head=abc123" in passed
    assert _run_step(tmp_path, repo, env, 0, labels="bug\\n")[0] == 0
    assert not any(arg.startswith("--pr-label") for arg in args.read_text().splitlines())
    assert (
        _run_step(tmp_path, repo, {**env, "EVENT_ACTION": "synchronize"}, 0, labels="rung-policy-approved\\n")[0] == 0
    )
    assert not any(arg.startswith("--pr-label") for arg in args.read_text().splitlines())


def test_reuse_compares_test_runner_options_from_the_environment() -> None:
    names = [name for name, _ in ledger.tool_environment(
        {"PYTEST_ADDOPTS": "-k refund", "PYTEST_PLUGINS": "x", "SHLVL": "2", "PWD": "/tmp"}
    )]  # fmt: skip
    assert names == ["PYTEST_ADDOPTS", "PYTEST_PLUGINS"]


def test_glob_exclusions_remove_matches(repo: Path) -> None:
    from rung.core import git

    (repo / "a.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "g.txt").write_text("guide\n")
    commit_all(repo, "files")
    found = git.expand(repo, ["**/*", "!**/*.md", "!docs/**"])
    assert "a.py" in found and "README.md" not in found and "docs/g.txt" not in found
