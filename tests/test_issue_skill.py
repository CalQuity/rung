"""The issue workflow is distributed to both clients without publishing anything."""

from __future__ import annotations

import json
import subprocess

from rung import cli, guides


def test_issue_skill_is_installed_for_both_agents_without_network(project, capsys, monkeypatch):
    real_run = subprocess.run

    def local_only(argv, *args, **kwargs):
        assert argv[0] not in {"gh", "curl", "wget"}, "installing guidance must not publish or contact GitHub"
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", local_only)
    for _ in range(2):
        assert cli.main(["-C", str(project), "agents", "--json"]) == 0
        written = json.loads(capsys.readouterr().out)["written"]
        for client in (".agents", ".claude"):
            path = f"{client}/skills/rung-issue/SKILL.md"
            assert path in written
            content = (project / path).read_text()
            assert content.startswith("---\nname: rung-issue\n")
            assert guides.load("issue") in content
    assert not (project / ".claude/settings.json").exists()
    assert not (project / ".codex/hooks.json").exists()


def test_issue_skill_destination_is_preflighted_before_any_install_writes(project, capsys):
    outside = project.parent / "outside"
    outside.mkdir()
    sentinel = outside / "SKILL.md"
    sentinel.write_text("owned by the user")
    skill = project / ".agents/skills/rung-issue"
    skill.parent.mkdir(parents=True)
    skill.symlink_to(outside, target_is_directory=True)
    assert cli.main(["-C", str(project), "agents", "--json"]) == 2
    assert "error" in capsys.readouterr().out
    assert sentinel.read_text() == "owned by the user"
    assert not (project / ".claude/skills/rung/SKILL.md").exists()
