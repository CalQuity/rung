"""`rung kit` and the kit `rung init` builds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from rung import cli
from rung.analysis import kit, survey
from rung.cli.commands import setup
from rung.cli.output import Output
from rung.core.config import Check, load

PY = sys.executable


def run(project: Path, *args: str) -> int:
    return cli.main(["-C", str(project), *args])


def test_each_check_kind_lands_in_its_part() -> None:
    cases = [
        (Check("lint", "command", [], {"run": "ruff check ."}), "static"),
        (Check("typecheck", "command", [], {"run": "pyright"}), "static"),
        (Check("shortcuts", "shortcuts", [], {}), "static"),
        (Check("backend-tests", "command", [], {"run": "pytest"}), "tests"),
        (
            Check("pricing-properties", "command", [], {"run": "pytest", "drafted_by": "claude-cli:sonnet"}),
            "properties",
        ),
        (Check("secrets", "scan", [], {"tool": "gitleaks"}), "security"),
        (Check("deps", "deps", [], {}), "security"),
        (Check("runtime", "evidence", [], {"run": "python drive.py"}), "app"),
        (Check("payments-tla", "tla", [], {}), "formal"),
        (Check("pricing-lean-drt", "drt", [], {}), "formal"),
    ]
    for check, part in cases:
        assert kit.part_of(check) == part, check.id


def test_kit_shows_a_never_run_check_and_points_at_rung_check(project: Path, capsys) -> None:
    assert run(project, "kit", "--no-color") == 0
    text = capsys.readouterr().out
    assert "never run  unit" in text
    assert "Next: rung check" in text


def test_a_failing_check_comes_first_in_next(project: Path, capsys) -> None:
    cfg = load(project)
    cfg.checks.append(Check("broken-tests", "command", ["src/*.py"], {"run": f"{PY} -c 'raise SystemExit(1)'"}))
    from rung.core.config import save

    save(cfg)
    assert run(project, "check") == 1
    capsys.readouterr()
    assert run(project, "kit", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["next"][0]["command"] == "rung check broken-tests"
    tests = next(p for p in data["parts"] if p["part"] == "tests")
    assert tests["state"] == "attention"


def test_a_fresh_part_is_ok_and_empty_parts_say_how_to_fill_them(project: Path, capsys) -> None:
    (project / "src" / "server.py").write_text(
        "from http.server import BaseHTTPRequestHandler\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_header('Content-Type', 'text/html')\n"
    )
    assert run(project, "check") == 0
    capsys.readouterr()
    assert run(project, "kit", "--json") == 0
    parts = {p["part"]: p for p in json.loads(capsys.readouterr().out)["parts"]}
    assert parts["tests"]["state"] == "ok"
    assert parts["app"]["state"] == "missing" and parts["app"]["suggest"] == "rung map"
    assert parts["properties"]["suggest"] == "rung draft property src/core.py"
    assert parts["agents"]["suggest"] == "rung agents --hooks"


def test_survey_finds_a_stdlib_web_app_and_a_one_function_pure_core(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text("def total(prices, coupon):\n    return sum(prices) - coupon\n")
    (tmp_path / "web.py").write_text(
        "from http.server import BaseHTTPRequestHandler\nimport html\nprint(html.escape('<b>'))\n"
    )
    info = survey.survey(tmp_path)
    assert "web UI" in info.surfaces
    assert [c.path for c in info.model_candidates] == ["pricing.py"]


def test_init_installs_agent_skills_and_hook_and_prints_the_kit(tmp_path: Path, capsys) -> None:
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--provider", "none", "--no-color") == 0
    text = capsys.readouterr().out
    assert (tmp_path / ".claude" / "skills" / "rung" / "SKILL.md").exists()
    assert "rung hook stop" in (tmp_path / ".claude" / "settings.json").read_text()
    assert "Verification kit" in text and "Next:" in text


def test_init_no_agents_leaves_claude_settings_alone(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--provider", "none", "--no-agents") == 0
    assert not (tmp_path / ".claude").exists()


def test_init_json_prints_one_document_with_the_kit(tmp_path: Path, capsys) -> None:
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    assert run(tmp_path, "init", "--provider", "none", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert {p["part"] for p in data["kit"]["parts"]} == {key for key, _, _ in kit.PARTS}
    assert data["drafted"] == []


def _shop(tmp_path: Path) -> Path:
    (tmp_path / "pricing.py").write_text("def total(prices, coupon):\n    return sum(prices) - coupon\n")
    (tmp_path / "web.py").write_text("from http.server import BaseHTTPRequestHandler\n# text/html\n")
    (tmp_path / "rung.toml").write_text('[project]\nname = "shop"\n\n[llm]\nprovider = "claude-cli"\n')
    return tmp_path


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**({"yes": False, "no_drafts": False} | kw))


def test_init_yes_drafts_each_missing_part_with_its_top_candidate(tmp_path: Path, monkeypatch) -> None:
    root = _shop(tmp_path)
    calls: list[argparse.Namespace] = []

    def fake_draft(ns, out):
        calls.append(ns)
        return 0

    monkeypatch.setattr("rung.cli.commands.draft.cmd_draft", fake_draft)
    monkeypatch.setattr(setup, "_map_first", lambda args, root, out: "")  # the map is approved: draft the script
    monkeypatch.setattr(setup, "_has", lambda probe: False)  # no Java or Lean here: no formal offers
    done = setup._draft_kit(_args(yes=True), root, survey.survey(root), load(root), True, Output())
    assert [(c.kind, c.files) for c in calls] == [("runtime", []), ("property", ["pricing.py"])]
    assert calls[0].require == [".png"]
    assert [d["exit"] for d in done] == [0, 0]


def test_init_offers_formal_models_only_when_the_checker_is_installed(tmp_path: Path, monkeypatch) -> None:
    root = _shop(tmp_path)
    (root / "worker.py").write_text(
        "def claim(job):\n    # lease, retry, retries, backoff, state, status, timeout, expire, attempt\n"
        "    state = status = lease = retry = backoff = timeout = attempt = 1\n    return state\n"
    )
    info = survey.survey(root)
    monkeypatch.setattr(setup, "_has", lambda probe: True)
    kinds = [o.kind for o in setup._offers(load(root), info)]
    assert "tla" in kinds and "lean" in kinds
    monkeypatch.setattr(setup, "_has", lambda probe: False)
    assert [o.kind for o in setup._offers(load(root), info)] == ["runtime", "property"]


@pytest.mark.parametrize("args, logged_in, tty", [
    (_args(no_drafts=True, yes=True), True, True),  # asked not to
    (_args(yes=True), False, True),                  # the provider is not logged in
    (_args(), True, False),                          # nobody to ask, and no --yes
])  # fmt: skip
def test_init_drafts_nothing_when_it_should_not(tmp_path: Path, monkeypatch, args, logged_in, tty) -> None:
    root = _shop(tmp_path)
    monkeypatch.setattr("rung.cli.commands.draft.cmd_draft", lambda ns, out: pytest.fail("drafted"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    assert setup._draft_kit(args, root, survey.survey(root), load(root), logged_in, Output()) == []


def test_a_part_already_in_the_kit_is_not_offered_again(tmp_path: Path, monkeypatch) -> None:
    root = _shop(tmp_path)
    cfg = load(root)
    cfg.checks.append(Check("runtime", "evidence", [], {"run": "python drive.py"}))
    monkeypatch.setattr(setup, "_has", lambda probe: False)
    assert [o.kind for o in setup._offers(cfg, survey.survey(root))] == ["property"]


def test_init_asks_per_part_and_drafts_only_the_file_the_person_picks(tmp_path: Path, monkeypatch) -> None:
    root = _shop(tmp_path)
    (root / "rules.py").write_text("def a(x):\n    return x\n\n\ndef b(x):\n    return -x\n")
    answers = iter(["n", "2"])  # skip the app script; pick the second candidate for property tests
    monkeypatch.setattr(setup, "ask", lambda question, default="": next(answers))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(setup, "_has", lambda probe: False)
    calls: list[argparse.Namespace] = []
    monkeypatch.setattr("rung.cli.commands.draft.cmd_draft", lambda ns, out: calls.append(ns) or 0)
    info = survey.survey(root)
    assert [c.path for c in info.model_candidates][:2] == ["rules.py", "pricing.py"]
    setup._draft_kit(_args(), root, info, load(root), True, Output())
    assert [(c.kind, c.files) for c in calls] == [("property", ["pricing.py"])]


def test_a_failing_happy_path_is_explained_to_the_model_with_what_it_observed() -> None:
    from rung.drafting.runtime import happy_path_advice

    result = {"scenarios": [
        {"name": "happy", "category": "happy", "passed": False, "assertions": [
            {"name": "no negative money amount", "expected": "none", "observed": "[-5.0]", "passed": False},
            {"name": "page loads", "passed": True}]},
        {"name": "coupon-twice", "category": "state", "passed": False, "assertions": [
            {"name": "discounted once", "passed": False}]},
    ]}  # fmt: skip
    advice = happy_path_advice(result)
    assert "happy: no negative money amount (expected 'none', observed '[-5.0]')" in advice
    assert "page loads" not in advice and "discounted once" not in advice
    assert "own scenario" in advice and "finding" in advice
    assert happy_path_advice({"scenarios": [{"name": "happy", "category": "happy", "assertions": []}]}) == ""
    assert happy_path_advice(None) == ""


def test_ui_components_are_neither_protocols_nor_pure_cores_but_money_code_is_a_protocol(tmp_path: Path) -> None:
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "grid.tsx").write_text(
        "import { useState } from 'react'\nexport function Grid() {\n"
        + "  const [state, setState] = useState(); const status = state; // retry timeout status state\n" * 4
        + "}\n"
    )
    (tmp_path / "orders.py").write_text(
        "def checkout(cart):\n    charge = payments.charge(cart)  # the order is paid once the charge settles\n"
        "    if charge.status == 'paid':\n        mark_paid(cart, charge, payment=charge)\n"
    )
    info = survey.survey(tmp_path)
    assert [c.path for c in info.protocol_candidates] == ["orders.py"]
    assert "components/grid.tsx" not in [c.path for c in info.model_candidates]


def test_the_kit_drives_the_web_ui_when_an_app_also_serves_an_api(project: Path, capsys) -> None:
    (project / "src" / "server.py").write_text("from http.server import BaseHTTPRequestHandler  # text/html\n")
    (project / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["fastapi"]\n')
    assert run(project, "kit", "--json") == 0
    app = next(p for p in json.loads(capsys.readouterr().out)["parts"] if p["part"] == "app")
    assert "web UI" in app["missing"] and "screenshots" in app["missing"]
