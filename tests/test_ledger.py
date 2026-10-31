from __future__ import annotations

import json
from pathlib import Path

from rung import ledger


def _record(root: Path, verdict: str = "pass") -> dict:
    return ledger.append(
        root,
        check_id="unit",
        kind="command",
        verdict=verdict,
        covers=ledger.snapshot(root, ["src/*.py"]),
        specs={},
        summary="ok",
    )


def test_verdict_is_fresh_until_covered_file_changes(project: Path) -> None:
    _record(project)
    assert ledger.status(project, "unit", ["src/*.py"], []).state == "fresh"

    (project / "src" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    status = ledger.status(project, "unit", ["src/*.py"], [])
    assert status.state == "stale"
    assert status.changed == ["src/core.py"]


def test_new_file_matching_pattern_stales_verdict(project: Path) -> None:
    _record(project)
    (project / "src" / "extra.py").write_text("x = 1\n")
    assert ledger.status(project, "unit", ["src/*.py"], []).changed == ["src/extra.py"]


def test_deleted_file_is_reported_missing(project: Path) -> None:
    covers = ledger.snapshot(project, ["src/core.py"])
    (project / "src" / "core.py").unlink()
    assert ledger.snapshot(project, ["src/core.py"]) == {"src/core.py": "missing"}
    assert covers["src/core.py"] != "missing"


def test_failing_verdict_reports_failing(project: Path) -> None:
    _record(project, verdict="fail")
    assert ledger.status(project, "unit", ["src/*.py"], []).state == "failing"


def test_never_run(project: Path) -> None:
    assert ledger.status(project, "unit", ["src/*.py"], []).state == "never"


def test_chain_detects_tampering(project: Path) -> None:
    _record(project)
    _record(project)
    assert ledger.verify_chain(ledger.read(project)) == []

    path = project / ledger.LEDGER_PATH
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["verdict"] = "fail"
    lines[0] = json.dumps(first, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    problems = ledger.verify_chain(ledger.read(project))
    assert any("do not match its hash" in p for p in problems)


def test_chain_detects_deleted_entry(project: Path) -> None:
    for _ in range(3):
        _record(project)
    path = project / ledger.LEDGER_PATH
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert any("link broken" in p for p in ledger.verify_chain(ledger.read(project)))
