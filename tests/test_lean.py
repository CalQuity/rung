from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rung.core.errors import ToolMissing
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


def test_weak_theorems_are_flagged(tmp_path: Path) -> None:
    (tmp_path / "M.lean").write_text(
        "theorem nonneg (n : Nat) : 0 ≤ n := Nat.zero_le _\n\n"
        "theorem real (xs : List Nat) : (xs ++ []).length = xs.length := by\n  simp\n"
    )
    weak = lean.weak_theorems(tmp_path)
    assert len(weak) == 1 and weak[0].startswith("nonneg:")  # a one-line `simp` proof is not flagged


def test_toolchain_prefers_override_then_newest_release(monkeypatch) -> None:
    monkeypatch.setenv("RUNG_LEAN_TOOLCHAIN", "leanprover/lean4:v9.9.9")
    assert lean.toolchain() == "leanprover/lean4:v9.9.9"
    monkeypatch.delenv("RUNG_LEAN_TOOLCHAIN")

    class Listing:
        stdout = "leanprover/lean4:v4.15.0\nleanprover/lean4:v4.35.0 (default)\nleanprover/lean4:v4.36.0-rc2\n"

    monkeypatch.setattr(lean.subprocess, "run", lambda *a, **k: Listing())
    assert lean.toolchain() == "leanprover/lean4:v4.35.0"


def test_strings_mentioning_sorry_are_not_escape_hatches(tmp_path: Path) -> None:
    (tmp_path / "M.lean").write_text('def msg : String := "sorry, no axiom here"\n')
    assert lean.scan_sources(tmp_path) == []


def test_audit_imports_come_from_the_lakefile(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text('name = "m"\n[[lean_lib]]\nname = "Model"\n')
    assert lean.lake_libraries(tmp_path) == ["Model"]


def _real_project(tmp_path: Path, source: str) -> Path:
    """Use the installed pinned toolchain, as the example integration tests do."""
    (tmp_path / "lean-toolchain").write_text(lean.KNOWN_GOOD_TOOLCHAIN + "\n")
    try:
        available = subprocess.run([lean.find_lake(), "--version"], cwd=tmp_path, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired, ToolMissing):
        pytest.skip("Lean toolchain unavailable")
    if available.returncode:
        pytest.skip("Lean toolchain unavailable")
    (tmp_path / "lakefile.toml").write_text('name="audit"\ndefaultTargets=["Model"]\n[[lean_lib]]\nname="Model"\n')
    (tmp_path / "Model.lean").write_text(source)
    return tmp_path


def test_finding_1_initializer_cannot_spoof_a_missing_theorem(tmp_path: Path) -> None:
    project = _real_project(tmp_path, "initialize IO.println \"'missing' does not depend on any axioms\"\n")
    result = lean.run(project, ["missing"])
    assert result.verdict != "pass" and "not a theorem" in result.reason


def test_finding_1_duplicate_axiom_reports_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        lean.parse_axioms("'t' depends on axioms: [bad]\n 't' does not depend on any axioms")


def test_finding_2_strings_nested_comments_and_declaration_modifiers(tmp_path: Path) -> None:
    (tmp_path / "Model.lean").write_text(
        'def a := "/-"\nprivate unsafe def wrong : Nat := 1\n'
        '@[inline, extern "wrong"] def checked : Nat := 0\ndef b := "-/"\n'
        "/- outer /- nested -/ sorry -/\ntheorem t : checked = 0 := rfl\n"
    )
    findings = lean.scan_sources(tmp_path)
    assert any("unsafe definition" in f for f in findings)
    assert any("extern" in f for f in findings)
    assert not any("sorry" in f for f in findings)


@pytest.mark.parametrize("source,names", [("", []), ("def safety : Nat := 0\n", ["safety"])])
def test_finding_12_a_build_or_definition_is_not_a_proof(tmp_path: Path, source, names) -> None:
    result = lean.run(_real_project(tmp_path, source), names)
    assert result.verdict != "pass"


def test_real_audit_accepts_only_the_selected_theorem(tmp_path: Path) -> None:
    result = lean.run(_real_project(tmp_path, "theorem safe (n : Nat) : n + 0 = n := by simp\n"), ["safe"])
    assert result.verdict == "pass" and result.axioms == {"safe": []}


def test_finding_18_lean_timeout_kills_compiler_children(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "survived"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
    launcher = tmp_path / "lake"
    launcher.write_text(
        f"#!{sys.executable}\nimport subprocess,time\n"
        f"subprocess.Popen([{sys.executable!r}, '-c', {child!r}])\ntime.sleep(10)\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setattr(lean, "find_lake", lambda: str(launcher))
    with pytest.raises(subprocess.TimeoutExpired):
        lean.run(tmp_path, [], timeout=0.2)
    time.sleep(1.2)
    assert not marker.exists()


def test_finding_1_unsuccessful_audit_cannot_supply_proof_facts(tmp_path: Path, monkeypatch) -> None:
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(
            [], 1, json.dumps([{"name": "t", "kind": "theorem", "isProp": True, "axioms": []}]), "unknown constant"
        )

    monkeypatch.setattr(lean, "run_process", failed)
    monkeypatch.setattr(lean, "find_lean", lambda project, **kwargs: "/trusted/bin/lean")
    with pytest.raises(ValueError, match="audit failed"):
        lean.audit_axioms("lake", tmp_path, [], ["t"])


def test_finding_2_raw_strings_characters_and_quoted_names_do_not_hide_code(tmp_path: Path) -> None:
    (tmp_path / "M.lean").write_text(
        'def raw := r#"text with " and /-"#\n'
        "def char := '\"'\n"
        "def «/-» := 1\n"
        "private unsafe def bad : Nat := 1\n"
        'def endMarker := "-/"\n'
    )
    assert any("unsafe definition" in f for f in lean.scan_sources(tmp_path))


def test_finding_1_project_cannot_shadow_the_inspectors_lean_import(tmp_path: Path) -> None:
    project = _real_project(tmp_path, "theorem safe : 0 = 0 := rfl\n")
    assert lean.run(project, ["safe"]).verdict == "pass"
    shadow = project / "Lean.lean"
    shadow.write_text(
        'import Init\ninitialize IO.println "[{\\"name\\":\\"missing\\",\\"kind\\":\\"theorem\\",'
        '\\"isProp\\":true,\\"axioms\\":[]}]"\ninitialize IO.Process.exit 0\n'
    )
    compiler = lean.find_lean(project)
    compiled = subprocess.run(
        [compiler, "-o", "Lean.olean", "Lean.lean"], cwd=project, capture_output=True, text=True, timeout=30
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    with pytest.raises(ValueError, match="not a theorem"):
        lean.audit_axioms(lean.find_lake(), project, ["Model"], ["missing"])


def test_finding_17_audit_path_cannot_follow_a_prepared_external_link(tmp_path: Path) -> None:
    from rung.core.config import ConfigError

    project = tmp_path / "model"
    (project / ".lake").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    (project / ".lake" / "rung_audit.lean").symlink_to(outside)
    with pytest.raises(ConfigError):
        lean.audit_axioms("unused", project, [], ["safe"])
    assert outside.read_text() == "original"
