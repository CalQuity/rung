from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rung.analysis import survey
from rung.core.config import LLMConfig
from rung.drafting import common, runtime
from rung.drafting import lean as lean_draft
from rung.drafting import tla as tla_draft
from rung.drafting.providers import Provider, ProviderError, check_shape
from rung.engines import drt, lean, tla


class ScriptedProvider(Provider):
    """Returns canned JSON replies in order and records every prompt and system prompt it was sent."""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(LLMConfig(provider="none"))
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def complete_json(self, prompt: str, *, schema: dict[str, Any], system: str = "", **_: object) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.systems.append(system)
        reply = self.replies.pop(0)
        check_shape(reply, schema)
        return reply


def _files(**files: str) -> list[dict[str, str]]:
    return [{"path": path, "content": content} for path, content in files.items()]


SPEC_REPLY = {
    "files": _files(**{"Q.tla": "---- MODULE Q ----\n====", "Q.cfg": "SPECIFICATION Spec"}),
    "laws": [{"id": "one-owner", "text": "A job has at most one owner."}],
    "notes": "",
}


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "q.py"
    src.write_text("def claim():\n    pass\n")
    return src


# --- shared pieces ------------------------------------------------------------------------------


def test_schemas_are_strict_and_checked() -> None:
    assert tla_draft.SCHEMA["additionalProperties"] is False
    assert set(tla_draft.SCHEMA["required"]) == set(tla_draft.SCHEMA["properties"])
    with pytest.raises(ProviderError, match="missing `notes`"):
        check_shape({"files": [], "laws": []}, tla_draft.SCHEMA)
    with pytest.raises(ProviderError, match=r"\$\.files\[0\]\.content: expected a string"):
        check_shape({"files": [{"path": "a", "content": 1}], "laws": [], "notes": ""}, tla_draft.SCHEMA)


def test_files_of_and_safe_write(tmp_path: Path) -> None:
    assert common.files_of(SPEC_REPLY)["Q.cfg"] == "SPECIFICATION Spec\n"
    with pytest.raises(ValueError, match="outside"):
        common.safe_write(tmp_path, {"../escape.tla": "x"})


def test_tla_normalizer_fixes_unicode_and_fences() -> None:
    fixed = tla_draft.normalize("```tla\nInit == x ∈ S ∧ y ≠ 1\n```\n")
    assert fixed == "Init == x \\in S /\\ y # 1\n"


# --- TLA+ ---------------------------------------------------------------------------------------


def test_counterexample_stops_the_draft_for_a_person(tmp_path: Path, monkeypatch) -> None:
    trace = [tla.TraceState(1, "Initial predicate", {"x": "0"}), tla.TraceState(2, "Inc", {"x": "1"})]
    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("fail", "invariant Safe is violated", "Safe", trace))
    provider = ScriptedProvider([SPEC_REPLY])
    result = tla_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.ok and "real bug, or wrong spec" in result.message
    assert "2. Inc" in result.trace
    assert len(provider.prompts) == 1  # the model is never asked to "fix" a finding


def test_tla_checker_errors_are_fed_back_for_repair(tmp_path: Path, monkeypatch) -> None:
    results = iter([
        tla.TLCResult("error", "Parse Error at line 3", output="*** Parse Error ***"),
        tla.TLCResult("pass", "ok", distinct_states=12),
    ])  # fmt: skip
    monkeypatch.setattr(tla, "run", lambda *a, **k: next(results))
    provider = ScriptedProvider([SPEC_REPLY, SPEC_REPLY])
    result = tla_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.ok and result.rounds == 2
    assert "Parse Error" in provider.prompts[1]
    assert "Writing TLA+ that mirrors code" in provider.systems[0]  # the guide is the system prompt


def test_vacuous_spec_is_sent_back(tmp_path: Path, monkeypatch) -> None:
    results = iter([tla.TLCResult("pass", "ok", distinct_states=1), tla.TLCResult("pass", "ok", distinct_states=9)])
    monkeypatch.setattr(tla, "run", lambda *a, **k: next(results))
    provider = ScriptedProvider([SPEC_REPLY, SPEC_REPLY])
    result = tla_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.rounds == 2 and "vacuous" in provider.prompts[1]


# --- Lean ---------------------------------------------------------------------------------------


def _lean_reply(theorems: list[str]) -> dict[str, Any]:
    files = _files(**{
        "lakefile.toml": 'name = "m"\n[[lean_lib]]\nname = "M"\n[[lean_exe]]\nname = "m"\nroot = "Main"',
        "M.lean": "def x := 1", "Main.lean": "def main : IO Unit := pure ()", "drt_adapter.py": "print()",
        "lean-toolchain": "leanprover/lean4:v4.0.0",
    })  # fmt: skip
    return {"files": files, "theorems": theorems, "alphabet": ["0", "1"], "exe": "m", "laws": [], "notes": ""}


