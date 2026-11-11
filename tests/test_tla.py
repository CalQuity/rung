from __future__ import annotations

from pathlib import Path

import pytest

from rung.checks import runner
from rung.core.config import load
from rung.engines import tla, tla_render

VIOLATION = """\
TLC2 Version 2.19
Computing initial states...
Error: Invariant ResultFromLeaseHolder is violated.
Error: The behavior up to this point is:
State 1: <Initial predicate>
/\\ owner = NoOwner
/\\ valid = FALSE
/\\ done = FALSE

State 2: <Claim(w1) line 30, col 5 to line 35, col 32 of module LeaseQueue>
/\\ owner = w1
/\\ valid = TRUE
/\\ done = FALSE

State 3: <Expire line 38, col 5 to line 40, col 50 of module LeaseQueue>
/\\ owner = w1
/\\ valid = FALSE
/\\ done = FALSE

State 4: <Complete line 44, col 5 to line 49, col 45 of module LeaseQueue>
/\\ owner = w1
/\\ valid = FALSE
/\\ done = TRUE

27 states generated, 15 distinct states found, 2 states left on queue.
"""

PASSED = """\
Model checking completed. No error has been found.
  Estimates of the probability that TLC did not check all reachable states
1,234 states generated, 456 distinct states found, 0 states left on queue.
"""


def test_parses_invariant_violation_and_trace() -> None:
    result = tla.parse_output(VIOLATION, returncode=12)
    assert result.verdict == "fail"
    assert result.violated == "ResultFromLeaseHolder"
    assert [s.action for s in result.trace] == [
        "Initial predicate",
        "Claim(w1)",
        "Expire",
        "Complete",
    ]
    assert result.trace[1].values["owner"] == "w1"
    assert result.distinct_states == 15


def test_parses_success_with_thousands_separators() -> None:
    result = tla.parse_output(PASSED, returncode=0)
    assert result.verdict == "pass"
    assert result.generated_states == 1234
    assert result.distinct_states == 456


def test_deadlock_is_a_failure() -> None:
    out = (
        "Error: Deadlock reached.\nError: The behavior up to this point is:\nState 1: <Initial predicate>\n/\\ x = 0\n"
    )
    result = tla.parse_output(out, returncode=11)
    assert result.verdict == "fail" and result.violated == "deadlock"


def test_parse_error_is_an_error_not_a_pass() -> None:
    result = tla.parse_output('*** Parse Error ***\nEncountered "==" at line 3', returncode=150)
    assert result.verdict == "error"
    assert "Parse Error" in result.reason


def test_multiline_values_are_joined() -> None:
    lines = ["State 1: <Initial predicate>", "/\\ f = [a |-> 1,", "     b |-> 2]", "/\\ g = 3"]
    (state,) = tla.parse_trace(lines)
    assert state.values == {"f": "[a |-> 1, b |-> 2]", "g": "3"}


def test_render_shows_only_changed_variables() -> None:
    trace = tla.parse_output(VIOLATION, returncode=12).trace
    text = tla.render_trace(trace)
    step3 = text.split("3. Expire")[1].split("4.")[0]
    assert "valid = FALSE" in step3 and "owner" not in step3


MODEL = """---- MODULE M ----
EXTENDS Naturals, TLC
VARIABLE x
Init == x = 0
Next == /\\ x < 3
        /\\ x' = x + 1
Safe == x < 2
Spec == Init /\\ [][Next]_x
====
"""


def _tlc_project(tmp_path: Path, model: str = MODEL, cfg: str = "SPECIFICATION Spec\nINVARIANT Safe\n"):
    if not tla.DEFAULT_JAR.exists():
        pytest.skip("cached TLC unavailable")
    (tmp_path / "M.tla").write_text(model)
    (tmp_path / "M.cfg").write_text(cfg)
    (tmp_path / "rung.toml").write_text('[[check]]\nid="m"\nkind="tla"\nspec="M.tla"\nconfig="M.cfg"\n')
    return load(tmp_path)


def test_finding_3_programmatic_tlc_exit_is_rejected(tmp_path: Path) -> None:
    model = MODEL.replace("x < 3", 'x < 3 /\\ IF x = 1 THEN TLCSet("exit", TRUE) ELSE TRUE')
    config = _tlc_project(tmp_path, model)
    result = runner.run_check(config, config.check("m"))
    assert result.verdict == "error" and "TLCSet" in result.outcome.summary
    control = tla.run(tmp_path / "M.tla", tmp_path / "M.cfg")
    assert control.verdict != "pass"
    (tmp_path / "M.tla").write_text(MODEL)
    assert tla.run(tmp_path / "M.tla", tmp_path / "M.cfg").verdict == "fail"


def test_finding_4_block_commented_invariants_check_nothing(tmp_path: Path) -> None:
    config = _tlc_project(tmp_path, cfg="SPECIFICATION Spec\n(* outer\n(* inner *)\nINVARIANT Safe\n*)\n")
    assert tla.declared_properties(tmp_path / "M.cfg")["invariants"] == []
    assert runner.run_check(config, config.check("m")).verdict == "fail"


def test_finding_10_external_module_resolution_fails_closed(tmp_path: Path, monkeypatch) -> None:
    config = _tlc_project(tmp_path)
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", f"-DTLA-Library={tmp_path.parent}")
    result = runner.run_check(config, config.check("m"))
    assert result.verdict == "error" and "TLA-Library" in result.outcome.summary


def test_finding_11_external_cfg_link_cannot_receive_a_pass(tmp_path: Path) -> None:
    config = _tlc_project(tmp_path, MODEL.replace("Safe == x < 2", "Safe == TRUE"))
    external = tmp_path.parent / f"{tmp_path.name}-external.cfg"
    external.write_text("SPECIFICATION Spec\nINVARIANT Safe\n")
    (tmp_path / "M.cfg").unlink()
    (tmp_path / "M.cfg").symlink_to(external)
    result = runner.run_check(config, config.check("m"))
    assert result.verdict == "error" and "external symlink" in result.outcome.summary


def test_finding_19_single_variable_and_infinite_trace_edges() -> None:
    trace = tla.parse_trace(
        ["State 1: <Initial predicate>", "x = 0", "", "State 2: <Step>", "x = 1", "", "State 3: Stuttering"]
    )
    assert trace[0].values == {"x": "0"} and trace[1].values == {"x": "1"}
    assert trace[-1].loop_to == 2 and "loop to state 2" in tla.render_trace(trace)
    assert "s2 --> s2 : Stuttering" in tla_render.counterexample_mermaid(trace, "Live", {})
    back = tla.parse_trace(["State 1: <Initial predicate>", "x = 0", "Back to state 1: <Step>"])
    assert back[-1].loop_to == 1


def test_finding_3_control_in_an_imported_module_is_rejected(tmp_path: Path) -> None:
    _tlc_project(tmp_path, MODEL.replace("Naturals, TLC", "Naturals, Control"))
    (tmp_path / "Control.tla").write_text('---- MODULE Control ----\nEXTENDS TLC\nStop == TLCSet("exit", TRUE)\n====\n')
    result = tla.run(tmp_path / "M.tla", tmp_path / "M.cfg")
    assert result.verdict == "error" and "TLCSet" in result.reason
