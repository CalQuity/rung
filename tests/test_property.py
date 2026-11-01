from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_generics_and_arrows_are_never_mutated(tmp_path: Path) -> None:
    src = tmp_path / "g.ts"
    src.write_text("const id = <T>(v: T): Array<T> => [v]\nconst ok = a < b\n")
    assert {m.mutated for m in mutate.mutants(src)} == {"const ok = a <= b"}


def test_an_applied_mutant_is_always_restored(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text("x = a < b\r\n")
    (mutant,) = mutate.mutants(src)
    with pytest.raises(RuntimeError), mutate.applied(mutant):
        assert "<=" in src.read_text()
        raise RuntimeError
    assert src.read_bytes() == b"x = a < b\r\n"


def test_classify_separates_findings_from_broken_tests() -> None:
    src, test = ["pricing.py"], "test_p.py"
    c = property_draft.classify
    assert c(0, "1 passed in 0.1s", src, test) == "pass"
    assert c(0, "1 skipped in 0.1s", src, test) == "error"  # nothing ran
    assert c(0, "\u2139 tests 0\n\u2139 pass 0", src, test) == "error"
    assert c(1, "Falsifying example: test_x(n=0)\ntest_p.py:9: AssertionError", src, test) == "finding"
    # A NameError raised in the code under test is a bug in the code, not a broken test.
    assert c(1, "Failing test case: test_x(n=0)\npricing.py:3: NameError", src, test) == "finding"
    assert c(1, "Failing test case: test_x(n=0)\ntest_p.py:7: NameError", src, test) == "error"
    failed = "Error: Property failed after 1 tests\n"
    broken = failed + "TypeError: x.missingMethod is not a function\n    at fn (tests/p.test.ts:5:7)"
    assert c(1, broken, ["pricing.ts"], "p.test.ts") == "error"
    assert c(1, failed + "    at total (src/pricing.ts:4:9)", ["pricing.ts"], "p.test.ts") == "finding"
    assert c(2, "ERROR collecting", src, test) == "error"
    # A plain assert the model wrote, failing on the real code, is a finding too (no generated example).
    assert c(1, "E       assert -1 == 0\ntest_p.py:12: AssertionError\n1 failed", src, test) == "finding"
    # A mutant that stops the module compiling is not a caught bug.
    assert c(1, "Transform failed with 1 error:\nsrc/pricing.ts:3:10", ["pricing.ts"], "p.test.ts") == "error"


def test_a_property_no_mutant_can_fail_is_sent_back_and_a_strong_one_is_kept(pricing: Path) -> None:
    provider = ScriptedProvider([_reply(WEAK), _reply(STRONG)])
    result, target = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "pricing")
    assert result.ok and not result.trace, result.message
    assert result.rounds == 2
    assert "pass when the code is broken" in provider.prompts[-1]
    assert "caught" in result.message
    assert (pricing / target.test_file).read_text() == STRONG


def test_the_developers_files_are_never_mutated_and_existing_tests_never_overwritten(pricing: Path) -> None:
    from rung.core.config import ConfigError

    seen: list[str] = []

    class Watching(ScriptedProvider):
        def complete_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
            seen.append((pricing / "pricing.py").read_text())
            return super().complete_json(prompt, **kwargs)

    provider = Watching([_reply(WEAK), _reply(STRONG)])
    result, _ = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "p")
    assert result.ok
    assert set(seen) == {PRICING} and (pricing / "pricing.py").read_text() == PRICING
    with pytest.raises(ConfigError, match="already exists"):
        property_draft.draft(ScriptedProvider([]), pricing, [pricing / "pricing.py"], "p")


def test_a_mutant_that_breaks_the_import_is_not_counted_as_caught(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "m"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n')
    (tmp_path / "flags.py").write_text("READY = True\nif READY:\n    def f(x):\n        return x\n")
    weak = (
        "from hypothesis import given, settings, strategies as st\nfrom flags import f\n\n\n"
        "@settings(max_examples=20, deadline=None)\n@given(st.integers())\ndef test_f_is_callable(x):\n"
        "    assert callable(f)\n"
    )
    result, _ = property_draft.draft(ScriptedProvider([_reply(weak)]), tmp_path, [tmp_path / "flags.py"], "flags")
    assert "INCONCLUSIVE" in result.message
    assert any("not counted" in w for w in result.warnings)


def test_a_failed_draft_is_kept_aside_not_written_into_the_tests(pricing: Path) -> None:
    provider = ScriptedProvider([_reply(WEAK)] * 2)
    result, target = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "pricing", max_rounds=2)
    assert not result.ok
    assert not (pricing / target.test_file).exists()
    assert result.files and ".rung/cache/drafts" in result.files[0].as_posix()


def test_a_property_that_fails_on_the_real_code_is_a_finding(pricing: Path) -> None:
    provider = ScriptedProvider([_reply(WRONG_LAW)])
    result, _ = property_draft.draft(provider, pricing, [pricing / "pricing.py"], "pricing")
    assert result.ok and "test case" in result.trace.lower()
    assert result.rounds == 1  # never sent back to the model to make the failure go away


@pytest.mark.parametrize(
    ("script", "deps", "expected"),
    [
        ("vitest run", {"vitest": "1"}, ["npx", "vitest", "run", "p.test.ts"]),
        ("jest", {"jest": "29"}, ["npx", "jest", "p.test.ts"]),
        ("node --import tsx --test tests/*.test.ts", {}, ["node", "--import", "tsx", "--test", "p.test.ts"]),
    ],
)
def test_typescript_properties_run_under_the_projects_own_runner(script: str, deps: dict, expected: list[str]) -> None:
    assert property_draft._js_runner({"scripts": {"test": script}}, deps, "p.test.ts") == expected