def _lean_ok(warnings: list[str]) -> lean.LeanResult:
    return lean.LeanResult("pass", "1 theorem(s) kernel-checked", warnings=warnings)


@pytest.fixture
def pinned(monkeypatch) -> None:
    monkeypatch.setattr(lean, "toolchain", lambda: "leanprover/lean4:v4.35.0")


def test_lean_draft_pins_the_toolchain_and_retries_trivial_theorems_once(tmp_path: Path, monkeypatch, pinned) -> None:
    results = iter([_lean_ok(["M.t: proved by `rfl`; it may say nothing about the model"]), _lean_ok([])])
    monkeypatch.setattr(lean, "run", lambda *a, **k: next(results))
    monkeypatch.setattr(lean_draft, "_drt_check", lambda *a, **k: drt.DRTResult("pass", "ok", cases=300))
    provider = ScriptedProvider([_lean_reply(["M.t"]), _lean_reply(["M.u"])])
    result = lean_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "model")
    assert result.ok and result.rounds == 2 and result.theorems == ["M.u"]
    assert "may prove nothing" in provider.prompts[1]
    assert (tmp_path / "model" / "lean-toolchain").read_text() == "leanprover/lean4:v4.35.0\n"
    assert "v4.35.0" in provider.systems[0]


def test_lean_draft_reports_a_divergence_instead_of_fixing_it(tmp_path: Path, monkeypatch, pinned) -> None:
    monkeypatch.setattr(lean, "run", lambda *a, **k: _lean_ok([]))
    divergence = drt.Divergence("1 0", "done=0", "done=1")
    monkeypatch.setattr(lean_draft, "_drt_check", lambda *a, **k: drt.DRTResult(
        "fail", "3 of 300 inputs diverge", cases=300, divergences=3, minimal=divergence))  # fmt: skip
    provider = ScriptedProvider([_lean_reply(["M.u"])])
    result = lean_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "model")
    assert result.ok and "disagree" in result.message
    assert "model: done=0" in result.trace and len(provider.prompts) == 1


def test_lean_draft_sends_adapter_errors_back(tmp_path: Path, monkeypatch, pinned) -> None:
    monkeypatch.setattr(lean, "run", lambda *a, **k: _lean_ok([]))
    checks = iter([drt.DRTResult("error", "printed 0 lines for 300 inputs"), drt.DRTResult("pass", "ok", cases=300)])
    monkeypatch.setattr(lean_draft, "_drt_check", lambda *a, **k: next(checks))
    provider = ScriptedProvider([_lean_reply(["M.u"]), _lean_reply(["M.u"])])
    result = lean_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "model")
    assert result.ok and result.rounds == 2 and "printed 0 lines" in provider.prompts[1]


# --- runtime ------------------------------------------------------------------------------------

# A drive script that meets the contract: seeded, one scenario per category, and a result.json. `FAIL`
# names scenarios that fail (an app bug), `FLAKY` makes one assertion's outcome depend on the clock.
DRIVE_SOURCE = """import json, os, pathlib, sys, time
out = pathlib.Path(os.environ["RUNG_EVIDENCE_DIR"])
seed = int(os.environ["RUNG_SEED"])
faults, only = os.environ.get("RUNG_FAULTS"), os.environ.get("RUNG_SCENARIOS")
FAIL, FLAKY, CATS = {fail}, {flaky}, {cats}
(out / "transcript.txt").write_text("$ app --version\\napp 1.0\\n")
scenarios = []
for cat in CATS:
    ok = cat not in FAIL and not (FLAKY and cat == "input" and time.time_ns() % 2)
    scenarios.append({{"name": cat, "category": cat, "faults": [], "passed": ok,
                      "assertions": [{{"name": cat + " holds", "passed": ok}}]}})
result = {{"seed": seed, "passed": all(s["passed"] for s in scenarios), "scenarios": scenarios,
          "not_applicable": {{"auth": "the app has no sign-in"}}}}
(out / "result.json").write_text(json.dumps(result))
sys.exit(0 if result["passed"] else 1)
"""
ALL_CATEGORIES = ["happy", "input", "state", "fault", "robustness"]


def _drive(fail: tuple[str, ...] = (), flaky: bool = False, cats: list[str] = ALL_CATEGORIES) -> dict[str, Any]:
    source = DRIVE_SOURCE.format(fail=repr(set(fail)), flaky=flaky, cats=repr(cats))
    return {
        "files": _files(**{"drive.py": source,
                           "FEATURES.md": "# Features\n## version\n- How a user reaches it: `app --version`"}),
        "sources": ["src/**/*.py"],
        "requirements": [],
        "notes": "",
    }  # fmt: skip


