"""Regression tests for defects found in the pre-release review (each named after its finding)."""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from rung import cli
from rung.checks import findings, runner
from rung.core import config, git, ledger
from rung.engines import sarif

from .conftest import commit_all, run_git

PY = sys.executable
DRIVER = {"tool": {"driver": {"name": "t"}}}


def rung(root: Path, *args: str) -> int:
    return cli.main(["-C", str(root), *args])


def _toml(root: Path, body: str) -> config.Config:
    (root / "rung.toml").write_text(body)
    return config.load(root)


def test_s2_a_command_check_without_covers_goes_stale_when_anything_changes(repo: Path) -> None:
    (repo / "test_x.py").write_text("def test_ok():\n    assert True\n")
    cfg = _toml(repo, f'[[check]]\nid = "tests"\nkind = "command"\nrun = "{PY} -c \'pass\'"\n')
    runner.run_check(cfg, cfg.check("tests"))
    assert runner.status(cfg, cfg.check("tests")).state == "fresh"
    (repo / "test_x.py").write_text("def test_ok():\n    assert False\n")
    assert runner.status(cfg, cfg.check("tests")).state == "stale"


def test_b1_deleting_a_baseline_stales_the_scan_verdict(repo: Path) -> None:
    cfg = _toml(repo, '[[check]]\nid = "scan1"\nkind = "scan"\nrun = "true"\ncovers = ["src/**"]\n')
    check = cfg.check("scan1")
    findings.save_baseline(repo, "scan1", [], "abc")
    ledger.record(
        repo, check_id="scan1", kind="scan", verdict="pass", summary="ok",
        covers=ledger.snapshot(repo, runner.covers(check)), specs=ledger.snapshot(repo, runner.specs(check)),
        definition=runner.definition(check),
    )  # fmt: skip
    assert runner.status(cfg, check).state == "fresh"
    findings.baseline_path(repo, "scan1").unlink()
    assert runner.status(cfg, check).state == "stale"


def test_b1_an_expired_waiver_stales_the_verdict(repo: Path) -> None:
    cfg = _toml(repo, f'[[check]]\nid = "c"\nkind = "command"\nrun = "{PY} -c \'pass\'"\ncovers = ["src/**"]\n')
    check = cfg.check("c")
    ledger.record(repo, check_id="c", kind="command", verdict="pass", covers={}, specs={}, summary="ok",
                  definition=runner.definition(check), valid_until="2020-01-01")  # fmt: skip
    status = runner.status(cfg, check)
    assert status.state == "stale" and "expired" in status.changed[-1]


def test_b2_a_broken_ledger_is_never_trusted(project: Path, capsys) -> None:
    assert rung(project, "check") == 0
    path = ledger.verdict_path(project, "unit")
    entry = json.loads(path.read_text())
    entry["summary"] = "tampered"
    path.write_text(json.dumps(entry) + "\n")
    capsys.readouterr()
    assert rung(project, "check", "--ci") == 0
    assert "PASS" in capsys.readouterr().out  # re-ran instead of trusting the tampered entry


def test_b3_changing_only_a_check_definition_is_selected_by_changed(repo: Path) -> None:
    (repo / "app.py").write_text("x = 1\n")
    body = f'[[check]]\nid = "c"\nkind = "command"\nrun = "{PY} -c \'pass\'"\ncovers = ["app.py"]\n'
    cfg = _toml(repo, body)
    runner.run_check(cfg, cfg.check("c"))
    commit_all(repo, "setup")
    cfg = _toml(repo, body.replace("'pass'", "'raise SystemExit(1)'"))
    selected = runner.select(cfg, [], git.changed_files(repo, "HEAD"))
    assert [c.id for c in selected] == ["c"]


def test_b4_unicode_filenames_are_covered_and_detected(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "café.py").write_text("x = 1\n")
    commit_all(repo, "unicode")
    assert "src/café.py" in git.list_files(repo)
    (repo / "src" / "café.py").write_text("x = 2\n")
    assert "src/café.py" in git.changed_files(repo, "HEAD")


