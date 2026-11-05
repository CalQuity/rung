"""Test optional remote execution through the CLI with a fake Daytona API."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rung import __version__
from rung.checks import daytona

from .conftest import commit_all, run_git
from .test_verification_workflow import _configure, rung


@pytest.fixture
def checkout(repo):
    (repo / ".gitignore").write_text("local.key\n")
    _configure(repo)
    (repo / "new.py").write_text("x = 42\n")
    commit_all(repo, "feature")
    (repo / "local.key").write_text("coordinator only")
    return repo


@pytest.fixture
def sdk(checkout, monkeypatch):
    state = SimpleNamespace(commands=[], deleted=False, uploaded=False, created=False, result="pass")
    base = run_git(checkout, "rev-parse", "main").strip()
    head = run_git(checkout, "rev-parse", "HEAD").strip()

    def exec_command(command, *, timeout):
        assert 0 < timeout <= daytona.STAGE_TIMEOUTS["gate"]
        assert "example-api-key" not in command
        state.commands.append(command)
        if command.startswith("rung --version"):
            if state.result == "mismatch":
                return SimpleNamespace(exit_code=0, result="rung 0.0.0")
            return SimpleNamespace(exit_code=0, result=f"rung {__version__}")
        if command.startswith("rung -C"):
            verdict = "fail" if state.result == "fail" else "pass"
            data = {
                "schema_version": 1,
                "head": head,
                "base": base,
                "selected": ["unit"],
                "passed": verdict == "pass",
                "blocked": [],
                "stale": {},
                "results": [{"id": "unit", "verdict": verdict, "summary": "remote check", "lines": ["private"]}],
            }
            if state.result == "forged":
                data["head"] = base
            return SimpleNamespace(exit_code=int(verdict == "fail"), result=json.dumps(data))
        return SimpleNamespace(exit_code=0, result="")

    def upload(local, remote, *, timeout):
        state.uploaded = True
        assert Path(local).is_file() and remote.endswith("/checkout.bundle") and timeout == 90
        bundle = run_git(checkout, "bundle", "list-heads", local)
        assert bundle.strip() == f"{head} HEAD"

    sandbox = SimpleNamespace(
        process=SimpleNamespace(exec=exec_command),
        fs=SimpleNamespace(upload_file=upload),
    )

    class Client:
        def __init__(self, config):
            assert config.api_key == "example-api-key"
            self.snapshot = SimpleNamespace(get=self.resolve)

        def resolve(self, name):
            assert name == "reviewed"
            return SimpleNamespace(id="snap-0123", name=name)

        def create(self, params, *, timeout):
            state.created = True
            assert vars(params) == {
                "snapshot": "reviewed",
                "ephemeral": True,
                "ttl_minutes": daytona.TTL_MINUTES,
                "public": False,
                "network_block_all": True,
            }
            if state.result == "unavailable":
                raise RuntimeError("example-api-key")
            return sandbox

        def delete(self, target, *, timeout, wait):
            assert target is sandbox and wait and timeout == daytona.STAGE_TIMEOUTS["delete"]
            state.deleted = True
            if state.result == "cleanup":
                raise RuntimeError("example-api-key")

    monkeypatch.setenv("DAYTONA_API_KEY", "example-api-key")
    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(
            CreateSandboxFromSnapshotParams=SimpleNamespace,
            DaytonaConfig=SimpleNamespace,
            Daytona=Client,
        ),
    )
    return state


def remote(repo, *args):
    return rung(
        repo,
        "gate",
        "--base",
        "main",
        "--json",
        "--executor",
        "daytona",
        "--daytona-snapshot",
        "reviewed",
        *args,
    )


@pytest.mark.parametrize("result", ["pass", "fail", "forged", "unavailable"])
def test_remote_gate_never_runs_checks_on_coordinator(checkout, sdk, result, monkeypatch, capsys):
    def local(*_args, **_kwargs):
        pytest.fail("remote gate fell back to local execution")

    monkeypatch.setattr("rung.checks.runner.run_check", local)
    sdk.result = result
    assert remote(checkout, "--policy-path", "a; touch injected") == int(result != "pass")
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["executor"] == "daytona" and data["passed"] == (result == "pass")
    assert sdk.created
    if result != "unavailable":
        assert sdk.deleted and sdk.uploaded
        assert "--policy-path=a; touch injected" in shlex.split(sdk.commands[-1])
    if result in {"forged", "unavailable"}:
        assert data["executor_error"] and not data["results"]
    else:
        assert data["results"] == [{"id": "unit", "verdict": result, "summary": "remote check"}]
        assert data["snapshot"] == "reviewed" and data["snapshot_id"] == "snap-0123"
    assert "example-api-key" not in output.out + output.err
    assert not (checkout / ".rung").exists()


def test_remote_preflight_blocks_before_sending_source(checkout, sdk, capsys):
    (checkout / "rung.toml").write_text((checkout / "rung.toml").read_text() + "# altered\n")
    assert remote(checkout) == 1
    assert json.loads(capsys.readouterr().out)["blocked"] == ["policy changed: rung.toml"]
    assert not sdk.created


def test_remote_rejects_uncommitted_source(checkout, sdk, capsys):
    (checkout / "new.py").write_text("x = 43\n")
    assert remote(checkout) == 1
    assert "clean, full checkout" in json.loads(capsys.readouterr().out)["executor_error"]
    assert not sdk.created


def test_remote_infrastructure_failures_close_the_gate(checkout, sdk, capsys):
    sdk.result = "mismatch"
    assert remote(checkout) == 1
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert not data["passed"] and data["executor_error"]
    assert sdk.deleted and not sdk.uploaded
    assert "example-api-key" not in output.out + output.err


def test_cleanup_failure_warns_without_replacing_a_validated_result(checkout, sdk, capsys, caplog):
    sdk.result = "cleanup"
    assert remote(checkout) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["passed"] and "executor_error" not in data
    assert data["results"] == [{"id": "unit", "verdict": "pass", "summary": "remote check"}]
    assert "Daytona cleanup failed" in data["cleanup_warning"] and "Daytona cleanup failed" in caplog.text
    assert sdk.deleted and "example-api-key" not in output.out + output.err + caplog.text


def test_sandbox_lifetime_outlasts_every_stage_with_a_margin():
    worst = daytona.CREATE_TIMEOUT + sum(daytona.STAGE_TIMEOUTS.values())
    assert worst + daytona.TTL_MARGIN_MINUTES * 60 <= daytona.TTL_MINUTES * 60


def test_remote_gate_forwards_approval_and_head_to_the_sandbox(checkout, sdk, capsys):
    head = run_git(checkout, "rev-parse", "HEAD").strip()
    assert remote(checkout, "--pr-label", "rung-policy-approved", "--pr-head", head) == 0
    argv = shlex.split(sdk.commands[-1])
    assert "--pr-label=rung-policy-approved" in argv and f"--pr-head={head}" in argv


def test_human_output_names_the_resolved_snapshot(checkout, sdk, capsys):
    assert rung(checkout, "gate", "--base", "main", "--executor", "daytona", "--daytona-snapshot", "reviewed") == 0
    assert "id snap-0123" in capsys.readouterr().out


def test_remote_without_credential_fails_without_contacting_provider(checkout, sdk, monkeypatch, capsys):
    monkeypatch.delenv("DAYTONA_API_KEY")
    assert remote(checkout) == 1
    assert "set DAYTONA_API_KEY" in json.loads(capsys.readouterr().out)["executor_error"]
    assert not sdk.created
