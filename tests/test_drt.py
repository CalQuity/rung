from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rung.engines import drt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lease-queue"

# A stand-in for the Lean model: the *fixed* lease queue, written independently.
REFERENCE = """
import sys
for line in sys.stdin:
    owner, valid, done, result = None, False, False, None
    for tok in line.split():
        n = int(tok)
        if n == 0:
            valid = False
        elif n % 2 == 1:
            if not done and (owner is None or not valid):
                owner, valid = (n - 1) // 2, True
        else:
            w = (n - 2) // 2
            if owner == w and valid and not done:
                done, result = True, w
    o = "-" if owner is None else owner
    r = "-" if result is None else result
    print(f"owner={o} valid={int(valid)} done={int(done)} result={r}")
"""


def _reference(tmp_path: Path) -> drt.Side:
    script = tmp_path / "reference.py"
    script.write_text(REFERENCE)
    return drt.Side(f"{sys.executable} {script}", tmp_path)


def test_finds_and_shrinks_the_lease_bug(tmp_path: Path) -> None:
    impl = drt.Side(f"{sys.executable} -m leasequeue.drt", EXAMPLE)
    result = drt.run(_reference(tmp_path), impl, alphabet=["0", "1", "2", "3", "4"], cases=500, max_len=8, seed=7)
    assert result.verdict == "fail"
    assert result.minimal is not None
    # claim(w1), expire, complete(w1): a worker completes on its own expired lease.
    # The TLA+ invariant ResultFromLeaseHolder cannot see this case; the model can.
    assert result.minimal.input == "3 0 4"
    assert "done=1" in result.minimal.impl and "done=0" in result.minimal.model


def test_identical_sides_pass(tmp_path: Path) -> None:
    side = _reference(tmp_path)
    result = drt.run(side, side, alphabet=["0", "1", "2", "3", "4"], cases=200, seed=1)
    assert result.verdict == "pass"
    assert result.cases == 200


def test_wrong_line_count_is_an_error(tmp_path: Path) -> None:
    quiet = drt.Side(f"{sys.executable} -c 'import sys; sys.stdin.read()'", tmp_path)
    result = drt.run(_reference(tmp_path), quiet, alphabet=["0"], cases=5)
    assert result.verdict == "error"
    assert "printed 0 lines" in result.reason


def test_generation_is_deterministic() -> None:
    a = drt.generate(["0", "1"], 50, 0, 6, seed=3)
    assert a == drt.generate(["0", "1"], 50, 0, 6, seed=3)
    assert a[0] == ""  # the empty sequence is always tried first when min_len is 0


def test_finding_5_truncated_protocol_is_never_agreement(tmp_path: Path) -> None:
    for name, prefix in (("model", "MODEL"), ("impl", "WRONG")):
        (tmp_path / f"{name}.py").write_text(f"import sys\nfor line in sys.stdin: print({prefix!r} + 'x' * 2000000)\n")
    result = drt.run(
        drt.Side(f"{sys.executable} model.py", tmp_path),
        drt.Side(f"{sys.executable} impl.py", tmp_path),
        alphabet=["a"],
        cases=1,
        min_len=1,
    )
    assert result.verdict == "error" and "capture limit" in result.reason


@pytest.mark.parametrize(
    "options",
    [
        {"cases": 0, "min_len": 1},
        {"cases": 10, "max_len": 0},
        {"cases": 1},
        {"cases": 2, "min_len": -1},
        {"alphabet": [" "]},
        {"alphabet": [""]},
    ],
)
def test_finding_15_no_operation_is_not_conformance(tmp_path: Path, options) -> None:
    side = _reference(tmp_path)
    result = drt.run(side, side, **({"alphabet": ["0"]} | options))
    assert result.verdict == "error"


def test_finding_15_records_actual_operation_scope(tmp_path: Path) -> None:
    side = _reference(tmp_path)
    result = drt.run(side, side, alphabet=["0", "1"], cases=50)
    assert result.verdict == "pass" and result.operations == ["0", "1"] and max(result.lengths) > 0


def test_finding_20_stateful_divergence_keeps_the_observed_batch(tmp_path: Path) -> None:
    (tmp_path / "model.py").write_text("import sys\nfor line in sys.stdin: print('ok')\n")
    (tmp_path / "impl.py").write_text(
        "import sys\nfor i,line in enumerate(sys.stdin): print('ok' if i == 0 else 'WRONG')\n"
    )
    result = drt.run(
        drt.Side(f"{sys.executable} model.py", tmp_path),
        drt.Side(f"{sys.executable} impl.py", tmp_path),
        alphabet=["a"],
        cases=5,
    )
    assert result.verdict == "fail" and result.minimal is None
    assert len(result.context) == 5 and all(d.model != d.impl for d in result.examples)
    assert result.context_model == ["ok"] * 5
    assert result.context_impl == ["ok", "WRONG", "WRONG", "WRONG", "WRONG"]
