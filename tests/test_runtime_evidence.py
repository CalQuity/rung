"""Seeded runs, scenarios and faults, the evidence page, and how reports show them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rung import cli
from rung.analysis import report, survey
from rung.checks import command
from rung.core import config, ledger
from rung.core.config import ConfigError
from rung.drafting import runtime
from rung.engines import evidence_page

from .test_draft import ScriptedProvider, _drive

PY = sys.executable

# A drive script that records what Rung passed it, writes a screenshot per step, and fails on any seed
# listed in `bad_seeds.txt`, in the `reload` scenario.
SCRIPT = """import json, os, pathlib, sys
out = pathlib.Path(os.environ["RUNG_EVIDENCE_DIR"])
seed = int(os.environ["RUNG_SEED"])
env = {k: os.environ.get(k) for k in ("RUNG_FAULTS", "RUNG_SCENARIOS")}
bad = pathlib.Path("bad_seeds.txt")
broken = bad.exists() and str(seed) in bad.read_text().split()
names = (os.environ.get("RUNG_SCENARIOS") or "happy,reload").split(",")
scenarios = []
for name in names:
    ok = not (broken and name == "reload")
    (out / name).mkdir()
    (out / name / "01-open-cart.png").write_bytes(b"png")
    (out / name / "02-coupon-applied-total-32.png").write_bytes(b"png")
    scenarios.append({"name": name, "category": "happy" if name == "happy" else "fault",
                      "faults": [f"reload after {seed % 7} ms"] if name == "reload" else [],
                      "passed": ok,
                      "assertions": [{"name": "total is $32.00", "expected": "$32.00",
                                      "observed": "$32.00" if ok else "$27.00", "passed": ok}]})
(out / "video.webm").write_bytes(b"webm")
(out / "trace.zip").write_bytes(b"zip")
(out / "network.har").write_text("{}")
(out / "env.json").write_text(json.dumps(env))
result = {"feature": "coupon", "seed": seed, "passed": all(s["passed"] for s in scenarios),
          "scenarios": scenarios, "not_applicable": {"auth": "no sign-in"}}
