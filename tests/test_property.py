from __future__ import annotations

from pathlib import Path

import pytest

from rung.drafting import property as property_draft
from rung.engines import mutate

from .test_draft import ScriptedProvider

PRICING = '''\
def total(prices: list[int], discount: int) -> int:
    """The sum of the prices minus the discount, never below zero."""
    subtotal = sum(prices)
    if subtotal - discount < 0:
        return 0
    return subtotal - discount
'''

WEAK = """\
from hypothesis import given, settings, strategies as st
from pricing import total


@settings(max_examples=50, deadline=None)
@given(st.lists(st.integers(0, 100)), st.integers(0, 100))
def test_total_is_an_int(prices, discount):
    assert isinstance(total(prices, discount), int)
"""

STRONG = """\
from hypothesis import given, settings, strategies as st
from pricing import total


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(0, 100), max_size=5), st.integers(0, 300))
def test_total_is_the_discounted_sum_floored_at_zero(prices, discount):
    assert total(prices, discount) == max(sum(prices) - discount, 0)
"""

WRONG_LAW = STRONG.replace("max(sum(prices) - discount, 0)", "sum(prices) - discount")


def _reply(test: str) -> dict:
    return {"test": test, "notes": "", "laws": [{"id": "total-floored", "text": "A total is never negative."}]}


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "shop"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n'
    )
    (tmp_path / "pricing.py").write_text(PRICING)
    return tmp_path


def test_mutants_change_one_token_and_never_strings_or_comments(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text('x = a < b  # a < b\ny = "a < b"\n"""\nDocs: a or b.\n"""\nz = p and q\n')
    found = {m.mutated for m in mutate.mutants(src)}
    assert found == {"x = a <= b  # a < b", "z = p or q"}


def test_an_applied_mutant_is_always_restored(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text("x = a < b\r\n")
    (mutant,) = mutate.mutants(src)
    with pytest.raises(RuntimeError), mutate.applied(mutant):
        assert "<=" in src.read_text()
        raise RuntimeError
    assert src.read_bytes() == b"x = a < b\r\n"


def test_classify_separates_findings_from_broken_tests() -> None:
    assert property_draft.classify(0, "1 passed") == "pass"
    assert property_draft.classify(1, "Falsifying example: test_x(n=0)") == "finding"
    assert property_draft.classify(1, "Failing test case: test_x(n=0)\nAssertionError") == "finding"
    assert property_draft.classify(1, "Failing test case: test_x(n=0)\nNameError: name 'totl'") == "error"
    assert property_draft.classify(5, "no tests ran") == "error"


def test_a_property_no_mutant_can_fail_is_sent_back_and_a_strong_one_is_kept(pricing: Path) -> None:
    provider = ScriptedProvider([_reply(WEAK), _reply(STRONG)])
    result, target = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "pricing")
    assert result.ok and not result.trace, result.message
    assert result.rounds == 2
    assert "pass when the code is broken" in provider.prompts[-1]
    assert "caught" in result.message
    assert target.test_file.read_text() == STRONG
    assert (pricing / "pricing.py").read_text() == PRICING  # every mutant was restored


def test_a_property_that_fails_on_the_real_code_is_a_finding(pricing: Path) -> None:
    provider = ScriptedProvider([_reply(WRONG_LAW)])
    result, _ = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "pricing")
    assert result.ok and "test case" in result.trace.lower()
    assert result.rounds == 1  # never sent back to the model to make the failure go away


def test_a_draft_killed_mid_mutant_is_repaired_on_the_next_run(pricing: Path) -> None:
    from rung.core.errors import RungError

    src = pricing / "pricing.py"
    (mutant, *_) = mutate.mutants(src)
    context = mutate.applied(mutant, property_draft.backup_dir(pricing))
    context.__enter__()  # the process dies here: no __exit__, the mutant stays in the file
    assert src.read_text() != PRICING
    with pytest.raises(RungError, match=r"restored pricing\.py"):
        property_draft.draft(ScriptedProvider([]), pricing, [src], "pricing")
    assert src.read_text() == PRICING
