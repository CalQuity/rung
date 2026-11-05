"""A tracked link that points outside the project is recorded by its target, never followed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rung import cli
from rung.checks import runner
from rung.core import config, ledger

from .conftest import commit_all


def test_a_link_outside_the_project_is_recorded_not_refused(repo: Path, tmp_path: Path, capsys) -> None:
    outside = tmp_path / "shared-config"
    outside.mkdir()
    elsewhere = tmp_path / "other-config"
    elsewhere.mkdir()
    (repo / "shared").symlink_to(outside)
    (repo / "rung.toml").write_text(
        f'[[check]]\nid = "unit"\nkind = "command"\nrun = {json.dumps(sys.executable + " -c pass")}\n'
    )
    commit_all(repo, "link a shared directory")
    assert cli.main(["-C", str(repo), "check", "unit", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["verdict"] == "pass", result["summary"]
    entry = ledger.read_all(repo)["unit"]
    recorded = ledger.snapshot(repo, ["**/*"])
    assert recorded["shared"].startswith("external-link:")
    assert entry["inputs"]["digest"] == ledger.inputs_digest(recorded)

    def changed() -> list[str]:
        cfg = config.load(repo)
        return runner.status(cfg, cfg.check("unit")).changed

    assert "shared" not in changed()
    (repo / "shared").unlink()
    (repo / "shared").symlink_to(elsewhere)
    assert "shared" in changed()