(out / "result.json").write_text(json.dumps(result))
sys.exit(0 if result["passed"] else 1)
"""


def rung(root: Path, *args: str) -> int:
    return cli.main(["-C", str(root), *args])


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    (tmp_path / "drive.py").write_text(SCRIPT)
    (tmp_path / "rung.toml").write_text(
        f'[project]\nname = "shop"\n\n[llm]\nprovider = "none"\n\n'
        f'[[check]]\nid = "runtime"\nkind = "evidence"\nrun = "{PY} drive.py"\nruns = 3\nseed = 100\n'
        'covers = ["drive.py"]\n'
    )
    return tmp_path


def _entry(root: Path) -> dict:
    return ledger.read_all(root)["runtime"]


def test_each_seed_runs_in_its_own_directory_and_is_recorded(shop: Path) -> None:
    assert rung(shop, "check", "runtime") == 0
    entry = _entry(shop)
    runs = entry["details"]["runs"]
    assert [r["seed"] for r in runs] == [100, 101, 102] and all(r["verdict"] == "pass" for r in runs)
    assert "3 runs, seeds 100-102" in entry["summary"]
    dirs = {r["evidence_dir"] for r in runs}
    assert len(dirs) == 3 and all(d.endswith(f"seed-{r['seed']}") for d, r in zip(sorted(dirs), runs, strict=True))
    assert runs[0]["scenarios"][1] == {"name": "reload", "category": "fault", "faults": ["reload after 2 ms"],
                                       "passed": True}  # fmt: skip


def test_the_default_seed_comes_from_the_check_id(shop: Path) -> None:
    cfg = config.load(shop)
    check = cfg.check("runtime")
    del check.options["seed"]
    assert command.seeds(check) == command.seeds(check)
    assert command.seeds(check)[0] == command.default_seed("runtime") != command.default_seed("other")
    assert command.seeds(check, only=7) == [7]


def test_a_failing_seed_is_named_with_the_command_that_reproduces_it(shop: Path, capsys) -> None:
    (shop / "bad_seeds.txt").write_text("101\n")
    assert rung(shop, "check", "runtime") == 1
    out = capsys.readouterr().out
    assert "1 of 3 runs failed; seed 101" in out
    assert "reproduce: rung check runtime --seed 101" in out
    details = _entry(shop)["details"]
    assert (details["failing_seed"], details["failing_scenario"]) == (101, "reload")
    assert details["reproduce"] == "rung check runtime --seed 101"
    assert [r["verdict"] for r in details["runs"]] == ["pass", "fail", "pass"]
    assert details["runs"][1]["failed"] == ["reload: total is $32.00"]


def test_seed_option_reproduces_one_run_without_recording_it(shop: Path, capsys) -> None:
    (shop / "bad_seeds.txt").write_text("101\n")
    before = len(ledger.history(shop))
    assert rung(shop, "check", "runtime", "--seed", "101") == 1
    assert rung(shop, "check", "runtime", "--seed", "102") == 0
    out = capsys.readouterr().out
    assert "not recorded in the ledger" in out
    assert len(ledger.history(shop)) == before
    runs = [sorted(d.name for d in r.glob("seed-*")) for r in (shop / ".rung" / "evidence" / "runtime").iterdir()]
    assert sorted(runs) == [["seed-101"], ["seed-102"]]


def test_seed_is_refused_for_other_checks_and_several_ids(shop: Path, capsys) -> None:
    with (shop / "rung.toml").open("a") as fh:
        fh.write(f'\n[[check]]\nid = "unit"\nkind = "command"\nrun = "{PY} -c pass"\n')
    assert rung(shop, "check", "unit", "--seed", "1") == 2
    assert rung(shop, "check", "unit", "runtime", "--seed", "1") == 2
    assert "evidence checks with `run`" in capsys.readouterr().err


def test_faults_and_scenarios_reach_the_script_and_must_be_driven(shop: Path, capsys) -> None:
    cfg = config.load(shop)
    check = cfg.check("runtime")
    check.options.update({"runs": 1, "faults": ["reload"]})
    config.save(cfg)
    assert rung(shop, "check", "runtime") == 0
    run_dir = shop / _entry(shop)["details"]["runs"][0]["evidence_dir"]
    assert json.loads((run_dir / "env.json").read_text())["RUNG_FAULTS"] == "reload"

    check.options["faults"] = ["offline"]
    config.save(cfg)
    assert rung(shop, "check", "runtime") == 1
    assert "no scenario named offline" in _entry(shop)["summary"]

    assert rung(shop, "check", "runtime", "--scenario", "happy") == 1  # offline still requested
    del check.options["faults"]
    config.save(cfg)
    capsys.readouterr()
    assert rung(shop, "check", "runtime", "--scenario", "happy", "--json") == 0
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert [s["name"] for s in result["runs"][0]["scenarios"]] == ["happy"]


def test_unset_faults_leave_the_script_its_own_list(shop: Path) -> None:
    assert rung(shop, "check", "runtime") == 0
    run_dir = shop / _entry(shop)["details"]["runs"][0]["evidence_dir"]
    assert json.loads((run_dir / "env.json").read_text()) == {"RUNG_FAULTS": None, "RUNG_SCENARIOS": None}


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("import json, os, pathlib; d = pathlib.Path(os.environ['RUNG_EVIDENCE_DIR']); "
         "(d / 'result.json').write_text(json.dumps({'seed': 1}))", "reports seed 1"),
        ("import json, os, pathlib; d = pathlib.Path(os.environ['RUNG_EVIDENCE_DIR']); "
         "(d / 'result.json').write_text(json.dumps({'scenarios': [{'name': 'x', 'passed': False}]}))",
         "result.json records failures: x: failed"),
    ],
)  # fmt: skip
def test_result_json_must_agree_with_the_run(tmp_path: Path, script: str, message: str) -> None:
    (tmp_path / "drive.py").write_text(script)
    check = config.Check("e", "evidence", [], {"run": f"{PY} drive.py", "seed": 5})
    outcome = command.EvidenceChecker().run(command.Context(root=tmp_path), check)
    assert outcome.verdict == "fail" and message in outcome.summary


def test_seed_runs_and_fault_names_are_validated(tmp_path: Path) -> None:
    base = f'[[check]]\nid = "e"\nkind = "evidence"\nrun = "{PY} d.py"\n'
    with pytest.raises(ConfigError, match="at least 1"):
        config.load(_write(tmp_path, base + "runs = 0\n"))
    with pytest.raises(ConfigError, match="`faults` names"):
        config.load(_write(tmp_path, base + 'faults = ["slow, network"]\n'))
    with pytest.raises(ConfigError, match="`scenarios` must be a list"):
        config.load(_write(tmp_path, base + 'scenarios = "happy"\n'))
    loaded = config.load(_write(tmp_path, base + 'runs = 3\nseed = 9\nfaults = []\nscenarios = ["happy"]\n'))
    assert loaded.check("e").get("runs") == 3


def _write(root: Path, body: str) -> Path:
    (root / "rung.toml").write_text(body)
    return root


def test_each_run_writes_a_self_contained_evidence_page(shop: Path, capsys) -> None:
    (shop / "bad_seeds.txt").write_text("102\n")
    rung(shop, "check", "runtime")
    out = capsys.readouterr().out
    assert "evidence page: .rung/evidence/runtime/" in out
    assert out.rstrip().endswith("reproduce: rung check runtime --seed 102")
    page = shop / _entry(shop)["details"]["report"]
    text = page.read_text()
    assert "http://" not in text and "https://" not in text and "<script" not in text
    assert "<style>" in text and "Reproduce the failing run: <code>rung check runtime --seed 102</code>" in text
    # Each scenario's screenshots sit under it, in step order, captioned from the file name.
    reload_at = text.index("Scenario: reload", text.index("id='seed-102'"))
    shot = text.index("seed-102/reload/01-open-cart.png", reload_at)
    assert shot < text.index("seed-102/reload/02-coupon-applied-total-32.png", reload_at)
    assert "2. coupon applied total 32" in text
    assert "<td>$32.00</td><td>$27.00</td>" in text  # expected against observed
    assert "<video controls" in text and "seed-100/video.webm" in text
    assert "npx playwright show-trace seed-100/trace.zip" in text and "seed-100/network.har" in text
    assert "<b>auth</b>: no sign-in" in text


def test_evidence_command_writes_the_page_for_one_seed_elsewhere(shop: Path, tmp_path: Path) -> None:
    rung(shop, "check", "runtime")
    out = tmp_path / "share"
    assert rung(shop, "evidence", "runtime", "--seed", "101", "--out", str(out)) == 0
    text = (out / "index.html").read_text()
    assert "id='seed-101'" in text and "id='seed-100'" not in text
    link = text.split("src='", 1)[1].split("'", 1)[0]
    assert (out / link).resolve().is_file()  # relative links still reach the files
    assert rung(shop, "evidence", "runtime", "--seed", "999") == 2


def test_report_shows_seeds_the_failing_scenario_and_the_page(shop: Path) -> None:
    (shop / "bad_seeds.txt").write_text("101\n")
    rung(shop, "check", "runtime")
    data = report.build(config.load(shop), evidence_url="https://example.invalid/artifacts/1")
    rt = data["checks"][0]["runtime"]
    assert rt["failing_seed"] == 101 and rt["reproduce"] == "rung check runtime --seed 101"
    md = report.markdown(data)
    assert "seeds ✅ 100 · ❌ 101 · ✅ 102" in md
    assert "failing seed **101**, scenario `reload`; reproduce with `rung check runtime --seed 101`" in md
    assert "auth: not applicable (no sign-in)" in md
    assert "[Download the recordings](https://example.invalid/artifacts/1)" in md
    assert "evidence page: `.rung/evidence/runtime/" in md


def test_modified_evidence_page_stales_the_receipt_and_hides_the_link(shop: Path) -> None:
    assert rung(shop, "check", "runtime") == 0
    entry = _entry(shop)
    path = entry["details"]["report"]
    assert any(row.startswith(f"{path} sha256:") for row in entry["evidence"])
    (shop / path).write_text("<h1>Fabricated result</h1>")
    data = report.build(config.load(shop))["checks"][0]
    assert data["state"] == "stale" and any(path in item for item in data["changed"])
    assert data["runtime"]["report"] == ""


def test_previous_failure_is_stale_after_editing_covered_inputs(shop: Path) -> None:
    (shop / "bad_seeds.txt").write_text("101\n")
    assert rung(shop, "check", "runtime") == 1
    assert report.build(config.load(shop))["checks"][0]["state"] == "failing"
    drive = shop / "drive.py"
    drive.write_text(drive.read_text() + "\n# changed since the failure\n")
    data = report.build(config.load(shop))["checks"][0]
    assert data["state"] == "stale" and "drive.py" in data["changed"]


def test_captions_follow_the_step_names() -> None:
    assert evidence_page.caption("03-coupon-applied-total-32.png") == "3. coupon applied total 32"
    assert evidence_page.caption("home.png") == "home"


# --- drafting -----------------------------------------------------------------------------------


def test_draft_accepts_exact_managed_paths_and_explains_a_missing_file(tmp_path: Path) -> None:
    prefixed = _drive()
    for file in prefixed["files"]:
        file["path"] = f".rung/verify/drives/refund/{file['path']}"
    rejected = {"files": [{"path": "../drive.py", "content": "pass"}], "sources": [], "requirements": [], "notes": ""}
    info = survey.survey(tmp_path)
    result = runtime.draft_runtime(
        ScriptedProvider([rejected]), tmp_path, info, focus=[], check_id="refund", max_rounds=1
    )
    assert not result.ok and not result.files
    assert "did not include nonempty files named drive.py, FEATURES.md" in result.message
    result = runtime.draft_runtime(
        ScriptedProvider([prefixed]), tmp_path, info, focus=[], check_id="refund", max_rounds=1
    )
    assert result.ok and {file.name for file in result.files} == {"drive.py", "FEATURES.md"}


def test_draft_rejects_a_script_that_only_drives_the_happy_path(tmp_path: Path) -> None:
    provider = ScriptedProvider([_drive(cats=["happy"]), _drive()])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.rounds == 2
    assert "covers only the happy path" in provider.prompts[1]
    assert "no scenario in categories input, state, fault, robustness" in provider.prompts[1]


def test_draft_rejects_a_script_that_ignores_the_seed(tmp_path: Path) -> None:
    unseeded = _drive()
    source = unseeded["files"][0]["content"]
    unseeded["files"][0]["content"] = source.replace('int(os.environ["RUNG_SEED"])', "0").replace('"seed": seed, ', "")
    wrong = _drive()
    wrong["files"][0]["content"] = source.replace('int(os.environ["RUNG_SEED"])', "0")
    provider = ScriptedProvider([unseeded, wrong, _drive()])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=3)
    assert result.ok and result.rounds == 3
    assert (
        "drive.py never reads RUNG_SEED" in provider.prompts[1] and "result.json has no `seed`" in provider.prompts[1]
    )
    assert "result.json reports seed 0, but Rung passed" in provider.prompts[2]


def test_draft_runs_the_same_seed_twice_and_sends_a_flaky_script_back(tmp_path: Path, monkeypatch) -> None:
    calls: list[int | None] = []
    real = command.EvidenceChecker.run

    def spy(self, ctx, check):
        calls.append(ctx.seed)
        return real(self, ctx, check)

    monkeypatch.setattr(command.EvidenceChecker, "run", spy)
    flips = iter([True, False])
    monkeypatch.setattr(runtime, "_flaky", lambda a, b: ["input: input holds"] if next(flips) else [])
    provider = ScriptedProvider([_drive(flaky=True), _drive()])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.rounds == 2
    seed = command.default_seed("runtime")
    assert calls == [None, seed, None, seed]  # each candidate ran its seed a second time
    assert f"twice with RUNG_SEED={seed} and the runs disagreed on: input: input holds" in provider.prompts[1]


def test_outcomes_that_differ_between_runs_are_flaky() -> None:
    first = {"scenarios": [{"name": "a", "assertions": [{"name": "x", "passed": True}]}]}
    second = {"scenarios": [{"name": "a", "assertions": [{"name": "x", "passed": False}]}]}
    assert runtime._flaky(first, second) == ["a: x"]
    assert runtime._flaky(first, first) == []


def test_a_scenario_the_app_fails_is_a_finding_not_a_repair(tmp_path: Path) -> None:
    provider = ScriptedProvider([_drive(fail=("input",))])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.findings == ["input: input holds"]
    assert len(provider.prompts) == 1  # never sent back to the model to make it pass
    assert "the app failed 1 assertion(s)" in result.message


def test_a_failing_happy_path_is_still_sent_back(tmp_path: Path) -> None:
    provider = ScriptedProvider([_drive(fail=("happy",)), _drive()])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.rounds == 2 and not result.findings


def test_draft_explains_trace_and_har_requirements(tmp_path: Path) -> None:
    provider = ScriptedProvider([_drive()])
    runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=1, require=(".zip", ".har"))
    assert ".zip (a Playwright trace (trace.zip)), .har (a HAR network log)" in provider.prompts[0]
    assert "RUNG_SEED" in provider.systems[0] and "context.tracing.start" in provider.systems[0]


def test_draft_with_findings_registers_the_check_and_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    from rung.cli.commands import draft as draft_cmd
    from rung.drafting import providers

    (tmp_path / "rung.toml").write_text('[project]\nname = "s"\n\n[llm]\nprovider = "none"\n')
    (tmp_path / "app.py").write_text(
        "def observation(scenario):\n    return 'wrong' if scenario == 'state' else 'app fixture'\n"
    )
    reply = _drive()
    reply["sources"] = ["app.py"]
    script = reply["files"][0]
    script["content"] = "import runpy\nobserve = runpy.run_path('app.py')['observation']\n" + script["content"].replace(
        "cat not in FAIL", "observe(cat) == 'app fixture'"
    )
    monkeypatch.setattr(providers, "get", lambda llm: ScriptedProvider([reply]))
    monkeypatch.setattr(draft_cmd.agents, "install_verify_skill", lambda root, name, cid: root / "SKILL.md")
    assert rung(tmp_path, "draft", "runtime", "--allow-unisolated") == 1
    out = capsys.readouterr().out
    assert "The app failed 1 assertion(s)" in out and "state: state holds" in out
    registered = config.load(tmp_path).check("runtime")
    assert registered.kind == "evidence" and registered.get("isolate") is True


def test_long_output_lines_are_shortened(tmp_path: Path) -> None:
    (tmp_path / "drive.py").write_text("print('{' + 'x' * 5000 + '}'); raise SystemExit(1)")
    check = config.Check("e", "evidence", [], {"run": f"{PY} drive.py"})
    outcome = command.EvidenceChecker().run(command.Context(root=tmp_path), check)
    assert outcome.verdict == "fail" and max(len(line) for line in outcome.lines) < 300


def test_a_failing_drive_script_reports_expected_and_observed_not_its_raw_output(tmp_path) -> None:
    from rung.checks.command import failure_details

    result = {"scenarios": [
        {"name": "coupon-total-floor", "assertions": [
            {"name": "total is never negative", "expected": "$0.00", "observed": "-$32.00", "passed": False},
            {"name": "note shown", "passed": True}]},
        {"name": "double-submit", "assertions": [{"name": "one paid order", "expected": 1, "observed": 2,
                                                  "passed": False}]},
    ]}  # fmt: skip
    assert failure_details(result) == [
        "coupon-total-floor: total is never negative (expected $0.00, observed -$32.00)",
        "double-submit: one paid order (expected 1, observed 2)",
    ]
    many = {"scenarios": [{"name": "s", "assertions": [{"name": f"a{i}", "passed": False} for i in range(20)]}]}
    lines = failure_details(many)
    assert len(lines) == 13 and lines[-1].startswith("... and 8 more")


def test_full_runtime_does_not_inherit_replay_selection(shop: Path, monkeypatch) -> None:
    (shop / "bad_seeds.txt").write_text("102\n")
    monkeypatch.setenv("RUNG_SCENARIOS", "happy")
    monkeypatch.setenv("RUNG_FAULTS", "happy")
    assert rung(shop, "check", "runtime") == 1
    assert _entry(shop)["details"]["failing_seed"] == 102


def test_draft_rejects_a_drive_that_only_checks_source_presence(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("raise RuntimeError('cannot start')\n")
    reply = _drive()
    reply["sources"] = ["app.py"]
    reply["files"][0]["content"] = (
        "from pathlib import Path\nassert Path('app.py').exists()\n" + reply["files"][0]["content"]
    )
    result = runtime.draft_runtime(
        ScriptedProvider([reply]),
        tmp_path,
        survey.survey(tmp_path),
        focus=[],
        max_rounds=1,
    )
    assert not result.ok and "did not detect the startup control" in result.message
    assert (tmp_path / "app.py").is_file()
    assert not (tmp_path / ".rung" / "verify" / "drive.py").exists()
    assert result.files[0].is_relative_to(tmp_path / ".rung" / "cache" / "drafts")


def test_draft_requires_an_assertion_to_catch_a_behavior_mutation(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def calculate():\n    return 42\n")
    false_proof = _drive()
    false_proof["sources"] = ["app.py"]
    false_proof["files"][0]["content"] = "import runpy\nrunpy.run_path('app.py')\n" + false_proof["files"][0]["content"]
    info = survey.survey(tmp_path)
    held = runtime.draft_runtime(ScriptedProvider([false_proof]), tmp_path, info, focus=[], max_rounds=1)
    assert not held.ok and "did not detect the behavior mutation" in held.message
    real = _drive()
    real["sources"] = ["app.py"]
    code = real["files"][0]["content"]
    real["files"][0]["content"] = "import runpy\nactual = runpy.run_path('app.py')['calculate']()\n" + code.replace(
        "ok = cat", "ok = actual == 42 and cat"
    )
    accepted = runtime.draft_runtime(ScriptedProvider([real]), tmp_path, info, focus=[], max_rounds=1)
    assert accepted.ok and "detected behavior mutation" in accepted.sensitivity


def test_runtime_draft_holds_startup_only_javascript(tmp_path: Path) -> None:
    app = tmp_path / "app.js"
    app.write_text("exports.answer = () => 42;\n")
    startup = _drive()
    startup["sources"] = ["app.js"]
    startup["files"][0]["content"] = (
        "import subprocess\nsubprocess.run(['node', 'app.js'], check=True)\n" + startup["files"][0]["content"]
    )
    result = runtime.draft_runtime(
        ScriptedProvider([startup]), tmp_path, survey.survey(tmp_path), focus=[], max_rounds=1, require_source=True
    )
    assert not result.ok and "needs a behavioral assertion control" in result.message


def test_runtime_draft_can_catch_a_javascript_arithmetic_mutation(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("exports.total = (amount) => amount + 1;\n")
    reply = _drive()
    reply["sources"] = ["app.js"]
    code = reply["files"][0]["content"]
    reply["files"][0]["content"] = (
        "import subprocess\n"
        "actual = subprocess.check_output(['node', '-p', \"require('./app.js').total(1)\"], text=True).strip()\n"
        + code.replace("ok = cat", "ok = actual == '2' and cat")
    )
    result = runtime.draft_runtime(
        ScriptedProvider([reply]), tmp_path, survey.survey(tmp_path), focus=[], max_rounds=1, require_source=True
    )
    assert result.ok and "detected behavior mutation" in result.sensitivity


def test_drafted_drive_replay_retains_evidence_with_the_write_guard(tmp_path, monkeypatch, capsys):
    from rung.core import sandbox

    monkeypatch.setenv("DATABASE_URL", "test-value")
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "escaped"
    guard_assertions = (
        "import os\nfrom pathlib import Path\n"
        f"assert Path.cwd() != Path({str(root.resolve())!r})\n"
        "assert 'DATABASE_URL' not in os.environ\n"
    )
    if sys.platform == "darwin":
        sandbox.probe_write_guard(tmp_path)
        guard_assertions += (
            f"outside = Path({str(outside)!r})\n"
            "try:\n"
            "    outside.write_text('escaped')\n"
            "except PermissionError:\n"
            "    pass\n"
            "assert not outside.exists()\n"
        )
    else:
        monkeypatch.setattr(sandbox, "validated_write_guard", lambda *_a, **_kw: ("env", "RUNG_TEST_GUARD=1"))
        guard_assertions += "assert os.environ['RUNG_TEST_GUARD'] == '1'\n"
    (root / "drive.py").write_text(
        guard_assertions + "out = Path(os.environ['RUNG_EVIDENCE_DIR'])\n"
        "(out / 'observation.txt').write_text(os.environ['RUNG_SEED'])\n"
    )
    (root / "rung.toml").write_text(
        f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} drive.py"\n'
        'isolate = true\nrequire = [".txt"]\n'
    )
    assert rung(root, "check", "drive", "--json") == 0
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["execution"] == "isolated"
    assert not outside.exists()
    assert (root / result["report"]).is_file()
    assert all((root / path).is_file() for path in result["artifacts"])
    assert rung(root, "status", "--require-fresh") == 0


def test_drafted_drive_does_not_run_when_the_write_guard_is_unavailable(tmp_path, monkeypatch, capsys):
    from rung.core import sandbox
    from rung.core.errors import ToolMissing

    marker = tmp_path / "ran"
    (tmp_path / "drive.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    (tmp_path / "rung.toml").write_text(
        f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} drive.py"\nisolate = true\n'
    )

    def unavailable(*args, **kwargs):
        raise ToolMissing("write guard unavailable")

    monkeypatch.setattr(sandbox, "write_guard", unavailable)
    assert rung(tmp_path, "check", "drive", "--json") == 1
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["verdict"] == "error" and "write guard unavailable" in result["summary"]
    assert not marker.exists()


def test_doctor_requires_write_guard_for_an_isolated_drive(tmp_path, monkeypatch, capsys):
    from rung.core import sandbox
    from rung.core.errors import ToolMissing

    (tmp_path / "rung.toml").write_text(
        f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} drive.py"\nisolate = true\n'
    )

    def unavailable(*args, **kwargs):
        raise ToolMissing("write guard unavailable")

    monkeypatch.setattr(sandbox, "probe_write_guard", unavailable)
    assert rung(tmp_path, "doctor", "--strict", "--json") == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(row["tool"] == "drive: write guard" and not row["ok"] for row in rows)


def test_a_drafted_check_without_isolate_keeps_running_unisolated(tmp_path, monkeypatch, capsys):
    from rung.core import sandbox
    from rung.core.errors import ToolMissing

    (tmp_path / "drive.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "(Path(os.environ['RUNG_EVIDENCE_DIR']) / 'observation.txt').write_text('seen')\n"
    )
    (tmp_path / "rung.toml").write_text(
        f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} drive.py"\ndrafted_by = "claude-cli:sonnet"\n'
    )

    def unavailable(*args, **kwargs):
        raise ToolMissing("write guard unavailable")

    monkeypatch.setattr(sandbox, "write_guard", unavailable)
    monkeypatch.setattr(sandbox, "validated_write_guard", unavailable)
    check = config.load(tmp_path).check("drive")
    assert not command.isolates_evidence(check)
    assert command.isolates_evidence(config.Check("drive", "evidence", [], {**check.options, "isolate": True}))
    assert rung(tmp_path, "check", "drive", "--json") == 0
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["verdict"] == "pass" and result.get("execution") != "isolated"
