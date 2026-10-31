from __future__ import annotations

from rung.runners import tla

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
        "Error: Deadlock reached.\nError: The behavior up to this point is:\n"
        "State 1: <Initial predicate>\n/\\ x = 0\n"
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
