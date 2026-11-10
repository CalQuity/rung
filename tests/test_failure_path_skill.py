"""Failure-path guidance is installed safely without executing its experiments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rung import cli, guides


def test_failure_path_install_is_repeatable_and_has_no_subprocess_side_effects(project, capsys, monkeypatch):
    real_run = subprocess.run

    def local_only(argv, *args, **kwargs):
        # Existing setup discovers repository metadata and the installed Lean version.
        allowed = Path(argv[0]).name == "git" or (Path(argv[0]).name == "elan" and argv[1:] == ["toolchain", "list"])
        assert allowed, "installing guidance must not execute probes or contact services"
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", local_only)
    (project / "AGENTS.md").write_text("# User guidance\nKeep this paragraph.\n")
    (project / "test_owned.py").write_text("# owned test\n")
    original_config = (project / "rung.toml").read_bytes()
    snapshots = []
    for _ in range(2):
        assert cli.main(["-C", str(project), "agents", "--json"]) == 0
        written = json.loads(capsys.readouterr().out)["written"]
        contents = []
        for client in (".agents", ".claude"):
            relative = f"{client}/skills/rung-failure-path/SKILL.md"
            assert relative in written
            content = (project / relative).read_text()
            assert content.startswith("---\nname: rung-failure-path\n")
            assert "description: >-\n  Use when" in content
            assert guides.load("failure-path") in content
            contents.append(content)
            usage = (project / client / "skills/rung/SKILL.md").read_text()
            assert "rung-failure-path" in usage
        assert contents[0] == contents[1]
        snapshots.append(contents)
    assert snapshots[0] == snapshots[1]
    assert (project / "rung.toml").read_bytes() == original_config
    assert (project / "test_owned.py").read_text() == "# owned test\n"
    assert (project / "AGENTS.md").read_text().count("Keep this paragraph.") == 1
    assert not (project / ".claude/settings.json").exists()
    assert not (project / ".codex/hooks.json").exists()
    assert not (project / ".rung/verdicts").exists()


@pytest.mark.parametrize("client", [".agents", ".claude"])
@pytest.mark.parametrize("symlink_file", [False, True], ids=["directory", "file"])
def test_failure_path_install_rejects_path_escape_before_writing(project, capsys, client, symlink_file):
    outside = project.with_name(project.name + "-outside")
    outside.mkdir()
    sentinel = outside / "SKILL.md"
    sentinel.write_text("user-owned sentinel")
    skill = project / client / "skills/rung-failure-path"
    skill.parent.mkdir(parents=True)
    if symlink_file:
        skill.mkdir()
        (skill / "SKILL.md").symlink_to(sentinel)
    else:
        skill.symlink_to(outside, target_is_directory=True)
    assert cli.main(["-C", str(project), "agents", "--json"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
    assert sentinel.read_text() == "user-owned sentinel"
    for directory in (".agents", ".claude"):
        assert not (project / directory / "skills/rung/SKILL.md").exists()
    assert not (project / "AGENTS.md").exists()


@pytest.mark.parametrize("no_agents", [False, True], ids=["default", "opt-out"])
def test_init_installs_failure_path_unless_agents_disabled(tmp_path, capsys, no_agents):
    (tmp_path / "app.py").write_text("value = 1\n")
    args = ["-C", str(tmp_path), "init", "--no-drafts", "--json"]
    if no_agents:
        args.append("--no-agents")
    assert cli.main(args) == 0
    capsys.readouterr()
    for client in (".agents", ".claude"):
        path = tmp_path / client / "skills/rung-failure-path/SKILL.md"
        assert path.exists() is not no_agents
