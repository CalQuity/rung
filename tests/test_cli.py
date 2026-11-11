from __future__ import annotations

import sys
from pathlib import Path

from rung import cli
from rung.core import ledger


def run(project: Path, *args: str) -> int:
    return cli.main(["-C", str(project), *args])


def test_check_records_a_fresh_verdict_that_goes_stale(project: Path, capsys) -> None:
    assert run(project, "check") == 0
    assert "PASS" in capsys.readouterr().out
    assert run(project, "status", "--ci") == 0

    (project / "src" / "core.py").write_text("changed = True\n")
    assert run(project, "status", "--ci") == 1
    assert "changed: src/core.py" in capsys.readouterr().out


def test_check_ci_reruns_only_what_is_not_fresh(project: Path, capsys) -> None:
    run(project, "check")
    capsys.readouterr()
    assert run(project, "check", "--ci") == 0
    assert "every selected check is fresh" in capsys.readouterr().out
    assert len(ledger.history(project)) == 1


def test_failing_command_fails_the_run(project: Path) -> None:
    run(project, "add", "command", "broken", "--run", f"{sys.executable} -c 'raise SystemExit(3)'")
    assert run(project, "check", "broken") == 1
    assert ledger.history(project)[-1]["verdict"] == "fail"


def test_missing_tool_is_an_error_verdict_not_a_crash(project: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RUNG_TLA2TOOLS", str(project / "nope.jar"))
    (project / "Spec.tla").write_text("---- MODULE Spec ----\n====\n")
    (project / "Spec.cfg").write_text("SPECIFICATION Spec\nINVARIANT TypeOK\n")
    with (project / "rung.toml").open("a") as fh:
        fh.write('\n[[check]]\nid = "proto"\nkind = "tla"\nspec = "Spec.tla"\nconfig = "Spec.cfg"\ncovers = []\n')
    assert run(project, "check", "proto") == 1
    assert "tla2tools.jar not found" in capsys.readouterr().out
    assert ledger.history(project)[-1]["verdict"] == "error"


def test_init_without_tty_writes_config_and_laws(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--provider", "none") == 0
    assert (tmp_path / "rung.toml").exists()
    assert (tmp_path / "LAWS.md").exists()


def test_ledger_command_reports_intact_chain(project: Path, capsys) -> None:
    run(project, "check")
    assert run(project, "ledger") == 0
    assert "0 corrupt" in capsys.readouterr().out


def test_no_config_is_a_clean_error(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "status") == 2
    assert "rung init" in capsys.readouterr().err


def test_evidence_check_hashes_artifacts_into_the_ledger(project: Path) -> None:
    script = project / "drive.py"
    script.write_text(
        "import os, pathlib\n"
        "out = pathlib.Path(os.environ['RUNG_EVIDENCE_DIR'])\n"
        "(out / 'transcript.txt').write_text('$ app --help\\nusage: app\\n')\n"
    )
    run(project, "add", "evidence", "drive", "--run", f"{sys.executable} drive.py")
    assert run(project, "check", "drive") == 0
    entry = ledger.history(project)[-1]
    assert entry["verdict"] == "pass"
    assert entry["evidence"][0].startswith(".rung/evidence/drive/")
    assert "transcript.txt sha256:" in entry["evidence"][0]


def test_evidence_check_without_artifacts_fails(project: Path) -> None:
    run(project, "add", "evidence", "claims", "--run", f"{sys.executable} -c 'pass'")
    assert run(project, "check", "claims") == 1
    assert "recorded no evidence" in ledger.history(project)[-1]["summary"]


def test_editing_a_check_stales_only_that_check(project: Path) -> None:
    run(project, "add", "command", "other", "--run", f"{sys.executable} -c 'pass'")
    run(project, "check")
    text = (
        (project / "rung.toml")
        .read_text()
        .replace('-c \'pass\'"\ncovers = ["src/*.py"]', '-c \'pass\' "\ncovers = ["src/*.py"]', 1)
    )
    (project / "rung.toml").write_text(text)
    from rung.checks import runner
    from rung.core import config

    cfg = config.load(project)
    states = {s.check_id: s.state for s in runner.statuses(cfg)}
    assert states == {"unit": "stale", "other": "fresh"}


def test_writing_verdicts_never_stales_a_whole_repo_check(project: Path) -> None:
    run(project, "add", "command", "everything", "--run", f"{sys.executable} -c 'pass'", "--covers", "**/*")
    run(project, "check")
    from rung.checks import runner
    from rung.core import config

    cfg = config.load(project)
    assert all(s.state == "fresh" for s in runner.statuses(cfg))


def test_a_failing_check_shows_its_stderr_too(project: Path) -> None:
    script = "import sys; print('step 1 ok'); sys.exit('expected version 1.0, got none')"
    run(project, "add", "command", "noisy", "--run", f'{sys.executable} -c "{script}"')
    assert run(project, "check", "noisy") == 1
    lines = ledger.history(project)[-1]["details"]["lines"]
    assert lines[0] == "step 1 ok" and lines[-1] == "expected version 1.0, got none"


def test_build_output_never_stales_a_whole_repo_check(project: Path) -> None:
    run(project, "add", "command", "everything", "--run", f"{sys.executable} -c 'pass'", "--covers", "**/*")
    run(project, "check", "everything")
    build = project / "spec" / "lean" / ".lake" / "build"
    build.mkdir(parents=True)
    (build / "model").write_text("binary")
    from rung.checks import runner
    from rung.core import config

    cfg = config.load(project)
    assert runner.status(cfg, cfg.check("everything")).state == "fresh"


def test_s1_init_registers_detected_test_suites(tmp_path: Path) -> None:
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / "backend" / "pyproject.toml").write_text('[project]\nname = "b"\ndependencies = ["pytest"]\n')
    assert run(tmp_path, "init", "--provider", "none") == 0
    from rung.core import config

    check = config.load(tmp_path).check("backend-tests")
    assert check.get("cwd") == "backend" and check.covers == []


def test_s4_add_emits_json(project: Path, capsys) -> None:
    import json

    capsys.readouterr()
    assert run(project, "--json", "add", "command", "lint", "--run", "true") == 0
    assert json.loads(capsys.readouterr().out)["check"] == "lint"


def test_doctor_and_laws_json_are_versioned_objects(project: Path, capsys) -> None:
    import json

    capsys.readouterr()
    (project / "rung.toml").write_text(
        (project / "rung.toml").read_text() + '\n[[check]]\nid = "gone"\nkind = "command"\nrun = "rung-missing-tool"\n'
    )
    assert run(project, "doctor", "--strict", "--json") == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["schema_version"] == 1 and isinstance(doctor["tools"], list)
    assert doctor["strict_failures"] == ["gone: run (rung-missing-tool)"]
    assert run(project, "laws", "--json") == 0
    laws = json.loads(capsys.readouterr().out)
    assert laws == {"schema_version": 1, "laws": []}


def test_drafting_without_a_provider_says_how_to_set_one() -> None:
    import pytest

    from rung.core.config import LLMConfig
    from rung.core.errors import ToolMissing
    from rung.drafting import providers

    with pytest.raises(ToolMissing, match=r"rung init --provider claude-cli.*\[llm\] in rung.toml"):
        providers.Provider(LLMConfig()).complete_json("x", schema={})


def test_a_model_name_picks_its_provider_without_asking(tmp_path: Path, monkeypatch) -> None:
    from rung.core.config import load
    from rung.drafting import providers

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    asked = []
    monkeypatch.setattr("rung.cli.commands.setup.ask", lambda prompt, default="": asked.append(prompt) or "n")
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--model", "sonnet", "--no-drafts", "--no-agents") == 0
    assert not any(prompt == "Choose" for prompt in asked)
    llm = load(tmp_path).llm
    assert (llm.provider, llm.model) == ("claude-cli", "sonnet")

    assert providers.provider_for_model("gpt-6.1-sol") == "codex-cli"
    assert providers.provider_for_model("o3") == "codex-cli"
    assert providers.provider_for_model("claude-opus-5-5") == "claude-cli"
    assert providers.provider_for_model("qwen2.5-coder") is None


def test_an_explicit_provider_wins_over_the_model_name(tmp_path: Path) -> None:
    from rung.core.config import load

    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--provider", "openai-compatible", "--model", "sonnet") == 0
    assert load(tmp_path).llm.provider == "openai-compatible"
