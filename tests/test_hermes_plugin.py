"""The Hermes plugin wires Rung's stop decision and CLI views into Hermes's plugin surfaces."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1] / "hermes-plugin"


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """Import the plugin package with Hermes's `agent.runtime_cwd` seam pointing at tmp_path."""
    agent = types.ModuleType("agent")
    runtime_cwd = types.ModuleType("agent.runtime_cwd")
    runtime_cwd.resolve_agent_cwd = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.runtime_cwd", runtime_cwd)
    for name in [name for name in sys.modules if name.startswith("rung_hermes")]:
        monkeypatch.delitem(sys.modules, name)
    spec = importlib.util.spec_from_file_location(
        "rung_hermes", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "rung_hermes", module)
    spec.loader.exec_module(module)
    return module


def _fake_run(calls, returncode=0, stdout="", stderr=""):
    def run(args, cwd, timeout, stdin=None):
        calls.append({"args": args, "cwd": cwd, "stdin": stdin})
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


class FakeContext:
    def __init__(self):
        self.tools, self.hooks, self.commands, self.skills = [], [], [], []

    def register_tool(self, name, toolset, schema, handler):
        self.tools.append(name)

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append(name)

    def register_skill(self, name, path):
        self.skills.append((name, Path(path)))


def test_registers_exactly_what_the_manifest_declares(plugin):
    manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    ctx = FakeContext()
    plugin.register(ctx)
    assert ctx.tools == manifest["provides_tools"]
    assert ctx.hooks == manifest["provides_hooks"]
    assert ctx.commands == ["rung"]
    assert [name for name, path in ctx.skills if path.is_file()] == ["rung-setup"]


def test_hook_continues_with_rungs_reason_when_checks_lack_fresh_passes(plugin, tmp_path, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]
    calls = []
    reason = "Rung: run `rung check tests` for touched checks without fresh passing verdicts."
    monkeypatch.setattr(tools, "_run", _fake_run(calls, returncode=2, stderr=reason))
    result = tools.on_pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
    assert result["action"] == "continue"
    assert reason in result["message"] and "rung_check" in result["message"]
    assert calls[0]["args"] == ["hook", "stop"]
    payload = json.loads(calls[0]["stdin"])
    assert payload == {"cwd": str(tmp_path / "src"), "stop_hook_active": False}


@pytest.mark.parametrize("returncode", [0, 1])
def test_hook_lets_the_turn_finish_unless_rung_blocks(plugin, monkeypatch, returncode):
    tools = sys.modules["rung_hermes.tools"]
    monkeypatch.setattr(tools, "_run", _fake_run([], returncode=returncode, stderr="anything"))
    assert tools.on_pre_verify(coding=True, attempt=0, changed_paths=["a.py"]) is None


def test_hook_stays_out_of_non_coding_turns_and_stops_nudging(plugin, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]
    calls = []
    monkeypatch.setattr(tools, "_run", _fake_run(calls))
    assert tools.on_pre_verify(coding=False, attempt=0, changed_paths=["a.py"]) is None
    assert calls == []
    tools.on_pre_verify(coding=True, attempt=tools.MAX_NUDGES, changed_paths=["a.py"])
    assert json.loads(calls[0]["stdin"])["stop_hook_active"] is True


def test_hook_fails_open_when_rung_cannot_run(plugin, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]

    def missing(*args, **kwargs):
        raise FileNotFoundError("rung")

    monkeypatch.setattr(tools, "_run", missing)
    assert tools.on_pre_verify(coding=True, attempt=0, changed_paths=["a.py"]) is None


def test_check_tool_passes_ids_and_returns_rungs_json(plugin, tmp_path, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]
    calls = []
    verdicts = {"results": [{"id": "tests", "verdict": "fail"}]}
    monkeypatch.setattr(tools, "_run", _fake_run(calls, returncode=1, stdout=json.dumps(verdicts)))
    reply = json.loads(tools.rung_check({"ids": ["tests"], "changed": True}))
    assert reply == {"ok": False, "exit_code": 1, "result": verdicts}
    assert calls[0]["args"] == ["check", "--changed", "tests", "--json"]
    assert calls[0]["cwd"] == tmp_path


def test_tools_report_a_missing_cli_instead_of_raising(plugin, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]

    def missing(*args, **kwargs):
        raise FileNotFoundError("rung")

    monkeypatch.setattr(tools, "_run", missing)
    reply = json.loads(tools.rung_kit({}))
    assert reply["ok"] is False and "rung-cli" in reply["error"]


def test_slash_command_rejects_unknown_views_and_bare_decisions(plugin):
    assert plugin._slash("deploy").startswith("Usage")
    assert plugin._slash("approve").startswith("Usage")


def test_feature_decisions_are_slash_commands_only(plugin, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]
    calls = []
    monkeypatch.setattr(tools, "_run", _fake_run(calls, stdout="Marked checkout approved."))
    assert plugin._slash("approve checkout coupons") == "Marked checkout approved."
    assert calls[0]["args"] == ["map", "--brief", "--approve", "checkout", "coupons", "--no-color"]
    plugin._slash("not-built wishlist")
    assert calls[1]["args"][:4] == ["map", "--brief", "--not-built", "wishlist"]
    schema = json.dumps(sys.modules["rung_hermes.schemas"].RUNG_MAP)
    assert "approve" not in json.loads(schema)["parameters"]["properties"]


def test_map_tool_drafts_with_intent_and_redraft(plugin, monkeypatch):
    tools = sys.modules["rung_hermes.tools"]
    calls = []
    monkeypatch.setattr(tools, "_run", _fake_run(calls, stdout=json.dumps({"features": []})))
    reply = json.loads(tools.rung_map({"redraft": True, "intent": "checkout"}))
    assert reply["ok"] is True and reply["result"] == {"features": []}
    assert calls[0]["args"] == ["map", "--brief", "--redraft", "--intent", "checkout", "--json"]
