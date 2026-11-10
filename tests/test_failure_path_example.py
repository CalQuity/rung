"""Exercise the standalone example and record its real command verdict."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from rung import cli

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "failure-path"


def test_failure_path_example_runs_standalone_and_records_a_fresh_verdict(tmp_path, capsys):
    assert (EXAMPLE / "test_store.py").is_file()
    root = tmp_path / "example"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("__pycache__", ".rung"))
    command = [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_initial_state",
        "test_successful_save",
        "test_rejected_write_preserves_state",
        "test_faulty_save_is_detected",
        "test_can_save_after_rejected_write",
    ):
        assert name in result.stderr
    assert "Ran 5 tests" in result.stderr and "OK" in result.stderr

    (root / "rung.toml").write_text('[project]\nname = "failure-path-example"\n')
    assert (
        cli.main(
            [
                "-C",
                str(root),
                "add",
                "command",
                "failure-path",
                "--run",
                f'"{sys.executable}" -m unittest discover -s . -p test_*.py -v',
                "--covers",
                "*.py",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["-C", str(root), "check", "failure-path", "--json"]) == 0
    check_output = json.loads(capsys.readouterr().out)
    assert check_output["results"][0]["verdict"] == "pass"
    assert cli.main(["-C", str(root), "report", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["checks_fresh"] == 1
    assert report["checks"][0]["state"] == "fresh"

    (root / "store.py").write_text((root / "store.py").read_text() + "\n# changed input\n")
    assert cli.main(["-C", str(root), "report", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)
    assert stale["checks"][0]["state"] == "stale"