DRIVE_OK = _drive()
DRIVE_NO_EVIDENCE = {**DRIVE_OK, "files": _files(**{"drive.py": "pass", "FEATURES.md": "# Features"})}


def test_runtime_draft_registers_only_after_the_script_proves_itself(tmp_path: Path) -> None:
    provider = ScriptedProvider([DRIVE_NO_EVIDENCE, DRIVE_OK])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[])
    assert result.ok and result.rounds == 2
    assert result.covers == ["src/**/*.py"]
    assert any(e.endswith("transcript.txt") for e in result.evidence)
    assert "recorded no evidence" in provider.prompts[1]
    assert (tmp_path / ".rung" / "verify" / "FEATURES.md").exists()


def test_runtime_draft_gives_up_without_registering(tmp_path: Path) -> None:
    provider = ScriptedProvider([DRIVE_NO_EVIDENCE] * 2)
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert not result.ok and result.rounds == 2


def test_runtime_run_command_isolates_declared_packages(monkeypatch) -> None:
    assert runtime.run_command([]) == "python3 .rung/verify/drive.py"
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/uv")
    assert runtime.run_command(["playwright"]) == "uv run --no-project --with playwright python .rung/verify/drive.py"


def test_runtime_draft_can_require_a_video(tmp_path: Path) -> None:
    provider = ScriptedProvider([DRIVE_OK, DRIVE_OK])
    result = runtime.draft_runtime(
        provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2, require=(".webm",)
    )
    assert not result.ok  # a transcript alone does not satisfy a required video
    assert ".webm" in provider.prompts[0] and "required evidence missing: .webm" in provider.prompts[1]


def test_drafts_carry_proposed_laws_that_are_never_duplicated(tmp_path: Path, monkeypatch) -> None:
    from rung.core import laws

    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("pass", "ok", distinct_states=9))
    result = tla_draft.draft(ScriptedProvider([SPEC_REPLY]), tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert laws.propose(tmp_path, result.laws, ["queue-tla"]) == ["one-owner"]
    assert laws.propose(tmp_path, result.laws, ["queue-tla"]) == []
    (law,) = laws.load(tmp_path)
    assert (law.status, law.enforced_by) == ("proposed", ["queue-tla"])


def test_runtime_context_reaches_sub_projects(tmp_path: Path) -> None:
    for i in range(500):
        (tmp_path / "aaa_demo").mkdir(exist_ok=True)
        (tmp_path / "aaa_demo" / f"f{i:03}.json").write_text("{}")
    app = tmp_path / "frontend" / "src" / "app"
    app.mkdir(parents=True)
    (tmp_path / "frontend" / "package.json").write_text('{"dependencies": {"next": "16"}}')
    (app / "page.tsx").write_text("export default function Home() { return <h1>Books</h1> }")
    text = runtime.gather_context(tmp_path, survey.survey(tmp_path), ["frontend/package.json"])
    assert "--- frontend/src/app/page.tsx ---" in text and "<h1>Books</h1>" in text
    assert text.index("frontend/src/app/page.tsx") < text.index("aaa_demo/f000.json")


def test_sec2_runtime_requirements_are_vetted_before_anything_is_installed(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from rung.checks import deps

    ages = {"playwright": 900}
    monkeypatch.setattr(deps, "first_published", lambda pkg: (
        datetime.now(UTC) - timedelta(days=ages[pkg.name]) if pkg.name in ages else None))  # fmt: skip
    assert runtime.vet_requirements(["playwright>=1.40"]) == []
    assert runtime.vet_requirements(["playwrite-pro"]) == ["playwrite-pro does not exist on PyPI"]
    bad = {**DRIVE_OK, "requirements": ["playwrite-pro"]}
    provider = ScriptedProvider([bad, DRIVE_OK])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.rounds == 2 and "will not install" in provider.prompts[1]


def test_claude_cli_drafts_without_tools_or_mcp_servers(monkeypatch) -> None:
    import json
    import subprocess

    from rung.drafting import providers

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        envelope = {"is_error": False, "structured_output": {"notes": "x"}, "result": "", "modelUsage": {}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope), "")

    monkeypatch.setattr(providers.ClaudeCLI, "available", lambda self: True)
    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    provider = providers.ClaudeCLI(LLMConfig(provider="claude-cli"))
    assert provider.complete_json("hi", schema={"type": "object", "properties": {}, "required": []}) == {"notes": "x"}
    (cmd,) = seen
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd and "--mcp-config" not in cmd
