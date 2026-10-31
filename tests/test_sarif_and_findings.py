from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from rung import findings
from rung.engines import sarif

FIXTURES = Path(__file__).parent / "fixtures" / "sarif"
REPO = Path("/REPO")


def _load(name: str) -> list[sarif.Finding]:
    return sarif.parse((FIXTURES / f"{name}.sarif").read_text(), REPO)


def test_gitleaks_secret_is_parsed_without_its_value() -> None:
    (leak,) = _load("gitleaks")
    assert (leak.tool, leak.rule, leak.path, leak.line) == ("gitleaks", "github-pat", "app.py", 2)
    assert "ghp_" not in leak.message


def test_osv_absolute_uris_become_relative() -> None:
    found = _load("osv")
    assert {f.path for f in found} == {"requirements.txt"}
    assert all(f.rule.startswith(("CVE-", "GHSA-", "PYSEC-")) for f in found)


def test_zizmor_levels_and_rules() -> None:
    found = _load("zizmor")
    assert any(f.rule == "zizmor/dangerous-triggers" and f.level == "error" for f in found)
    assert all(f.path == ".github/workflows/ci.yml" for f in found)


def test_missing_level_falls_back_to_the_default() -> None:
    doc = {"runs": [{"tool": {"driver": {"name": "t"}}, "results": [{"ruleId": "r", "message": {"text": "m"}}]}]}
    (f,) = sarif.parse(doc, REPO, default_level="error")
    assert f.level == "error"


def test_fingerprint_survives_a_line_shift() -> None:
    def doc(line: int) -> dict:
        return {"runs": [{"tool": {"driver": {"name": "t"}}, "results": [{
            "ruleId": "sql-injection", "level": "error", "message": {"text": "tainted query"},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": "db.py"},
                                                "region": {"startLine": line, "snippet": {"text": "cur.execute(q)"}}}}],
        }]}]}  # fmt: skip

    (before,) = sarif.parse(doc(10), REPO)
    (after,) = sarif.parse(doc(42), REPO)
    assert before.fingerprint == after.fingerprint and before.line != after.line


def test_diff_counts_duplicates() -> None:
    (leak,) = _load("gitleaks")
    introduced, resolved = sarif.diff([leak], [leak, leak])
    assert introduced == [leak] and resolved == []
    introduced, resolved = sarif.diff([leak, leak], [])
    assert introduced == [] and len(resolved) == 2


def test_triage_against_base_waivers_and_threshold() -> None:
    old, new_error, new_note = _load("zizmor")[:3]
    new_error = replace(new_error, level="error", fingerprint="a" * 16)
    new_note = replace(new_note, level="note", fingerprint="b" * 16)
    waived = replace(old, fingerprint="c" * 16)
    waivers = [findings.Waiver("w", "c" * 16, "accepted risk", "Dev", date(2099, 1, 1))]
    t = findings.triage("w", [old, new_error, new_note, waived], [old], waivers, fail_on="warning")
    assert t.blocking == [new_error]
    assert t.nonblocking == [new_note]
    assert t.waived == [waived]


def test_expired_waiver_stops_applying() -> None:
    (leak,) = _load("gitleaks")
    expired = findings.Waiver("s", leak.fingerprint, "temporary", "Dev", date(2020, 1, 1))
    t = findings.triage("s", [leak], None, [expired], fail_on="note", today=date(2026, 1, 1))
    assert t.blocking == [leak] and t.expired == [expired]


def test_waiver_and_baseline_round_trip(tmp_path: Path) -> None:
    w = findings.Waiver("secrets", "f" * 16, 'test fixture, "fake" key', "Dev", date(2030, 6, 1))
    findings.add_waiver(tmp_path, w)
    assert findings.load_waivers(tmp_path) == [w]
    found = _load("zizmor")
    findings.save_baseline(tmp_path, "workflows", found, "abc")
    assert sorted(findings.load_baseline(tmp_path, "workflows"), key=lambda f: f.fingerprint) == sorted(
        found, key=lambda f: f.fingerprint
    )


def test_waiver_needs_a_reason(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reason"):
        findings.add_waiver(tmp_path, findings.Waiver("s", "f" * 16, "  ", "Dev", date(2030, 1, 1)))


def test_dependency_findings_survive_manifest_edits_and_version_formatting() -> None:
    def doc(version: str, line_hash: str) -> dict:
        return {"runs": [{"tool": {"driver": {"name": "osv-scanner"}}, "results": [{
            "ruleId": "CVE-2019-10906", "level": "warning",
            "message": {"text": f"Package 'jinja2@{version}' is vulnerable to 'CVE-2019-10906'"},
            "partialFingerprints": {"primaryLocationLineHash": line_hash},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": "requirements.txt"}}}],
        }]}]}  # fmt: skip

    (before,) = sarif.parse(doc("2.10.0", "aaa"), REPO)
    (after,) = sarif.parse(doc("2.10", "bbb"), REPO)
    assert before.fingerprint == after.fingerprint
