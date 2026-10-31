from __future__ import annotations

from pathlib import Path

from rung.engines import lean


def test_scan_flags_escape_hatches(tmp_path: Path) -> None:
    (tmp_path / "Bad.lean").write_text(
        "theorem a : 1 = 1 := by sorry\naxiom cheat : False\ntheorem b : 2 + 2 = 4 := by native_decide\n"
    )
    findings = lean.scan_sources(tmp_path)
    assert any("sorry" in f for f in findings)
    assert any("axiom declaration" in f for f in findings)
    assert any("native_decide" in f for f in findings)


def test_scan_ignores_comments_and_build_dirs(tmp_path: Path) -> None:
    (tmp_path / "Good.lean").write_text(
        "-- no sorry here\n/- a block comment mentioning axiom and sorry -/\ntheorem a : 1 = 1 := rfl\n"
    )
    (tmp_path / ".lake").mkdir()
    (tmp_path / ".lake" / "Dep.lean").write_text("theorem x : True := sorry\n")
    assert lean.scan_sources(tmp_path) == []


def test_scan_does_not_flag_identifiers_containing_keywords(tmp_path: Path) -> None:
    (tmp_path / "Names.lean").write_text("def sorryCount := 0\ndef axiomatic := 1\n")
    assert lean.scan_sources(tmp_path) == []


def test_parse_axioms() -> None:
    out = (
        "'LeaseModel.run_consistent' depends on axioms: [propext]\n"
        "'LeaseModel.cheat' depends on axioms: [sorryAx, propext]\n"
        "'LeaseModel.simple' does not depend on any axioms\n"
    )
    axioms = lean.parse_axioms(out)
    assert axioms["LeaseModel.run_consistent"] == ["propext"]
    assert "sorryAx" in axioms["LeaseModel.cheat"]
    assert axioms["LeaseModel.simple"] == []
    assert not set(axioms["LeaseModel.run_consistent"]) - lean.STANDARD_AXIOMS
