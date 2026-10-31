from __future__ import annotations

from pathlib import Path

import pytest

from rung import draft
from rung.config import LLMConfig
from rung.engines import tla
from rung.providers import Provider


class ScriptedProvider(Provider):
    """Returns canned replies in order and records every prompt it was sent."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(LLMConfig(provider="none"))
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


SPEC_REPLY = """Here you go.
```tla file=Q.tla
---- MODULE Q ----
====
```
```cfg file=Q.cfg
SPECIFICATION Spec
```
"""


def test_parse_files_reads_fenced_blocks() -> None:
    files = draft.parse_files(SPEC_REPLY)
    assert set(files) == {"Q.tla", "Q.cfg"}
    assert files["Q.cfg"] == "SPECIFICATION Spec\n"


def test_refuses_paths_outside_the_output_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        draft.safe_write(tmp_path, {"../escape.tla": "x"})


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "q.py"
    src.write_text("def claim():\n    pass\n")
    return src


def test_counterexample_stops_the_draft_for_a_person(tmp_path: Path, monkeypatch) -> None:
    trace = [
        tla.TraceState(1, "Initial predicate", {"x": "0"}),
        tla.TraceState(2, "Inc", {"x": "1"}),
    ]
    monkeypatch.setattr(
        tla,
        "run",
        lambda *a, **k: tla.TLCResult("fail", "invariant Safe is violated", "Safe", trace),
    )
    provider = ScriptedProvider([SPEC_REPLY, "should never be asked"])
    result = draft.draft_tla(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.ok and "real bug, or wrong spec" in result.message
    assert "2. Inc" in result.trace
    assert len(provider.prompts) == 1  # the model is not asked to "fix" a finding


def test_checker_errors_are_fed_back_for_repair(tmp_path: Path, monkeypatch) -> None:
    results = iter(
        [
            tla.TLCResult("error", "Parse Error at line 3", output="*** Parse Error ***"),
            tla.TLCResult("pass", "ok", distinct_states=12),
        ]
    )
    monkeypatch.setattr(tla, "run", lambda *a, **k: next(results))
    provider = ScriptedProvider([SPEC_REPLY, SPEC_REPLY])
    result = draft.draft_tla(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.ok and result.rounds == 2
    assert "Parse Error" in provider.prompts[1]


def test_vacuous_spec_is_sent_back(tmp_path: Path, monkeypatch) -> None:
    results = iter(
        [
            tla.TLCResult("pass", "ok", distinct_states=1),
            tla.TLCResult("pass", "ok", distinct_states=9),
        ]
    )
    monkeypatch.setattr(tla, "run", lambda *a, **k: next(results))
    provider = ScriptedProvider([SPEC_REPLY, SPEC_REPLY])
    result = draft.draft_tla(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.rounds == 2
    assert "vacuous" in provider.prompts[1]


DRIVE_OK = """```python file=drive.py
import os, pathlib
out = pathlib.Path(os.environ["RUNG_EVIDENCE_DIR"])
(out / "transcript.txt").write_text("$ app --version\\napp 1.0\\n")
```
```markdown file=FEATURES.md
# Features
## version
- How a user reaches it: `app --version`
```
SOURCES: src/**/*.py
"""
DRIVE_NO_EVIDENCE = DRIVE_OK.replace('(out / "transcript.txt").write_text("$ app --version\\napp 1.0\\n")', "pass")


def test_runtime_draft_registers_only_after_the_script_proves_itself(tmp_path: Path) -> None:
    from rung import runtime, survey

    provider = ScriptedProvider([DRIVE_NO_EVIDENCE, DRIVE_OK])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[])
    assert result.ok and result.rounds == 2
    assert result.covers == ["src/**/*.py"]
    assert any(e.endswith("transcript.txt") for e in result.evidence)
    assert "recorded no evidence" in provider.prompts[1]  # the failure was fed back for repair
    assert (tmp_path / ".rung" / "verify" / "FEATURES.md").exists()


def test_runtime_draft_gives_up_without_registering(tmp_path: Path) -> None:
    from rung import runtime, survey

    provider = ScriptedProvider([DRIVE_NO_EVIDENCE] * 2)
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert not result.ok and result.rounds == 2
