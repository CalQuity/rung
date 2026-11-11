"""End-to-end run of the lease-queue example with the real TLC and Lean.

Skipped when Java, tla2tools.jar or Lean are not installed; CI installs them.
The example ships with its bug, so the expected verdicts are asymmetric: the
protocol check and the differential test fail, the Lean proofs pass. A patched
copy must pass all three.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rung.checks import runner
from rung.checks.base import Outcome
from rung.core import config
from rung.core.errors import ToolMissing
from rung.engines import lean, tla

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lease-queue"
BUGGY_GUARD = "if worker in job.claimed_by and not job.done:"
FIXED_GUARD = "if job.owner == worker and job.lease_valid and not job.done:"


def _tools_available() -> bool:
    try:
        tla.find_java()
        tla.find_jar()
        lean.find_lake()
    except ToolMissing:
        return False
    return True


pytestmark = pytest.mark.skipif(not _tools_available(), reason="needs java, tla2tools.jar and lean")


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "lease-queue"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns(".lake", ".rung", "__pycache__"))
    return dest


def _verdicts(root: Path) -> dict[str, Outcome]:
    cfg = config.load(root)
    return {c.id: runner.run_check(cfg, c).outcome for c in cfg.checks}


def test_buggy_queue_fails_where_the_readme_says(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    outcomes = _verdicts(root)
    proofs = outcomes["lease-model-proofs"]
    assert proofs.verdict == "pass", (proofs.summary, proofs.lines)
    assert outcomes["lease-protocol"].verdict == "fail"
    assert "ResultFromLeaseHolder" in outcomes["lease-protocol"].summary
    assert outcomes["impl-matches-model"].verdict == "fail"


def test_fixed_queue_passes_every_check(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    queue = root / "leasequeue" / "queue.py"
    queue.write_text(queue.read_text().replace(BUGGY_GUARD, FIXED_GUARD))
    tla_spec = root / "spec" / "tla" / "LeaseQueue.tla"
    tla_spec.write_text(
        tla_spec.read_text().replace(
            "    /\\ w \\in claimedBy\n    /\\ ~done\n    /\\ done' = TRUE",
            "    /\\ owner = w\n    /\\ valid\n    /\\ ~done\n    /\\ done' = TRUE",
        )
    )
    outcomes = _verdicts(root)
    assert {k: o.verdict for k, o in outcomes.items()} == {
        "lease-protocol": "pass",
        "lease-model-proofs": "pass",
        "impl-matches-model": "pass",
    }