def test_b5_tracked_build_directories_are_project_files(repo: Path) -> None:
    (repo / "src" / "build").mkdir(parents=True)
    (repo / "src" / "build" / "core.py").write_text("x = 1\n")
    commit_all(repo, "tracked build dir")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("x")  # untracked dependency output stays out
    files = git.list_files(repo)
    assert "src/build/core.py" in files and "node_modules/dep.js" not in files


def test_b6_nested_projects_use_paths_relative_to_rung_toml(repo: Path) -> None:
    backend = repo / "backend"
    (backend / "src").mkdir(parents=True)
    (backend / "src" / "app.py").write_text("x = 1\n")
    commit_all(repo, "nested")
    (backend / "src" / "app.py").write_text("x = 2\n")
    assert git.changed_files(backend, "HEAD") == {"src/app.py"}
    assert git.show(backend, "HEAD", "src/app.py") == "x = 1\n"
    with git.worktree(backend, "HEAD") as tree:
        assert (tree / "src" / "app.py").read_text() == "x = 1\n"


@pytest.mark.parametrize(
    "output, code",
    [
        ("{}", 2),
        (
            json.dumps(
                {
                    "runs": [
                        {
                            "tool": {"driver": {"name": "t"}},
                            "invocations": [{"executionSuccessful": False}],
                            "results": [],
                        }
                    ]
                }
            ),
            2,
        ),
        (json.dumps({"runs": [{"tool": {"driver": {"name": "t"}}, "results": []}]}), 2),
    ],
)
def test_b7_a_failed_scanner_is_an_error_not_a_pass(repo: Path, output: str, code: int) -> None:
    script = repo / "scan.py"
    script.write_text(f"import sys\nprint({output!r})\nsys.exit({code})\n")
    cfg = _toml(repo, f'[[check]]\nid = "s"\nkind = "scan"\nrun = "{PY} scan.py"\n')
    assert runner.run_check(cfg, cfg.check("s")).verdict == "error"


def test_b8_escalated_findings_block_and_indexed_references_resolve() -> None:
    base = sarif.Finding("t", "r", "note", "a.py", 1, "m", "f" * 16)
    head = sarif.Finding("t", "r", "error", "a.py", 1, "m", "f" * 16)
    t = findings.triage("c", [head], [base], [], fail_on="warning")
    assert t.blocking == [head]
    location = {"physicalLocation": {"artifactLocation": {"index": 1}}}
    result = {"ruleIndex": 1, "message": {"text": "x"}, "locations": [location]}
    run = {
        "tool": {"driver": {"name": "t", "rules": [{"id": "R1"}, {"id": "R2"}]}},
        "artifacts": [{"location": {"uri": "a.py"}}, {"location": {"uri": "b.py"}}],
        "results": [result],
    }
    doc = {"runs": [run]}
    (f,) = sarif.parse(doc, Path("/repo"))
    assert (f.rule, f.path) == ("R2", "b.py")


def test_b10_each_evidence_run_gets_its_own_directory(project: Path) -> None:
    script = project / "drive.py"
    script.write_text(
        "import os, pathlib, sys\nout = pathlib.Path(os.environ['RUNG_EVIDENCE_DIR'])\n"
        "if not (pathlib.Path('flag')).exists():\n    (out / 'a.txt').write_text('x')\n"
    )
    rung(project, "add", "evidence", "e", "--run", f"{PY} drive.py")
    assert rung(project, "check", "e") == 0
    (project / "flag").write_text("second run writes nothing")
    assert rung(project, "check", "e") == 1


def test_b13_strings_with_newlines_and_quotes_round_trip(project: Path) -> None:
    cfg = config.load(project)
    cfg.checks.append(config.Check("odd", "command", [], {"run": 'echo "a"\nb \\ c'}))
    config.save(cfg)
    assert config.load(project).check("odd").get("run") == 'echo "a"\nb \\ c'
    w = findings.Waiver("unit", "a" * 16, 'multi\nline "reason"', 'Jo "J" Doe', date(2030, 1, 1))
    findings.add_waiver(project, w)
    assert findings.load_waivers(project) == [w]


