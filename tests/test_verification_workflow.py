"""Exercise the developer's setup, change planning and merge decision through the CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rung import cli
from rung.checks import prove
from rung.core import ledger

from .conftest import commit_all, run_git


def rung(root: Path, *args: str) -> int:
    return cli.main(["-C", str(root), *args])


def test_preview_is_offline_and_refresh_keeps_existing_policy(repo, monkeypatch, capsys) -> None:
    def unexpected(*_args):
        raise AssertionError("setup started a model provider")

    monkeypatch.setattr("rung.drafting.providers.get", unexpected)
    assert rung(repo, "init", "--dry-run", "--no-agents", "--provider", "claude-cli", "--json") == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] and "rung.toml" in preview["files"]
    assert not (repo / "rung.toml").exists()
    assert rung(repo, "init", "--no-drafts", "--no-agents", "--provider", "claude-cli") == 0
    cfg = repo / "rung.toml"
    cfg.write_text("# Review policy\n" + cfg.read_text())
    original = cfg.read_text()
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert rung(repo, "init", "--refresh", "--no-drafts", "--no-agents") == 0
    assert cfg.read_text().startswith(original)
    assert 'id = "tests"' in cfg.read_text()
    updated = cfg.read_bytes()
    assert rung(repo, "init", "--refresh", "--no-drafts", "--no-agents") == 0
    assert cfg.read_bytes() == updated


def test_workspace_preview_keeps_each_runner_and_shared_inputs(repo, capsys) -> None:
    member = repo / "packages" / "app"
    member.mkdir(parents=True)
    (repo / "package.json").write_text('{"packageManager":"pnpm@10.0.0"}')
    (member / "package.json").write_text('{"scripts":{"test":"vitest run"}}')
    (member / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert rung(repo, "init", "--dry-run", "--json") == 0
    checks = [check for check in json.loads(capsys.readouterr().out)["checks"] if check["kind"] == "command"]
    assert {check["options"]["run"] for check in checks} == {"python -m pytest", "pnpm test"}
    assert all(check["covers"] == [] and check["options"]["cwd"] == "packages/app" for check in checks)


def test_change_plan_recommends_proof_in_the_changed_workspace(repo, capsys) -> None:
    (repo / "go").mkdir()
    (repo / "go" / "go.mod").write_text("module fixture\n")
    (repo / "py").mkdir()
    (repo / "py" / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert rung(repo, "plan", "--path", "py/app.py", "--json") == 0
    recommendations = json.loads(capsys.readouterr().out)["plan"]
    assert [r["command"] for r in recommendations if r["command"].startswith("rung prove ")] == [
        "rung prove 'python -m pytest' --cwd py"
    ]


def test_inferred_cargo_checks_require_a_lockfile_and_a_passing_case(repo, monkeypatch, capsys) -> None:
    import os

    bin_dir = repo.parent / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text("#!/bin/sh\nprintf 'test result: ok. %s passed; 0 failed\\n' \"$FIXTURE_CASES\"\n")
    cargo.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ["PATH"])))
    (repo / "Cargo.toml").write_text('[package]\nname = "fixture"\nversion = "0.1.0"\n')
    assert rung(repo, "init", "--no-drafts", "--no-agents") == 0
    capsys.readouterr()
    assert rung(repo, "doctor", "--strict", "--json") == 1
    assert any("cargo generate-lockfile" in row["detail"] for row in json.loads(capsys.readouterr().out))
    (repo / "Cargo.lock").touch()
    for cases, exit_code in (("0", 1), ("1", 0)):
        monkeypatch.setenv("FIXTURE_CASES", cases)
        assert rung(repo, "check", "tests", "--json") == exit_code
        assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] == ("fail" if exit_code else "pass")


def _configure(repo: Path, *, extra: str = "") -> None:
    (repo / "check.py").write_text("raise SystemExit(0)\n")
    launch = f"{sys.executable} -c 'import runpy; runpy.run_path(\"check.py\")'"
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "unit"\nkind = "command"\nrun = {json.dumps(launch)}\ncovers = ["check.py"]\n' + extra
    )
    commit_all(repo, "kit and check")
    run_git(repo, "checkout", "-qb", "feature")


def test_gate_reruns_even_a_rehashed_verdict(repo, capsys) -> None:
    _configure(repo)
    (repo / "check.py").write_text("raise SystemExit(1)\n")
    assert rung(repo, "check", "--changed", "--base", "main") == 1
    path = ledger.verdict_path(repo, "unit")
    entry = json.loads(path.read_text())
    entry["verdict"] = "pass"
    entry["hash"] = ledger._self_hash(entry)
    path.write_text(json.dumps(entry))
    assert rung(repo, "check", "--changed", "--base", "main", "--ci") == 1
    capsys.readouterr()
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert not gate["passed"] and gate["results"][0]["verdict"] == "fail"


def test_gate_blocks_renamed_policy_before_running_code(repo, capsys) -> None:
    (repo / ".gitleaksignore").write_text("policy\n")
    _configure(repo)
    run_git(repo, "mv", ".gitleaksignore", "moved")
    (repo / "check.py").write_text("from pathlib import Path\nPath('ran').touch()\n")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == [".gitleaksignore"]
    assert gate["results"] == [] and not (repo / "ran").exists()


def test_gate_requires_approved_law_checks_and_rejects_external_captures(repo, capsys) -> None:
    (repo / "LAWS.md").write_text("## refund\nRefunds are bounded.\n- enforced by: demo\n- status: approved\n")
    _configure(repo, extra='\n[[check]]\nid = "demo"\nkind = "evidence"\n')
    assert rung(repo, "gate", "unit", "--base", "main", "--json") == 1
    assert "approved law refund" in json.loads(capsys.readouterr().out)["blocked"][0]
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    assert "external capture" in json.loads(capsys.readouterr().out)["blocked"][0]


def test_change_plan_shows_scopes_and_missing_checks(repo, capsys) -> None:
    _configure(repo)
    assert rung(repo, "plan", "--path", "check.py", "--path", "other.py", "--json") == 0
    scope = json.loads(capsys.readouterr().out)["change"]
    assert scope["checks"] == ["unit"]
    assert scope["without_execution_check"] == ["other.py"]
    assert scope["run"] == "rung check unit"


def test_missing_and_modified_artifacts_stale_a_verdict(repo, capsys) -> None:
    _configure(repo, extra='\n[[check]]\nid = "demo"\nkind = "evidence"\nrequire = [".txt"]\n')
    capture = repo.parent / "capture.txt"
    capture.write_text("recorded")
    assert rung(repo, "check", "unit") == 0
    assert rung(repo, "attest", "demo", str(capture), "--by", "terminal") == 0
    assert rung(repo, "status", "--ci") == 0
    stored = next((repo / ".rung" / "evidence").rglob("*.txt"))
    for tamper in (lambda: stored.write_text("different"), stored.unlink):
        tamper()
        capsys.readouterr()
        assert rung(repo, "status", "--ci", "--json") == 1
        assert "modified, or unsafe evidence" in json.loads(capsys.readouterr().out)["checks"][1]["changed"][0]


def test_agent_install_refuses_symlink_before_writing(repo) -> None:
    _configure(repo)
    outside = repo.parent / "sentinel.md"
    outside.write_text("original\n")
    (repo / "AGENTS.md").symlink_to(outside)
    assert rung(repo, "agents", "--hooks") == 2
    assert outside.read_text() == "original\n"
    assert not (repo / ".claude").exists()


def test_doctor_checks_configured_executables_without_running_them(repo, capsys) -> None:
    _configure(repo, extra='\n[[check]]\nid = "scan"\nkind = "scan"\nrun = "rung-fixture-absent-scanner"\n')
    assert rung(repo, "doctor", "--strict", "--json") == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(row["tool"].startswith("scan: run") and not row["ok"] for row in rows)


def test_fixture_error_cannot_prove_a_regression(repo) -> None:
    conf = repo / "conftest.py"
    conf.write_text("import pytest\n@pytest.fixture(autouse=True)\ndef bad():\n    raise RuntimeError('fixture')\n")
    commit_all(repo, "broken fixture")
    run_git(repo, "checkout", "-qb", "fix")
    conf.write_text("")
    (repo / "test_proof.py").write_text("def test_always_green():\n    assert True\n")
    result = prove.prove(repo, f"{sys.executable} -m pytest -q -p no:cacheprovider", "main")
    assert result.verdict == "fail"
    assert result.cases[0].base == "error"


def test_runtime_rejects_unusable_results_and_empty_assertions(repo, capsys) -> None:
    _configure(repo)
    assert rung(repo, "add", "evidence", "drive", "--run", f"{sys.executable} drive.py", "--structured") == 0
    for result, message in [
        ("not json", "JSON object"),
        ('{"passed":false}', "passed=false"),
        ('{"seed":1,"scenarios":[{"name":"empty"}]}', "must record assertions"),
    ]:
        (repo / "drive.py").write_text(
            "from pathlib import Path\nfrom os import environ\n"
            f"Path(environ['RUNG_EVIDENCE_DIR'], 'result.json').write_text({result!r})\n"
        )
        capsys.readouterr()
        assert rung(repo, "check", "drive", "--seed", "1", "--json") == 1
        assert message in json.loads(capsys.readouterr().out)["results"][0]["summary"]


def test_gate_protects_configured_specs_and_reports_uncovered_paths(repo, capsys) -> None:
    (repo / "policy.txt").write_text("original")
    _configure(repo, extra='specs = ["policy.txt"]\n')
    (repo / "policy.txt").write_text("changed")
    (repo / "new.py").write_text("pass")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["policy_changes"] == ["policy.txt"]
    assert data["uncovered_paths"] == ["new.py"]
    assert data["results"] == []


def test_agent_workflow_is_installed_for_both_clients_and_runs_from_child(repo, capsys) -> None:
    _configure(repo)
    assert rung(repo, "agents", "--hooks", "--json") == 0
    files = json.loads(capsys.readouterr().out)["written"]
    for directory in (".agents", ".claude"):
        path = f"{directory}/skills/rung/SKILL.md"
        assert path in files
        skill = (repo / path).read_text()
        assert "rung plan --changed" in skill and "rung report --json" in skill
    child = repo / "nested"
    child.mkdir()
    assert rung(child, "check", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1 and result["results"][0]["verdict"] == "pass"
    assert rung(child, "check", "does-not-exist", "--json") == 2
    assert "no check named" in json.loads(capsys.readouterr().out)["error"]


def test_known_media_requires_recognizable_contents(repo, capsys) -> None:
    _configure(repo)
    assert rung(repo, "add", "evidence", "drive", "--run", f"{sys.executable} drive.py", "--require", ".webm") == 0
    (repo / "drive.py").write_text(
        "from pathlib import Path\nfrom os import environ\n"
        "Path(environ['RUNG_EVIDENCE_DIR'], 'fake.webm').write_text('placeholder')\n"
    )
    capsys.readouterr()
    assert rung(repo, "check", "drive", "--json") == 1
    assert "required evidence missing: .webm" in json.loads(capsys.readouterr().out)["results"][0]["summary"]


def test_executable_bit_invalidates_a_local_verdict(repo, capsys) -> None:
    script = repo / "verify.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "verify"\nkind = "command"\nrun = "./verify.sh"\ncovers = ["verify.sh"]\n'
    )
    assert rung(repo, "check", "verify") == 0
    script.chmod(0o644)
    capsys.readouterr()
    assert rung(repo, "status", "--ci", "--json") == 1
    assert "verify.sh" in json.loads(capsys.readouterr().out)["checks"][0]["changed"]
    assert rung(repo, "check", "--ci", "--json") == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] == "error"


def test_node_script_changes_are_policy_but_dependency_changes_are_not(repo, capsys) -> None:
    manifest = repo / "package.json"
    manifest.write_text('{"scripts":{"test":"node --test"},"dependencies":{"example":"1.0"}}')
    (repo / "rung.toml").write_text('[[check]]\nid = "unit"\nkind = "command"\nrun = "npm test"\n')
    commit_all(repo, "node kit")
    run_git(repo, "checkout", "-qb", "feature")
    manifest.write_text('{"scripts":{"test":"exit 0"},"dependencies":{"example":"2.0"}}')
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["policy_changes"] == ["package.json"] and blocked["results"] == []
    manifest.write_text('{"scripts":{"test":"node --test"},"dependencies":{"example":"2.0"}}')
    assert rung(repo, "gate", "--base", "main", "--json") in (0, 1)  # npm availability is unrelated
    attempted = json.loads(capsys.readouterr().out)
    assert attempted["policy_changes"] == [] and attempted["results"]


def test_command_output_cannot_emit_ansi_even_when_the_tool_forces_it(repo, capsys) -> None:
    _configure(repo)
    (repo / "check.py").write_text('print("\\x1b[31mcolored\\x1b[0m")\n')
    assert rung(repo, "check", "--no-color") == 0
    terminal = capsys.readouterr().out
    assert "colored" in terminal and "\x1b" not in terminal
    assert rung(repo, "check", "--json") == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["lines"] == ["colored"]


def test_changed_check_and_agent_hook_expose_missing_scope(repo, capsys) -> None:
    from rung.agents.hooks import stop_hook

    _configure(repo)
    assert rung(repo, "check", "unit") == 0
    (repo / "new_feature.py").write_text("pass\n")
    capsys.readouterr()
    assert rung(repo, "check", "--changed", "--base", "main", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["uncovered_paths"] == ["new_feature.py"] and data["recorded"] is False
    assert stop_hook(repo, "{}")[0] == 2
    assert stop_hook(repo, '{"stop_hook_active": true}')[0] == 0
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "**/*.py", "--json") == 1
    assert "required coverage missing: new_feature.py" in json.loads(capsys.readouterr().out)["blocked"]


def test_refresh_flags_a_moved_workspace_directory(repo, capsys) -> None:
    _configure(repo, extra='cwd = "removed"\n')
    assert rung(repo, "init", "--dry-run", "--refresh", "--no-drafts", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["missing_working_directories"] == {"unit": "removed"}


def test_doctor_checks_python_modules_and_their_working_directory(repo, capsys) -> None:
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "unit"\nkind = "command"\nrun = "python3 -m rung_missing_fixture_module"\n'
    )
    assert rung(repo, "doctor", "--strict", "--json") == 1
    assert any(
        row["tool"].endswith("Python module rung_missing_fixture_module") and not row["ok"]
        for row in json.loads(capsys.readouterr().out)
    )


def test_initializer_reuses_make_target_without_guessing_pytest(repo, capsys) -> None:
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    (repo / "tests").mkdir()
    (repo / "Makefile").write_text("test:\n\tpython3 -m unittest discover -s tests\n")
    assert rung(repo, "init", "--dry-run", "--no-agents", "--json") == 0
    config = json.loads(capsys.readouterr().out)
    assert [(check["id"], check["options"].get("run")) for check in config["checks"] if check["kind"] == "command"] == [
        ("tests", "make test")
    ]
    assert "vulnerable-deps" not in config["added_checks"]


def test_gate_protects_make_recipe_for_a_generated_check(repo, capsys) -> None:
    makefile = repo / "Makefile"
    makefile.write_text("test:\n\tfalse\n")
    (repo / "rung.toml").write_text('[[check]]\nid = "tests"\nkind = "command"\nrun = "make test"\n')
    commit_all(repo, "test recipe")
    run_git(repo, "checkout", "-qb", "feature")
    makefile.write_text("test:\n\ttrue\n")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["policy_changes"] == ["Makefile"] and data["results"] == []


def test_branch_plan_preserves_comparison_base_in_the_next_command(repo, capsys) -> None:
    _configure(repo)
    (repo / "check.py").write_text("raise SystemExit(1)\n")
    assert rung(repo, "plan", "--changed", "--base", "main", "--json") == 0
    change = json.loads(capsys.readouterr().out)["change"]
    assert change["run"] == f"rung check --base {change['base']} unit"


def test_cache_does_not_reuse_pass_after_environment_change(repo, capsys, monkeypatch) -> None:
    _configure(repo)
    (repo / "check.py").write_text("from os import environ\nassert environ.get('RUNG_FIXTURE_FEATURE') == '1'\n")
    monkeypatch.setenv("RUNG_FIXTURE_FEATURE", "1")
    assert rung(repo, "check") == 0
    monkeypatch.delenv("RUNG_FIXTURE_FEATURE")
    capsys.readouterr()
    assert rung(repo, "check", "--ci", "--json") == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] == "fail"


def test_refresh_reports_changed_detected_command_without_replacing_it(repo, capsys) -> None:
    manifest = repo / "package.json"
    manifest.write_text('{"scripts":{"test":"node --test"}}')
    assert rung(repo, "init", "--no-drafts", "--no-agents") == 0
    manifest.write_text('{"packageManager":"pnpm@10","scripts":{"test":"node --test"}}')
    capsys.readouterr()
    assert rung(repo, "init", "--refresh", "--no-drafts", "--no-agents", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["changed_detected_commands"] == [{"id": "tests", "configured": "npm test", "detected": "pnpm test"}]
    assert 'run = "npm test"' in (repo / "rung.toml").read_text()


def test_gate_requires_declared_coverage_beyond_wide_freshness_inputs(repo, capsys) -> None:
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "frontend"\nkind = "command"\nrun = "true"\nverifies = ["frontend/**"]\n'
    )
    commit_all(repo, "reviewed scopes")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "ops").mkdir()
    (repo / "ops" / "migration.sql").write_text("select 1;\n")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "ops/**", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["required_coverage_missing"] == ["ops/migration.sql"] and data["results"] == []


def test_scoped_custom_gate_requires_real_reviewed_policy_paths(repo, capsys) -> None:
    (repo / "app.py").write_text("refund = 100\n")
    (repo / "verify.py").write_text("from app import refund\nassert refund <= 100\n")
    (repo / "rung.toml").write_text('[project]\nname = "fixture"\n')
    assert rung(repo, "add", "command", "verify", "--run", "python3 verify.py", "--verifies", "app.py") == 0
    commit_all(repo, "application and declared check")
    (repo / "app.py").write_text("refund = 50\n")
    capsys.readouterr()
    for options in ((), ("--policy-path", "missing.py")):
        assert rung(repo, "gate", "--base", "main", *options, "--json") == 1
        gate = json.loads(capsys.readouterr().out)
        assert gate["verifier_policy_missing"] == ["verify"] and gate["results"] == []
    assert rung(repo, "gate", "--base", "main", "--policy-path", "verify.py", "--json") == 0
    assert json.loads(capsys.readouterr().out)["verifier_policy_missing"] == []
    (repo / "verify.py").write_text("from app import refund\nassert 0 < refund <= 100\n")
    assert rung(repo, "gate", "--base", "main", "--policy-path", "verify.py", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == ["verify.py"] and gate["results"] == []


def test_add_persists_explicit_verifier_inputs(repo, capsys) -> None:
    from rung.core.config import load

    (repo / "rung.toml").write_text('[project]\nname = "fixture"\n')
    assert (
        rung(repo, "add", "command", "verify", "--run", "python3 verify.py", "--specs", "verify.py", "helpers/**") == 0
    )
    assert load(repo).check("verify").get_list("specs") == ["verify.py", "helpers/**"]


def test_gate_protects_direct_launch_scripts(repo, capsys) -> None:
    (repo / "test_runner.py").write_text("raise SystemExit(1)\n")
    (repo / "rung.toml").write_text('[[check]]\nid = "test"\nkind = "command"\nrun = "python3 test_runner.py"\n')
    commit_all(repo, "reviewed runner")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "test_runner.py").write_text("raise SystemExit(0)\n")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["policy_changes"] == ["test_runner.py"] and data["results"] == []


def test_reuse_local_notices_replaced_external_executable(repo, capsys, monkeypatch) -> None:
    bindir = repo.parent / "bin"
    bindir.mkdir()
    tool = bindir / "fixture-check"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    (repo / "rung.toml").write_text('[[check]]\nid = "test"\nkind = "command"\nrun = "fixture-check"\n')
    assert rung(repo, "check") == 0
    tool.write_text("#!/bin/sh\nexit 1\n# changed tool\n")
    capsys.readouterr()
    assert rung(repo, "check", "--reuse-local", "--json") == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] == "fail"


def test_pytest_gate_requires_real_case_report_and_protects_hooks(repo, capsys) -> None:
    (repo / "test_app.py").write_text("def test_behavior():\n    assert 1 == 1\n")
    launch = json.dumps(f"{sys.executable} -m pytest -q -p no:cacheprovider")
    (repo / "rung.toml").write_text(f'[[check]]\nid = "tests"\nkind = "command"\nrun = {launch}\n')
    commit_all(repo, "reviewed test suite")
    run_git(repo, "checkout", "-qb", "feature")
    assert rung(repo, "gate", "--base", "main", "--json") == 0
    assert "1 passing test case" in json.loads(capsys.readouterr().out)["results"][0]["summary"]
    (repo / "conftest.py").write_text(
        "import pytest\ndef pytest_sessionstart(session):\n    pytest.exit('stop early', returncode=0)\n"
    )
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    assert json.loads(capsys.readouterr().out)["policy_changes"] == ["conftest.py"]
    assert rung(repo, "gate", "--base", "main", "--allow-policy-changes", "--json") == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] in {"fail", "error"}


def test_init_preflights_agent_settings_before_writing(repo, capsys):
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{")
    for args in (("--dry-run",), ("--no-drafts",)):
        assert rung(repo, "init", "--json", *args) == 2
        assert "Claude Code hook settings" in json.loads(capsys.readouterr().out)["error"]
        assert {path.name for path in repo.iterdir()} == {".git", ".claude", "README.md"}


def test_doctor_reports_missing_test_binary_from_package_script(repo, monkeypatch, capsys):
    from rung.cli.commands import setup

    monkeypatch.setattr(setup.shutil, "which", lambda tool, **_: "/usr/bin/npm" if tool == "npm" else None)
    (repo / "package.json").write_text('{"scripts":{"test":"vitest run"}}')
    assert rung(repo, "init", "--no-agents", "--no-drafts") == 0
    capsys.readouterr()
    assert rung(repo, "doctor", "--strict", "--json") == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(row["tool"].endswith("Node runner vitest") and not row["ok"] for row in rows)


def test_png_requires_real_chunks_and_checksums(tmp_path):
    import struct
    import zlib

    from rung.checks.command import recognizable_artifact

    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    path = tmp_path / "shot.png"
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(bytes(4)))
    iend = chunk(b"IEND", b"")
    path.write_bytes(signature + ihdr + idat + iend)
    assert recognizable_artifact(path)
    path.write_bytes(signature + ihdr + b"fake IDAT" + iend)
    assert not recognizable_artifact(path)
    path.write_bytes(signature + ihdr + idat[:-1] + b"!" + iend)
    assert not recognizable_artifact(path)


def test_terminal_wrapping_respects_cjk_cells(capsys):
    from rung.cli.output import Output, _width

    output = Output(color=False)
    output.columns = 15
    output.detail("検証の状態を詳しく説明します", indent=2)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) > 1 and all(_width(line) <= 15 for line in lines)
    assert "".join(line.strip() for line in lines) == "検証の状態を詳しく説明します"
    output.columns = 32
    path = ".rung/evidence/browser/20261105T091246Z-5a0fe136/" + "検証結果" * 7 + ".png"
    output.location(path, label="evidence: ")
    label, *lines = capsys.readouterr().out.splitlines()
    assert label.strip() == "evidence:" and all(_width(line) <= 32 for line in lines)
    assert "".join(line.strip() for line in lines) == path


def test_narrow_replay_command_and_traceback_preserve_the_copyable_location(capsys):
    from rung.checks.base import Outcome
    from rung.checks.runner import Result
    from rung.cli.commands.run import _print_result
    from rung.cli.output import Output, _width
    from rung.core.config import Check

    output = Output(color=False)
    output.columns = 32
    command = "rung check drive --seed 101"
    output.command(command, prefix="reproduce: ")
    result = Result(
        Check("drive", "evidence", [], {}),
        Outcome(
            "fail", "one assertion failed", ['  File "/tmp/project-with-long-name/drive.py", line 17, in <module>']
        ),
    )
    _print_result(result, output)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.strip() == command for line in lines)
    source = "/tmp/project-with-long-name/drive.py:17"
    assert any(line.strip() == source for line in lines)
    assert all(_width(line) <= 32 for line in lines if source not in line)
    page = ".rung/evidence/browser/20261105T091246Z-5a0fe136/index.html"
    capture = ".rung/evidence/browser/20261105T091246Z-5a0fe136/seed-101/refund.png"
    outcome = Outcome("fail", "refund did not match", [f"evidence: {capture}", "expected 32, observed 27"],
                      {page: "hash", capture: "hash"}, details={"report": page})  # fmt: skip
    _print_result(Result(result.check, outcome), output)
    text = capsys.readouterr().out
    assert text.index("expected 32, observed 27") < text.index("Report:")
    assert page in text and capture not in text


def test_gate_requires_unittest_to_run_a_passing_case(repo, capsys):
    app = repo / "app"
    app.mkdir()
    (app / "settings.py").write_text("refund_limit = 100\n")
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "unit"\nkind = "command"\nrun = "{sys.executable} -B -m unittest -q"\nverifies = ["app/**"]\n'
    )
    commit_all(repo, "declared unittest scope")
    (app / "settings.py").write_text("refund_limit = 50\n")
    args = ("gate", "--base", "main", "--require-coverage", "app/**", "--json")
    assert rung(repo, *args) == 1
    assert "no passing test cases" in json.loads(capsys.readouterr().out)["results"][0]["summary"]
    (repo / "test_app.py").write_text(
        "import unittest\nfrom app.settings import refund_limit\n"
        "class AppTests(unittest.TestCase):\n    def test_limit(self):\n        self.assertEqual(refund_limit, 50)\n"
    )
    assert rung(repo, *args) == 0
    assert json.loads(capsys.readouterr().out)["passed"]


@pytest.mark.parametrize(("command", "file"), [
    ("python3 -Bm verify", "verify.py"),
    ("env MODE=ci python3 verify.py", "verify.py"),
    ("node --test verify.js", "verify.js"),
])  # fmt: skip
def test_gate_protects_static_wrapped_launchers(repo, capsys, command, file):
    (repo / file).write_text("original verifier\n")
    (repo / "rung.toml").write_text(f'[[check]]\nid = "verify"\nkind = "command"\nrun = {json.dumps(command)}\n')
    commit_all(repo, "trusted baseline")
    (repo / file).write_text("weakened verifier\n")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == [file] and gate["results"] == []


def test_gate_protects_pytest_hooks_with_compact_interpreter_flags(repo, capsys):
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "unit"\nkind = "command"\nrun = "{sys.executable} -Bm pytest -q"\n'
    )
    commit_all(repo, "trusted baseline")
    (repo / "conftest.py").write_text("def pytest_sessionstart(session):\n    raise SystemExit(0)\n")
    assert rung(repo, "gate", "--base", "main", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == ["conftest.py"] and gate["results"] == []


def test_gate_protects_direct_package_script_wrappers(repo, capsys):
    (repo / "package.json").write_text('{"scripts":{"test":"node --no-warnings scripts/verify.mjs"}}')
    (repo / "scripts").mkdir()
    (repo / "scripts" / "verify.mjs").write_text("throw new Error('regression')\n")
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "unit"\nkind = "command"\nrun = "npm test"\ncovers = ["app.mjs"]\n'
    )
    commit_all(repo, "test policy")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "scripts" / "verify.mjs").write_text("console.log('verification bypassed')\n")
    (repo / "app.mjs").write_text("export const result = false\n")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "app.mjs", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == ["scripts/verify.mjs"]
    assert gate["results"] == []


def test_failing_command_excerpt_keeps_assertion_and_location():
    from subprocess import CompletedProcess

    from rung.checks.command import tail

    process = CompletedProcess(
        ["npm", "test"],
        1,
        "run unit\n",
        "\n".join(
            [
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:",
                "-1 !== 3",
                "at file:///repo/tests.mjs:7",
                *["internal frame"] * 30,
                "Node.js",
            ]
        ),
    )
    output = tail(process)
    assert len(output) <= 15
    assert "-1 !== 3" in output and "at file:///repo/tests.mjs:7" in output
    assert output[-1] == "Node.js"


def test_plan_separates_fast_feedback_from_shared_input_scope(repo, capsys):
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "api"\nkind = "command"\nrun = "true"\nverifies = ["api/**"]\n'
        '[[check]]\nid = "web"\nkind = "command"\nrun = "true"\nverifies = ["web/**"]\n'
    )
    assert rung(repo, "plan", "--path", "api/route.py", "--path", "shared/logic.py", "--json") == 0
    scope = json.loads(capsys.readouterr().out)["change"]
    assert scope["focused_checks"] == ["api"]
    assert scope["focused_run"] == "rung check api"
    assert scope["run"] == "rung check api web"
    assert scope["without_execution_check"] == ["shared/logic.py"]


def test_runtime_draft_checks_sandbox_can_start_before_calling_provider(repo, monkeypatch):
    from types import SimpleNamespace

    import pytest

    from rung.analysis import survey
    from rung.core import sandbox
    from rung.core.errors import ToolMissing
    from rung.drafting import runtime

    def no_model(*_args, **_kwargs):
        pytest.fail("model was called without an operational sandbox")

    monkeypatch.setattr(sandbox, "write_guard", lambda *_args, **_kwargs: ("/example/bwrap", "--"))
    monkeypatch.setattr(sandbox.proc, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    provider = SimpleNamespace(complete_json=no_model)
    with pytest.raises(ToolMissing, match="guard could not start"):
        runtime.draft_runtime(provider, repo, survey.survey(repo), focus=[], guarded=True)


def test_next_route_types_require_a_project_typecheck_script(repo, capsys):
    manifest = repo / "package.json"
    manifest.write_text('{"dependencies":{"next":"16.4.5","typescript":"5.9.3"}}')
    (repo / "tsconfig.json").write_text('{"include":[".next/types/**/*.ts","**/*.tsx"]}')
    assert rung(repo, "init", "--dry-run", "--json") == 0
    preview = json.loads(capsys.readouterr().out)
    assert all(check["id"] != "typecheck" for check in preview["checks"])
    assert "generates Next.js route types" in json.dumps(preview["survey"])
    manifest.write_text(
        '{"dependencies":{"next":"16.4.5","typescript":"5.9.3"},"scripts":{"typecheck":"next typegen && tsc --noEmit"}}'
    )
    assert rung(repo, "init", "--dry-run", "--json") == 0
    preview = json.loads(capsys.readouterr().out)
    check = next(check for check in preview["checks"] if check["id"] == "typecheck")
    assert check["options"]["run"] == "npm run typecheck"


def test_gate_protects_python_module_entry_point(repo, capsys):
    (repo / "runner").mkdir()
    (repo / "runner" / "__main__.py").write_text("raise SystemExit(1)\n")
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "unit"\nkind = "command"\nrun = "python -m runner"\ncovers = ["app.txt"]\n'
    )
    commit_all(repo, "test policy")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "runner" / "__main__.py").write_text("print('verification bypassed')\n")
    (repo / "app.txt").write_text("regression\n")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "app.txt", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == ["runner/__main__.py"]
    assert gate["results"] == []


def test_pytest_all_skipped_is_not_a_passing_local_verdict(repo, capsys):
    (repo / "test_demo.py").write_text(
        'import pytest\n\n@pytest.mark.skip(reason="not yet ready")\ndef test_unverified():\n    pass\n'
    )
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "unit"\nkind = "command"\nrun = {json.dumps(sys.executable + " -m pytest -q")}\n'
    )
    assert rung(repo, "check", "unit", "--json") == 1
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["verdict"] == "fail"
    assert "reported no passing test cases" in result["summary"]


def test_gate_blocks_a_changed_uv_launcher_with_required_app_coverage(repo, capsys):
    (repo / "verify.py").write_text("raise SystemExit(1)\n")
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "verify"\nkind = "command"\nrun = "uv run --no-project python verify.py"\n'
        'verifies = ["app.py"]\n'
    )
    (repo / "app.py").write_text("refund = 100\n")
    commit_all(repo, "reviewed app and check")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "verify.py").write_text("print('skipped verification')\n")
    (repo / "app.py").write_text("refund = 150\n")
    commit_all(repo, "weaken verification")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "app.py", "--json") == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["policy_changes"] == ["verify.py"]
    assert gate["required_coverage_missing"] == []
    assert gate["results"] == []


def test_focused_workspace_check_records_only_matching_targets(repo, capsys):
    (repo / "rung.toml").write_text(
        "\n".join(
            f'[[check]]\nid = "{name}"\nkind = "command"\nrun = "true"\nverifies = ["{name}/**"]\n'
            for name in ("api", "web")
        )
    )
    commit_all(repo, "workspace kit")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "api").mkdir()
    (repo / "api" / "app.py").write_text("refund = 150\n")
    assert rung(repo, "check", "--changed", "--focused", "--base", "main", "--json") == 0
    focused = json.loads(capsys.readouterr().out)
    assert focused["selection"] == "focused"
    assert focused["selected"] == ["api"] and focused["omitted"] == ["web"]
    assert [row["id"] for row in focused["results"]] == ["api"]

    (repo / "api" / "app.py").unlink()
    (repo / "shared.py").write_text("refund = 150\n")
    assert rung(repo, "check", "--changed", "--focused", "--base", "main", "--json") == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["results"] == [] and not blocked["recorded"]


def test_gate_protects_a_symlinked_launcher_and_its_intermediate_links(repo, capsys):
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "verify"\nkind = "command"\nrun = "python3 verify.py"\nverifies = ["app.py"]\n'
    )
    (repo / "verify.py").symlink_to("alias.py")
    (repo / "alias.py").symlink_to("trusted.py")
    (repo / "trusted.py").write_text("from app import refund\nassert refund == 100\n")
    (repo / "unused.py").write_text("pass\n")
    (repo / "app.py").write_text("refund = 100\n")
    commit_all(repo, "trusted baseline")
    run_git(repo, "switch", "-qc", "bypass")
    (repo / "app.py").write_text("refund = 150\n")
    (repo / "trusted.py").write_text("pass\n")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "app.py", "--json") == 1
    result = json.loads(capsys.readouterr().out)
    assert result["policy_changes"] == ["trusted.py"] and result["results"] == []

    run_git(repo, "restore", "trusted.py")
    (repo / "alias.py").unlink()
    (repo / "alias.py").symlink_to("unused.py")
    assert rung(repo, "gate", "--base", "main", "--require-coverage", "app.py", "--json") == 1
    result = json.loads(capsys.readouterr().out)
    assert result["policy_changes"] == ["alias.py"] and result["results"] == []


def test_doctor_and_reuse_inspect_an_uv_delegated_executable(repo, capsys, monkeypatch):
    import os

    bindir = repo.parent / "fixture-bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join(("../fixture-bin", os.environ["PATH"])))
    tool = bindir / "rung-fixture-delegate"
    (repo / "rung.toml").write_text(
        '[[check]]\nid = "wrapped"\nkind = "command"\nrun = "uv run --no-project rung-fixture-delegate"\n'
    )
    assert rung(repo, "doctor", "--strict", "--json") == 1
    assert any(not row["ok"] and "rung-fixture-delegate" in row["tool"] for row in json.loads(capsys.readouterr().out))
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    assert rung(repo, "doctor", "--strict", "--json") == 0
    assert rung(repo, "check", "wrapped") == 0
    capsys.readouterr()
    assert rung(repo, "check", "wrapped", "--reuse-local", "--json") == 0
    assert json.loads(capsys.readouterr().out)["selected"] == []
    tool.write_text("#!/bin/sh\nexit 1\n")
    assert rung(repo, "check", "wrapped", "--reuse-local", "--json") == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["verdict"] == "fail"


def test_initializer_deduplicates_only_a_single_make_delegate(repo, capsys):
    backend = repo / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (backend / "uv.lock").touch()
    makefile = repo / "Makefile"
    makefile.write_text("test:\n\tcd backend && uv run pytest -q\n\n.PHONY: test\n")
    assert rung(repo, "init", "--dry-run", "--no-agents", "--json") == 0
    commands = [c["options"]["run"] for c in json.loads(capsys.readouterr().out)["checks"] if c["kind"] == "command"]
    assert commands == ["uv run pytest"]
    makefile.write_text("test:\n\tcd backend && uv run pytest -q\n\n\tpython3 smoke.py\n")
    assert rung(repo, "init", "--dry-run", "--no-agents", "--json") == 0
    commands = [c["options"]["run"] for c in json.loads(capsys.readouterr().out)["checks"] if c["kind"] == "command"]
    assert commands == ["make test", "uv run pytest"]
