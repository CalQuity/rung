"""Saved diagnostic metrics are scoped, escaped and separate from health scoring."""

from __future__ import annotations

from rung.analysis import diagnostics, score
from rung.core import config
from rung.engines import score_page


def test_saved_changed_line_metrics_do_not_change_score_and_stale_inputs_stay_historical(project):
    cfg = config.load(project)
    before = score.build(cfg, ran_checks=False)
    session = diagnostics.Session(cfg, cfg.check("unit"), "coverage", {"base": "pinned"})
    session.path("report.json").write_text('{"files": {}}')
    session.finish(
        {
            "state": "complete",
            "exit_code": 0,
            "summary": "1 of 2 changed lines exercised <script>bad</script>",
            "errors": [],
            "metrics": {"covered": 1, "total": 2, "percent": 50.0, "unmeasured_files": 0},
            "files": [{"path": "src/core.py", "missing_lines": [3], "unmeasured": False}],
        }
    )
    data = score.build(cfg, ran_checks=False)
    assert data["score"] == before["score"]
    assert data["diagnostics"][0]["current"] is True
    assert data["assessments"]["line_coverage"] == "changed_lines_measured"
    html = score_page.render(data)
    assert "Changed-line coverage" in html and "50.00%" in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html and "<script>bad</script>" not in html
    assert "not whole-project coverage" in html
    (project / "src/core.py").write_text("value = 99\n")
    stale = score.build(cfg, ran_checks=False)
    assert stale["assessments"]["line_coverage"] == "not_assessed"
    assert stale["diagnostics"][0]["current"] is False
    assert "Historical" in score_page.render(stale)


def test_html_shows_attempt_matrix_without_relabeling_flaky_as_passed(project):
    cfg = config.load(project)
    session = diagnostics.Session(cfg, cfg.check("unit"), "flaky", {"attempts": 3})
    session.finish(
        {
            "state": "complete",
            "exit_code": 0,
            "summary": "Intermittent failure observed",
            "errors": [],
            "warnings": ["Repeated contexts are compared as groups <example>"],
            "attempts": [{"index": 1, "state": "complete", "exit_code": 1, "duration_seconds": 1.234}],
            "metrics": {
                "attempts_requested": 3,
                "attempts_completed": 3,
                "flaky_cases": 1,
                "consistent_failures": 0,
                "incomplete_cases": 0,
                "cases": 1,
            },
            "cases": [
                {
                    "id": "test_example.py::test_x[one]",
                    "classification": "intermittent",
                    "identity_scope": "subtest_group",
                    "observation_counts": [2, 2, 2],
                    "outcomes": [
                        {"attempt": 1, "state": "passed"},
                        {"attempt": 2, "state": "failed"},
                        {"attempt": 3, "state": "passed"},
                    ],
                }
            ],
        }
    )
    data = score.build(cfg, ran_checks=False)
    html = score_page.render(data)
    assert "Repeated-test diagnosis" in html and "Intermittent failure observed" in html
    assert "test_example.py::test_x[one]" in html
    assert "does not prove" in html
    assert "Subtest group" in html and "Observations per attempt: 2, 2, 2" in html
    assert "Repeated contexts are compared as groups &lt;example&gt;" in html
    assert "Duration (seconds)" in html and "1.23" in html
    assert data["stats"]["never"] == 1 and data["score"]["value"] == 0