@pytest.mark.parametrize(
    "body, message",
    [
        ('[[check]]\nid = "x"\nkind = "command"\nrun = "true"\ncovers = "src/**"\n', "list of glob strings"),
        ("llm = 42\n", "must be a table"),
        ('[[check]]\nid = "x"\nkind = "command"\nrun = "true"\ntimeout = "oops"\n', "non-negative integer"),
        ('[[check]]\nid = "../escape"\nkind = "command"\nrun = "true"\n', "letters, digits"),
    ],
)
def test_b14_sec3_invalid_configuration_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    (tmp_path / "rung.toml").write_text(body)
    with pytest.raises(config.ConfigError, match=message):
        config.load(tmp_path)


def test_b14_a_differential_test_with_zero_cases_is_an_error(repo: Path) -> None:
    cfg = _toml(repo, f'[[check]]\nid = "d"\nkind = "drt"\nmodel = "{PY} -c pass"\nimpl = "{PY} -c pass"\n'
                      'alphabet = ["0"]\ncases = 0\n')  # fmt: skip
    assert runner.run_check(cfg, cfg.check("d")).verdict == "error"


def _append(root: str, n: int) -> None:
    for i in range(n):
        ledger.record(Path(root), check_id=f"c{i % 3}", kind="command", verdict="pass", covers={}, specs={},
                      summary="x")  # fmt: skip


def test_b15_concurrent_writers_never_corrupt_verdicts(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_append, args=(str(tmp_path), 6)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert ledger.problems(tmp_path) == []
    assert set(ledger.read_all(tmp_path)) == {"c0", "c1", "c2"}


def test_b16_a_timeout_kills_the_whole_process_tree(project: Path, tmp_path: Path) -> None:
    marker = tmp_path / "child-still-running"
    child = f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('x')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    script = project / "parent.py"
    script.write_text(parent)
    rung(project, "add", "command", "slow", "--run", f"{PY} parent.py")
    cfg = config.load(project)
    cfg.check("slow").options["timeout"] = 1
    config.save(cfg)
    assert rung(project, "check", "slow") == 1
    time.sleep(4)
    assert not marker.exists()


def test_sec1_secrets_in_command_output_are_redacted_in_the_ledger(project: Path) -> None:
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    rung(project, "add", "command", "leaky", "--run", f"{PY} -c 'print(\"token {token}\")'")
    rung(project, "check", "leaky")
    assert token not in ledger.verdict_path(project, "leaky").read_text()


# --- second review ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["cd src && python3 -c pass", "pytest | tee out.txt", "FOO=1 pytest", "pytest; echo done", "cd src"],
)
def test_r2_shell_syntax_in_commands_is_rejected(tmp_path: Path, command: str) -> None:
    (tmp_path / "rung.toml").write_text(f'[[check]]\nid = "x"\nkind = "command"\nrun = {json.dumps(command)}\n')
    with pytest.raises(config.ConfigError, match="without a shell"):
        config.load(tmp_path)


def test_r2_an_explicit_shell_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "rung.toml").write_text('[[check]]\nid = "x"\nkind = "command"\nrun = "sh -c \'cd src && pytest\'"\n')
    assert config.load(tmp_path).check("x").get("run").startswith("sh -c")


def test_r2_a_tla_config_without_invariants_fails(tmp_path: Path) -> None:
    from rung.engines import tla

    cfg = tmp_path / "S.cfg"
    cfg.write_text("SPECIFICATION Spec\n")
    assert tla.declared_properties(cfg)["invariants"] == []
    cfg.write_text("SPECIFICATION Spec\nINVARIANTS\n    TypeOK\n    Safe\nCHECK_DEADLOCK FALSE\n")
    declared = tla.declared_properties(cfg)
    assert declared["invariants"] == ["TypeOK", "Safe"] and declared["narrowing"] == ["deadlock checking off"]


