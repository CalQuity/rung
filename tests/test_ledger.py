from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rung.core import ledger


def _record(root: Path, verdict: str = "pass", check_id: str = "unit", **extra) -> dict:
    return ledger.record(
        root,
        check_id=check_id,
        kind="command",
        verdict=verdict,
        covers=ledger.snapshot(root, ["src/*.py"]),
        specs={},
        summary="ok",
        **extra,
    )


def _status(root: Path, **kwargs) -> ledger.Status:
    return ledger.View(root).status("unit", ["src/*.py"], [], **kwargs)


def test_verdict_is_fresh_until_covered_file_changes(project: Path) -> None:
    _record(project)
    assert _status(project).state == "fresh"
    (project / "src" / "core.py").write_text("def add(a, b):\n    return a - b\n")
    status = _status(project)
    assert status.state == "stale" and status.changed == ["src/core.py"]


def test_new_file_matching_pattern_stales_verdict(project: Path) -> None:
    _record(project)
    (project / "src" / "extra.py").write_text("x = 1\n")
    assert _status(project).changed == ["src/extra.py"]


def test_without_the_local_cache_a_change_is_still_detected(project: Path) -> None:
    _record(project)
    for cached in (project / ledger.CACHE_DIR / "inputs").glob("*.json"):
        cached.unlink()  # a fresh clone in CI has no cache
    (project / "src" / "core.py").write_text("changed = True\n")
    assert _status(project).changed == ["(covered files changed)"]


def test_failing_and_never(project: Path) -> None:
    assert _status(project).state == "never"
    _record(project, verdict="fail")
    assert _status(project).state == "failing"


def test_a_hand_edited_verdict_is_not_trusted(project: Path) -> None:
    _record(project)
    path = ledger.verdict_path(project, "unit")
    entry = json.loads(path.read_text())
    entry["verdict"] = "pass"
    entry["summary"] = "looks fine to me"
    path.write_text(json.dumps(entry))
    assert _status(project).changed == [ledger.CORRUPT]
    assert ledger.problems(project)


def test_a_merge_conflict_only_stales_that_check(project: Path) -> None:
    _record(project, check_id="unit")
    _record(project, check_id="other")
    path = ledger.verdict_path(project, "unit")
    path.write_text("<<<<<<< ours\n" + path.read_text() + "=======\n{}\n>>>>>>> theirs\n")
    view = ledger.View(project)
    assert view.status("unit", ["src/*.py"], []).state == "stale"
    assert view.status("other", ["src/*.py"], []).state == "fresh"


def test_verdicts_stay_small_however_many_files_they_cover(project: Path) -> None:
    for i in range(500):
        (project / "src" / f"m{i}.py").write_text(f"x = {i}\n")
    _record(project)
    assert ledger.verdict_path(project, "unit").stat().st_size < 2_000


def test_max_age_expires_a_verdict(project: Path) -> None:
    _record(project)
    path = ledger.verdict_path(project, "unit")
    entry = json.loads(path.read_text())
    entry["at"] = (datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds")
    entry["hash"] = ledger._self_hash(entry)
    path.write_text(json.dumps(entry))
    assert _status(project).state == "fresh"
    assert _status(project, max_age_days=1).state == "stale"


def test_history_keeps_every_run_locally(project: Path) -> None:
    _record(project)
    _record(project, verdict="fail")
    assert [e["verdict"] for e in ledger.history(project)] == ["pass", "fail"]
