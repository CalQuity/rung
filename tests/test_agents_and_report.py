from __future__ import annotations

import json
import sys
from pathlib import Path

from rung import agents
from rung.analysis import report
from rung.checks import runner
from rung.core import config

from .conftest import commit_all


def _project(repo: Path) -> config.Config:
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text("x = 1\n")
    (repo / "rung.toml").write_text(
        f'[project]\nname = "demo"\n\n[[check]]\nid = "unit"\nkind = "command"\n'
        f'run = "{sys.executable} -c \'pass\'"\ncovers = ["src/*.py"]\n'
    )
    (repo / "LAWS.md").write_text("# Laws\n\n## core-ok\nCore works.\n- enforced by: unit\n- status: approved\n")
    commit_all(repo, "project")
    return config.load(repo)


def test_stop_hook_blocks_until_touched_checks_are_fresh(repo: Path) -> None:
    cfg = _project(repo)
    assert agents.stop_hook(repo, "{}") == (0, "")  # no edits yet

    (repo / "src" / "core.py").write_text("x = 2\n")
    code, message = agents.stop_hook(repo, "{}")
    assert code == 2 and "rung check unit" in message

    runner.run_check(cfg, cfg.check("unit"))
    assert agents.stop_hook(repo, "{}") == (0, "")


def test_stop_hook_never_loops_and_fails_open(repo: Path, tmp_path: Path) -> None:
    _project(repo)
    (repo / "src" / "core.py").write_text("x = 3\n")
    assert agents.stop_hook(repo, json.dumps({"stop_hook_active": True})) == (0, "")
    assert agents.stop_hook(tmp_path, "{}") == (0, "")  # no rung.toml: allow
    assert agents.stop_hook(repo, "not json")[0] == 2  # bad payload is ignored, the check still applies


def test_hook_install_merges_with_existing_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    agents.install_stop_hook(tmp_path)
    agents.install_stop_hook(tmp_path)
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert len(data["hooks"]["Stop"]) == 1


def test_report_markdown_summarizes_checks_and_laws(repo: Path) -> None:
    cfg = _project(repo)
    runner.run_check(cfg, cfg.check("unit"))
    data = report.build(cfg)
    assert data["summary"] == {"checks_fresh": 1, "checks_total": 1, "laws_held": 1, "laws_approved": 1,
                               "new_findings": 0, "new_below_threshold": 0}  # fmt: skip
    md = report.markdown(data)
    assert md.startswith(report.COMMENT_MARKER)
    assert "Core works." in md and "`unit`" in md


def test_codex_hook_is_opt_in_and_uses_structured_stop_protocol(repo, monkeypatch, capsys):
    from io import StringIO

    from rung import cli

    cfg = _project(repo)
    hooks = repo / ".codex" / "hooks.json"
    assert cli.main(["-C", str(repo), "agents", "--hooks"]) == 0
    assert not hooks.exists()
    hooks.parent.mkdir()
    hooks.write_text('{"description":"local policy","hooks":{"SessionStart":[]}}')
    for _ in range(2):
        assert cli.main(["-C", str(repo), "agents", "--codex-hooks"]) == 0
    data = json.loads(hooks.read_text())
    assert data["description"] == "local policy"
    assert data["hooks"]["SessionStart"] == []
    stop = data["hooks"]["Stop"]
    assert len(stop) == 1 and stop[0]["hooks"][0]["command"] == "rung hook stop --agent codex"
    capsys.readouterr()

    (repo / "src" / "core.py").write_text("x = 2\n")

    def codex_stop(**fields):
        payload = {"cwd": str(repo / "src"), **fields}
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
        assert cli.main(["hook", "stop", "--agent", "codex"]) == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        return json.loads(captured.out)

    blocked = codex_stop()
    assert blocked["decision"] == "block" and "rung check unit" in blocked["reason"]
    assert codex_stop(stop_hook_active=True) == {}
    assert runner.run_check(cfg, cfg.check("unit")).verdict == "pass"
    assert codex_stop() == {}


def test_cli_markdown_report_removes_terminal_controls(repo, capsys):
    from rung import cli

    _project(repo)
    path = repo / "rung.toml"
    path.write_text(path.read_text().replace('name = "demo"', 'name = "demo\\u001b[2JTitle"'))
    assert cli.main(["-C", str(repo), "report", "--no-color"]) == 0
    output = capsys.readouterr().out
    assert "demoTitle" in output and "\x1b" not in output
    assert output.startswith(report.COMMENT_MARKER + "\n")