def test_r2_lean_escape_hatches_with_attributes_and_sorryax(tmp_path: Path) -> None:
    from rung.engines import lean

    (tmp_path / "P.lean").write_text(
        "theorem anything : 1 = 2 := sorryAx _ false\n@[simp] axiom cheat : forall n : Nat, n = n + 1\n"
    )
    findings = lean.scan_sources(tmp_path)
    assert any("sorry" in f for f in findings) and any("axiom declaration" in f for f in findings)


def test_r2_lean_audits_every_theorem_when_none_are_listed(tmp_path: Path) -> None:
    from rung.engines import lean

    (tmp_path / "M.lean").write_text(
        "namespace Billing\ntheorem refund_bounded : True := trivial\nnamespace Inner\n"
        "@[simp] theorem nested : True := trivial\nend Inner\nend Billing\ntheorem top : True := trivial\n"
    )
    assert lean.discover_theorems(tmp_path) == ["Billing.refund_bounded", "Billing.Inner.nested", "top"]


def test_r2_a_different_secret_on_the_same_line_is_a_new_finding(tmp_path: Path) -> None:
    def doc() -> dict:
        region = {"startLine": 1, "startColumn": 8, "endLine": 1, "endColumn": 30, "snippet": {"text": "REDACTED"}}
        location = {"physicalLocation": {"artifactLocation": {"uri": "conf.py"}, "region": region}}
        result = {
            "ruleId": "slack-bot-token",
            "message": {"text": "slack-bot-token in conf.py"},
            "locations": [location],
        }
        return {"runs": [{"tool": {"driver": {"name": "gitleaks"}}, "results": [result]}]}

    (tmp_path / "conf.py").write_text("TOKEN='xoxb-1111111111-AAAAAAAAAAA'\n")
    (before,) = sarif.parse(doc(), tmp_path)
    (tmp_path / "conf.py").write_text("TOKEN='xoxb-2222222222-BBBBBBBBBBB'\n")
    (after,) = sarif.parse(doc(), tmp_path)
    assert before.fingerprint != after.fingerprint


def test_r2_secrets_in_any_scanners_messages_are_redacted(tmp_path: Path) -> None:
    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    doc = {"runs": [{"tool": {"driver": {"name": "custom"}},
                     "results": [{"ruleId": "r", "message": {"text": f"found {token}"}}]}]}  # fmt: skip
    (f,) = sarif.parse(doc, tmp_path)
    assert token not in f.message


def test_r2_the_stop_hook_sees_committed_edits(repo: Path) -> None:
    from rung import agents

    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text("x = 1\n")
    cfg = _toml(repo, f'[[check]]\nid = "unit"\nkind = "command"\nrun = "{PY} -c \'pass\'"\ncovers = ["src/*.py"]\n')
    runner.run_check(cfg, cfg.check("unit"))
    commit_all(repo, "base")
    run_git(repo, "checkout", "-qb", "agent")
    (repo / "src" / "core.py").write_text("x = 2\n")
    commit_all(repo, "agent commits its edit")
    code, _ = agents.stop_hook(repo, "{}")
    assert code == 2


def test_r2_a_misspelled_option_is_an_error_with_a_suggestion(tmp_path: Path) -> None:
    from rung.core.config import ConfigError, load

    (tmp_path / "rung.toml").write_text('[[check]]\nid = "t"\nkind = "command"\nrun = "true"\ntmeout = 5\n')
    with pytest.raises(ConfigError, match="did you mean `timeout`"):
        load(tmp_path)


def test_r2_rewriting_rung_toml_keeps_it_readable(tmp_path: Path) -> None:
    from rung.core.config import write_atomic

    target = tmp_path / "rung.toml"
    write_atomic(target, "x = 1\n")
    assert target.stat().st_mode & 0o777 == 0o644


def test_r2_requirements_includes_cannot_loop_or_leave_the_repo() -> None:
    from rung.checks import deps

    files = {"requirements.txt": "-r requirements.txt\n-r ../../etc/secret.txt\nrequests\n"}
    found = deps.parse_manifest("requirements.txt", files["requirements.txt"], files.get)
    assert {p.name for p in found} == {"requests"}


