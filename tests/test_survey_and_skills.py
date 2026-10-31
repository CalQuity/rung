from __future__ import annotations

from pathlib import Path

from rung import plan, skills, survey

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lease-queue"


def test_survey_finds_formal_artifacts_and_stateful_code() -> None:
    info = survey.survey(EXAMPLE)
    assert info.primary_language == "Python"
    assert info.formal["tla"] == ["spec/tla/LeaseQueue.tla"]
    assert "spec/lean/LeaseModel.lean" in info.formal["lean"]
    assert any(c.path == "leasequeue/queue.py" for c in info.protocol_candidates)


def test_plan_always_ends_with_the_ledger_gate() -> None:
    recs = plan.plan(survey.survey(EXAMPLE))
    assert recs[-1].rung == "ledger"
    assert any(r.rung == "protocol" for r in recs)


def test_survey_skips_dependency_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("const state = 1\n" * 50)
    assert survey.survey(tmp_path).languages == {}


def test_skill_install_is_idempotent_and_keeps_existing_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# House rules\n\nBe kind.\n")
    skills.install(tmp_path)
    skills.install(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("# House rules")
    assert text.count(skills.BEGIN) == 1
    assert (tmp_path / ".claude" / "skills" / "rung" / "SKILL.md").exists()