def test_r2_a_malformed_manifest_is_a_rung_error() -> None:
    from rung.checks import deps
    from rung.core.errors import RungError

    with pytest.raises(RungError, match=r"package\.json"):
        deps.parse_manifest("package.json", "{not json")


def test_r2_allow_matches_names_however_they_are_spelled(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rung.checks import deps
    from rung.checks.base import Context
    from rung.core.config import Check

    (repo / "requirements.txt").write_text("hallucinated-pkg-zzq\n")
    monkeypatch.setattr(deps, "first_published", lambda pkg: None)
    check = Check("deps", "deps", [], {"allow": ["Hallucinated_Pkg.zzq"]})
    assert deps.DepsChecker().run(Context(root=repo), check).verdict == "pass"


def test_r2_files_written_by_the_check_are_named(repo: Path) -> None:
    script = "open('out.txt', 'w').write(__import__('time').time().hex())"
    cfg = _toml(repo, f'[[check]]\nid = "gen"\nkind = "command"\nrun = "{PY} -c \\"{script}\\""\n')
    result = runner.run_check(cfg, cfg.check("gen"))
    assert result.verdict == "error" and "out.txt" in result.outcome.summary


def test_r2_unexpected_exceptions_exit_2_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from rung import cli
    from rung.cli import commands

    def boom(*_: object) -> int:
        raise KeyError("surprise")

    monkeypatch.setattr(commands, "cmd_doctor", boom)
    monkeypatch.delenv("RUNG_DEBUG", raising=False)
    assert cli.main(["doctor"]) == 2
    assert "unexpected error" in capsys.readouterr().err


def test_attested_evidence_holds_until_the_code_changes(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "page.tsx").write_text("export default 1\n")
    cfg = _toml(repo, '[[check]]\nid = "ui"\nkind = "evidence"\nrequire = [".gif"]\ncovers = ["src/**"]\n')
    check = cfg.check("ui")
    assert runner.run_check(cfg, check).verdict == "error"  # nothing attested yet

    gif = repo / "demo.gif"
    gif.write_bytes(b"GIF89a...")
    result = runner.attest(cfg, check, [gif], by="claude-in-chrome", note="saved a draft")
    assert result.verdict == "pass" and "claude-in-chrome" in result.outcome.summary
    assert runner.status(cfg, check).trusted
    assert runner.run_check(cfg, check).verdict == "pass"  # not re-run, still held

    (repo / "src" / "page.tsx").write_text("export default 2\n")
    assert not runner.status(cfg, check).trusted
    assert runner.run_check(cfg, check).verdict == "error"


def test_attest_refuses_scripted_checks_missing_types_and_empty_files(repo: Path) -> None:
    from rung.core.config import ConfigError

    cfg = _toml(
        repo,
        '[[check]]\nid = "ui"\nkind = "evidence"\nrequire = [".gif"]\n\n'
        f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} -c \\"pass\\""\n',
    )
    png, empty = repo / "shot.png", repo / "blank.gif"
    png.write_bytes(b"\x89PNG")
    empty.write_bytes(b"")
    with pytest.raises(ConfigError, match=r"requires evidence ending in \.gif"):
        runner.attest(cfg, cfg.check("ui"), [png], by="computer-use")
    with pytest.raises(ConfigError, match="non-empty"):
        runner.attest(cfg, cfg.check("ui"), [empty], by="computer-use")
    with pytest.raises(ConfigError, match="not an attested"):
        runner.attest(cfg, cfg.check("drive"), [png], by="computer-use")


def test_zero_byte_artifacts_are_not_evidence(repo: Path) -> None:
    script = "import os, pathlib; (pathlib.Path(os.environ['RUNG_EVIDENCE_DIR']) / 'x.webm').touch()"
    cfg = _toml(repo, f'[[check]]\nid = "drive"\nkind = "evidence"\nrun = "{PY} -c \\"{script}\\""\n')
    result = runner.run_check(cfg, cfg.check("drive"))
    assert result.verdict == "fail" and "no evidence" in result.outcome.summary
