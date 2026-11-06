"""Formal models that stay honest: drift from the code they describe, `rung maintain` and `rung confirm`,
fail-ability (guard mutants), readable counterexamples, scope, and the per-check verify skill."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rung import cli
from rung.agents.install import install_formal_skill
from rung.analysis import report
from rung.checks import runner
from rung.checks.base import Context
from rung.checks.formal import LeanChecker, TLAChecker
from rung.core import git, laws, modelcard
from rung.core.config import Check, load
from rung.drafting import maintain, providers
from rung.drafting import tla as tla_draft
from rung.engines import lean, tla, tla_mutate, tla_render

from .conftest import commit_all
from .test_draft import SPEC_REPLY, ScriptedProvider, _files, _source

SPEC = """\
---- MODULE Pay ----
EXTENDS Naturals
CONSTANT Clicks
VARIABLES pc, charges, status
vars == << pc, charges, status >>

TypeOK == charges \\in 0..2

Init == /\\ pc = [c \\in Clicks |-> "idle"]
        /\\ charges = 0
        /\\ status = "unpaid"

\\* Source: shop/payments.py:6 checkout
Check(c) ==
    /\\ pc[c] = "idle"
    /\\ status # "paid"
    /\\ pc' = [pc EXCEPT ![c] = "charging"]
    /\\ UNCHANGED << charges, status >>

Charge(c) == /\\ pc[c] = "charging"
             /\\ charges' = charges + 1
             /\\ pc' = [pc EXCEPT ![c] = "marking"]
             /\\ UNCHANGED status

Mark(c) ==
    /\\ pc[c] = "marking"
    /\\ \\/ status = "unpaid"
       \\/ status = "refunded"
    /\\ status' = "paid"
    /\\ pc' = [pc EXCEPT ![c] = "done"]
    /\\ UNCHANGED charges

Terminated == /\\ \\A c \\in Clicks : pc[c] = "done"
              /\\ UNCHANGED vars

Next == (\\E c \\in Clicks : Check(c) \\/ Charge(c) \\/ Mark(c)) \\/ Terminated
Spec == Init /\\ [][Next]_vars

AtMostOnce == charges <= 1
====
"""
CFG = "SPECIFICATION Spec\nCONSTANTS Clicks = {c1, c2}\nINVARIANT TypeOK\nINVARIANT AtMostOnce\n"
PAYMENTS = 'async def checkout(order, gateway, orders):\n    if await orders.status(order) == "paid":\n        return\n'
CARD_REPLY: dict[str, Any] = {
    "actions": [{"name": "Check", "label": "a customer clicks Pay"},
                {"name": "Charge", "label": "the gateway charges the card"}],
    "invariants": [{"name": "AtMostOnce", "statement": "A card is charged at most once.",
                    "harm": "The customer pays twice."}],
    "scope": {"bounds": ["Clicks = {c1, c2}: two clicks"], "assumptions": ["each call is atomic"],
              "not_covered": ["gateway timeouts"]},
    "functions": ["shop/payments.py:1 checkout"],
}  # fmt: skip
TRACE = [
    tla.TraceState(1, "Initial predicate", {"charges": "0", "status": '"unpaid"'}),
    tla.TraceState(2, "Check(c1)", {"charges": "0", "status": '"unpaid"'}),
    tla.TraceState(3, "Charge(c1)", {"charges": "1", "status": '"unpaid"'}),
    tla.TraceState(4, "Charge(c2)", {"charges": "2", "status": '"unpaid"'}),
]


def _passes(*_a: object, **_k: object) -> tla.TLCResult:
    return tla.TLCResult("pass", "model checking completed with no error", distinct_states=9)


def _parse_error(*_a: object, **_k: object) -> tla.TLCResult:
    return tla.TLCResult("error", "Parse Error at line 3")


def _finds(*_a: object, **_k: object) -> tla.TLCResult:
    return tla.TLCResult("fail", "invariant AtMostOnce is violated", "AtMostOnce", TRACE, distinct_states=5)


def _caught(spec: Path, config: Path | None, **_: object) -> tla_mutate.FailAbility:
    return tla_mutate.FailAbility(
        tla_mutate.spec_digest(spec, config), [tla_mutate.MutantResult("Check", 'status # "paid"', "caught", "x")]
    )


@pytest.fixture
def shop(repo: Path) -> Path:
    """A repository with a checkout function and a TLA+ check whose model was confirmed at the first commit."""
    (repo / "shop").mkdir()
    (repo / "shop" / "payments.py").write_text(PAYMENTS)
    (repo / "spec").mkdir()
    (repo / "spec" / "Pay.tla").write_text(SPEC)
    (repo / "spec" / "Pay.cfg").write_text(CFG)
    (repo / "rung.toml").write_text(
        '[project]\nname = "shop"\n\n[llm]\nprovider = "none"\n\n[[check]]\nid = "pay"\nkind = "tla"\n'
        'spec = "spec/Pay.tla"\nconfig = "spec/Pay.cfg"\ncovers = ["shop/payments.py"]\n'
    )
    (repo / laws.LAWS_FILE).write_text(
        "# Laws\n\n## charge-once\nA card is charged at most once.\n- harm: The customer pays twice.\n"
        "- enforced by: pay\n- status: approved\n"
    )
    commit_all(repo, "shop")
    card = modelcard.new("pay", "tla", ["shop/payments.py"])
    modelcard.describe(card, CARD_REPLY)
    modelcard.confirm(repo, card, by="rung draft", reason="the draft was accepted")
    modelcard.save(repo, card)
    return repo


def _change_code(root: Path, message: str = "lock the order") -> None:
    (root / "shop" / "payments.py").write_text(PAYMENTS.replace("    if", "    async with lock:\n        if"))
    commit_all(root, message)


# --- drift --------------------------------------------------------------------------------------


def test_drift_names_the_changed_files_and_clears_when_confirmed(shop: Path) -> None:
    assert modelcard.drift(shop, "pay") is None
    _change_code(shop)
    drift = modelcard.drift(shop, "pay")
    assert drift and drift.changed == ["shop/payments.py"]
    assert "confirmed at" in drift.message and drift.lines()[-1] == "next: rung maintain pay"
    card = modelcard.load(shop, "pay")
    assert card
    modelcard.confirm(shop, card, by="Dev", reason="reviewed")
    modelcard.save(shop, card)
    assert modelcard.drift(shop, "pay") is None


def test_a_card_confirmed_before_file_modes_were_recorded_does_not_drift(shop: Path) -> None:
    import hashlib

    from rung.core.ledger import inputs_digest

    card = modelcard.load(shop, "pay")
    assert card
    # Before 0.4 a snapshot value was the file's sha256 alone, without its mode.
    legacy = {"shop/payments.py": hashlib.sha256(PAYMENTS.encode()).hexdigest()}
    card["confirmed"] |= {"files": legacy, "digest": inputs_digest(legacy)}
    modelcard.save(shop, card)
    assert modelcard.drift(shop, "pay") is None
    assert runner.status(load(shop), load(shop).check("pay")).state != "drifted"
    _change_code(shop)
    drift = modelcard.drift(shop, "pay")
    assert drift and drift.changed == ["shop/payments.py"]


def test_a_drifted_tla_check_is_an_error_and_never_reruns_tlc_on_the_old_spec(shop: Path, monkeypatch) -> None:
    def never(*_a: object, **_k: object) -> tla.TLCResult:
        raise AssertionError("TLC must not run on a model that may not describe the code")

    _change_code(shop)
    monkeypatch.setattr(tla, "run", never)
    check = load(shop).check("pay")
    outcome = TLAChecker().run(Context(root=shop), check)
    assert outcome.verdict == "error"
    assert outcome.summary.startswith("the code changed since the model was confirmed at ")
    assert "changed: shop/payments.py" in outcome.lines and outcome.lines[-1] == "next: rung maintain pay"
    assert outcome.details["drift"]["changed"] == ["shop/payments.py"]


def test_laws_enforced_by_a_drifted_check_are_unproven_with_the_reason(shop: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tla, "run", _passes)
    cfg = load(shop)
    assert runner.run_check(cfg, cfg.check("pay")).verdict == "pass"
    assert runner.status(cfg, cfg.check("pay")).state == "fresh"
    _change_code(shop)
    status = runner.status(cfg, cfg.check("pay"))
    assert status.state == "drifted" and not status.trusted
    (law,) = laws.load(shop)
    state, why = laws.state(law, {"pay": status})
    assert state == "unproven" and "model drift" in why and "rung maintain pay" in why
    assert cli.main(["-C", str(shop), "--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["checks"][0]["state"] == "drifted"


def test_a_record_file_never_stales_a_whole_project_check(shop: Path) -> None:
    assert git.is_rung_output(".rung/formal/pay.json")


# --- fail-ability -------------------------------------------------------------------------------


def test_guard_mutants_remove_one_unprimed_conjunct_at_a_time() -> None:
    mutants = tla_mutate.guard_mutants(SPEC)
    assert [(m.action, m.guard) for m in mutants] == [
        ("Check", 'pc[c] = "idle"'),
        ("Check", 'status # "paid"'),
        ("Charge", 'pc[c] = "charging"'),
        ("Mark", 'pc[c] = "marking"'),
        ("Mark", '\\/ status = "unpaid" \\/ status = "refunded"'),
    ]
    first = mutants[0].text
    assert '    /\\ TRUE\n    /\\ status # "paid"' in first and first.count('pc[c] = "idle"') == 0
    assert "Charge(c) == /\\ TRUE\n             /\\ charges'" in mutants[2].text  # a bullet after `==` keeps its column
    assert '\\/ status = "refunded"' not in mutants[4].text  # the guard's continuation lines go with it


def test_typeok_or_a_pass_does_not_count_as_catching_a_mutant() -> None:
    assert tla_mutate.classify(tla.TLCResult("fail", "x", "AtMostOnce"))[0] == "caught"
    assert tla_mutate.classify(tla.TLCResult("fail", "x", "TypeOK"))[0] == "survived"
    assert tla_mutate.classify(tla.TLCResult("pass", "x", distinct_states=4))[0] == "survived"
    assert tla_mutate.classify(tla.TLCResult("error", "TLC timed out after 60s"))[0] == "inconclusive"
    survived = tla_mutate.FailAbility("d", [tla_mutate.MutantResult("A", "g", "survived", "")])
    assert not survived.ok and "can be removed without any invariant noticing" in tla_mutate.repair_request(survived)
    assert tla_mutate.FailAbility("d").ok  # nothing to remove is not a failure


def test_tla_draft_sends_invariants_that_notice_no_removed_guard_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tla, "run", _passes)  # every mutant passes too: no invariant notices anything
    reply = {**SPEC_REPLY, **CARD_REPLY, "files": _files(**{"Pay.tla": SPEC, "Pay.cfg": CFG})}
    provider = ScriptedProvider([reply, reply])
    result = tla_draft.draft(provider, tmp_path, [_source(tmp_path)], tmp_path / "spec", max_rounds=2)
    assert not result.ok and "notice no removed guard" in result.message
    assert "these guards can be removed without any invariant noticing" in provider.prompts[1]
    assert 'Check: `status # "paid"`' in provider.prompts[1]


def test_a_passing_tla_draft_records_its_fail_ability_and_what_it_describes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tla, "run", _passes)
    monkeypatch.setattr(tla_mutate, "run", _caught)
    reply = {**SPEC_REPLY, **CARD_REPLY, "files": _files(**{"Pay.tla": SPEC, "Pay.cfg": CFG})}
    result = tla_draft.draft(ScriptedProvider([reply]), tmp_path, [_source(tmp_path)], tmp_path / "spec")
    assert result.ok and result.failability["summary"] == "1/1 guard mutants caught by an invariant"
    assert result.described["scope"]["bounds"] == ["Clicks = {c1, c2}: two clicks"]
    assert "plain words" in tla_draft.FORMAT and set(tla_draft.SCHEMA["required"]) >= {"actions", "scope"}


# --- readable counterexamples -------------------------------------------------------------------


def test_a_counterexample_reads_as_plain_steps_with_the_harm(shop: Path) -> None:
    card = modelcard.load(shop, "pay")
    lines = tla_render.story(TRACE, "AtMostOnce", card)
    assert lines[0] == "broken: A card is charged at most once. (AtMostOnce)"
    assert lines[1] == "harm: The customer pays twice."
    assert "  2. a customer clicks Pay (Check(c1))" in lines
    assert "       charges = 2" in lines and "  4. the gateway charges the card (Charge(c2))" in lines
    diagram = tla_render.counterexample_mermaid(TRACE, "AtMostOnce", card)
    assert diagram.startswith("stateDiagram-v2\n")
    assert "s2 --> s3 : the gateway charges the card" in diagram
    assert "note right of s4 - AtMostOnce" not in diagram and "note right of s4 : AtMostOnce broken" in diagram
    assert all(line.count('"') == 2 for line in diagram.splitlines() if "state " in line)  # values never end a label


def test_check_writes_the_counterexample_diagram_and_shows_scope(shop: Path, monkeypatch) -> None:
    monkeypatch.setattr(tla, "run", _finds)
    cfg = load(shop)
    result = runner.run_check(cfg, cfg.check("pay"))
    lines = result.outcome.lines
    assert result.verdict == "fail" and "harm: The customer pays twice." in lines
    (diagram,) = [line.removeprefix("diagram: ") for line in lines if line.startswith("diagram: ")]
    assert (shop / diagram).read_text().startswith("stateDiagram-v2") and diagram in result.outcome.evidence
    assert "scope: bounds: Clicks = {c1, c2}: two clicks" in lines
    assert lines[-1].startswith("next: replay the steps against the code")
    entry = json.loads((shop / ".rung" / "verdicts" / "pay.json").read_text())
    assert entry["details"]["scope"]["assumptions"] == ["each call is atomic"]


DOT = """\
strict digraph DiskGraph {
-1 [label="/\\\\ charges = 0\\n/\\\\ status = \\"unpaid\\"",style = filled]
-1 -> 2 [label="Check",color="black",fontcolor="black"];
2 [label="/\\\\ charges = 1\\n/\\\\ status = \\"unpaid\\""];
{rank = same; -1;}
}
"""


def test_the_explored_state_graph_is_drawn_from_tlc_dot_output(shop: Path) -> None:
    graph = tla_render.parse_dot(DOT)
    assert graph.nodes == {"-1": ["charges = 0", 'status = "unpaid"'], "2": ["charges = 1", 'status = "unpaid"']}
    assert graph.initial == ["-1"] and graph.edges == [("-1", "2", "Check")]
    text = tla_render.graph_mermaid(graph, modelcard.load(shop, "pay"))
    assert "[*] --> n1" in text and "n1 --> n2 : a customer clicks Pay" in text


def test_tlc_is_asked_for_a_dot_dump_only_when_wanted(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "No error has been found", "")

    monkeypatch.setattr(tla, "find_jar", lambda: tmp_path / "tla2tools.jar")
    monkeypatch.setattr(tla, "find_java", lambda: "java")
    monkeypatch.setattr(tla.subprocess, "run", fake)
    (tmp_path / "M.tla").write_text("x")
    tla.run(tmp_path / "M.tla")
    tla.run(tmp_path / "M.tla", dump=tmp_path / "out" / "states.dot")
    assert "-dump" not in seen[0]
    assert seen[1][seen[1].index("-dump") + 1 :][:2] == [
        "dot,actionlabels",
        str((tmp_path / "out" / "states").resolve()),
    ]


# --- laws: harm ---------------------------------------------------------------------------------


def test_laws_carry_a_harm_line(tmp_path: Path) -> None:
    added = laws.propose(tmp_path, [{"id": "once", "text": "Charged once.", "harm": "Pays twice."}], ["pay"])
    assert added == ["once"] and "- harm: Pays twice.\n" in (tmp_path / laws.LAWS_FILE).read_text()
    (law,) = laws.load(tmp_path)
    assert law.harm == "Pays twice."


def test_laws_and_report_show_harm_scope_and_fail_ability(shop: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tla, "run", _passes)
    card = modelcard.load(shop, "pay")
    assert card
    card["failability"] = _caught(shop / "spec" / "Pay.tla", shop / "spec" / "Pay.cfg").as_dict()
    modelcard.save(shop, card)
    assert cli.main(["-C", str(shop), "check", "pay"]) == 0
    out = capsys.readouterr().out
    assert "fail-ability: 1/1 guard mutants caught by an invariant" in out
    assert cli.main(["-C", str(shop), "laws"]) == 0
    out = capsys.readouterr().out
    assert "harm: The customer pays twice." in out and "pay bounds: Clicks = {c1, c2}: two clicks" in out
    text = report.markdown(report.build(load(shop)))
    assert "_Harm: The customer pays twice._" in text and "**Formal models**" in text
    assert "Fail-ability: 1/1 guard mutants caught" in text and "Assumes: each call is atomic" in text


# --- the per-check skill ------------------------------------------------------------------------


def test_the_verify_skill_has_real_paths_commands_scope_and_the_confirmed_commit(shop: Path) -> None:
    card = modelcard.load(shop, "pay")
    assert card
    path = install_formal_skill(shop, "shop", "pay", "tla", card, ["spec/Pay.tla", "spec/Pay.cfg"],
                                [("A card is charged at most once.", "The customer pays twice.")])  # fmt: skip
    text = path.read_text()
    assert path == shop / ".claude" / "skills" / "verify-pay" / "SKILL.md"
    assert text.startswith("---\nname: verify-pay\ndescription: >-\n")
    assert "`rung check pay`" in text and "`rung maintain pay`" in text and "spec/Pay.tla" in text
    assert "Clicks = {c1, c2}: two clicks" in text and "each call is atomic" in text and "gateway timeouts" in text
    assert "`shop/payments.py:1 checkout`" in text and card["confirmed"]["commit"] in text
    assert "If it breaks: The customer pays twice." in text and "replay" in text.lower()
    assert "{" not in text.replace("Clicks = {c1, c2}", "")  # every placeholder was filled


# --- rung maintain ------------------------------------------------------------------------------


def _maintain_reply(spec: str, classification: str = "model-must-change", evidence: str = "") -> dict[str, Any]:
    return {
        **CARD_REPLY,
        "changes": [
            {"change": "shop/payments.py:2 takes a lock", "classification": classification, "evidence": evidence}
        ],
        "files": _files(**{"Pay.tla": spec}) if spec else [],
        "notes": "",
    }


LOCKED = SPEC.replace('    /\\ status # "paid"\n', '    /\\ status # "paid"\n    /\\ charges = 0\n')
WEAKER = LOCKED.replace("AtMostOnce == charges <= 1", "AtMostOnce == charges <= 2")


def _maintain(shop: Path, monkeypatch, replies: list[dict[str, Any]], tlc=_passes) -> tuple[int, ScriptedProvider]:
    provider = ScriptedProvider(replies)
    monkeypatch.setattr(providers, "get", lambda llm: provider)
    monkeypatch.setattr(tla, "run", tlc)
    monkeypatch.setattr(tla_mutate, "run", _caught)
    code = cli.main(["-C", str(shop), "maintain", "pay"])
    return code, provider


def test_maintain_updates_the_model_from_the_diff_and_moves_the_confirmed_commit(shop: Path, monkeypatch, capsys):
    _change_code(shop, "fix: hold a lock across check and charge")
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)])
    out = capsys.readouterr().out
    assert code == 0 and "UPDATED" in out and "[model-must-change]" in out
    prompt = provider.prompts[0]
    assert "+    async with lock:" in prompt and "fix: hold a lock across check and charge" in prompt
    assert "--- model file Pay.tla ---" in prompt and "a customer clicks Pay" in prompt
    assert "promise-may-be-broken" in provider.systems[0] and "never from a checker result" in provider.systems[0]
    assert (shop / "spec" / "Pay.tla").read_text() == LOCKED
    card = modelcard.load(shop, "pay")
    assert card and card["confirmed"]["commit"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=shop, text=True).strip()  # fmt: skip
    assert card["confirmed"]["by"].startswith("rung maintain") and card["history"][-1]["outcome"] == "updated"
    assert modelcard.drift(shop, "pay") is None
    assert runner.status(load(shop), load(shop).check("pay")).state == "fresh"
    assert card["confirmed"]["commit"] in (shop / ".claude" / "skills" / "verify-pay" / "SKILL.md").read_text()


def test_maintain_rejects_a_weakened_invariant_without_cited_evidence(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(WEAKER), _maintain_reply(LOCKED)])
    assert code == 0 and "without citing evidence" in provider.prompts[1] and "AtMostOnce" in provider.prompts[1]
    assert (shop / "spec" / "Pay.tla").read_text() == LOCKED


def test_a_promise_changed_with_evidence_may_change_an_invariant(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    reply = _maintain_reply(WEAKER, "promise-changed", 'CHANGELOG.md:3 "a retry may charge twice; refunds follow"')
    code, provider = _maintain(shop, monkeypatch, [reply])
    out = capsys.readouterr().out
    assert code == 0 and len(provider.prompts) == 1 and "promises changed on purpose: AtMostOnce" in out
    assert "evidence: CHANGELOG.md:3" in out and "still say what you mean" in out


def test_adding_an_invariant_or_a_type_conjunct_is_not_a_weakening() -> None:
    before = {"TypeOK": "/\\ a \\in S", "Safe": "/\\ x <= 1 /\\ y = 0"}
    assert maintain.weakened(before, {"TypeOK": "/\\ a \\in S /\\ b \\in T", "Safe": before["Safe"], "New": "z"}) == []
    assert maintain.weakened(before, {"TypeOK": "", "Safe": "/\\ x <= 1"}) == ["Safe"]
    assert maintain.weakened(before, {**before, "setting: state constraint": "x"}) == ["setting: state constraint"]


def test_a_counterexample_on_the_updated_model_is_a_bug_and_still_confirms(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)], tlc=_finds)
    out = capsys.readouterr().out
    assert code == 1 and "BUG" in out and "harm: The customer pays twice." in out
    assert "TLC: invariant AtMostOnce is violated" in out
    assert "AtMostOnce" not in "".join(provider.prompts[1:])  # the counterexample never goes back to the model
    assert modelcard.drift(shop, "pay") is None  # the model was updated, so it describes the code
    assert runner.status(load(shop), load(shop).check("pay")).state == "failing"


def test_blocked_maintenance_leaves_the_model_and_the_confirmation_alone(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)] * 4, tlc=_parse_error)
    out = capsys.readouterr().out
    assert code == 1 and "BLOCKED" in out and "Parse Error" in provider.prompts[1]
    assert (shop / "spec" / "Pay.tla").read_text() == SPEC
    assert (shop / ".rung" / "cache" / "maintain" / "pay" / "Pay.tla").read_text() == LOCKED
    assert modelcard.drift(shop, "pay") is not None
    assert out.rstrip().splitlines()[-1].startswith("next: fix the model by hand, then rung confirm pay")


def test_maintain_refuses_a_model_file_it_does_not_own(shop: Path, monkeypatch) -> None:
    _change_code(shop)
    stray = {**_maintain_reply(LOCKED), "files": _files(**{"../shop/payments.py": "x"})}
    code, provider = _maintain(shop, monkeypatch, [stray, _maintain_reply(LOCKED)])
    assert code == 0 and "only change the model's own files" in provider.prompts[1]
    assert (shop / "shop" / "payments.py").read_text() != "x\n"


def test_maintain_with_unchanged_code_reports_in_sync_without_a_model(shop: Path, monkeypatch, capsys) -> None:
    code, provider = _maintain(shop, monkeypatch, [])
    assert code == 0 and "IN SYNC" in capsys.readouterr().out and provider.prompts == []


# --- rung confirm -------------------------------------------------------------------------------


def test_confirm_records_who_and_why_and_clears_the_drift(shop: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tla, "run", _passes)
    _change_code(shop)
    assert cli.main(["-C", str(shop), "check", "pay"]) == 1
    assert "next: rung maintain pay" in capsys.readouterr().out
    code = cli.main(["-C", str(shop), "confirm", "pay", "--by", "Dev", "--reason", "docstring only"])
    out = capsys.readouterr().out
    assert code == 0 and "Confirmed pay describes the code at" in out and "PASS  pay" in out
    card = modelcard.load(shop, "pay")
    assert card and (card["confirmed"]["by"], card["confirmed"]["reason"]) == ("Dev", "docstring only")
    assert card["history"][-1]["outcome"] == "confirmed by a person"


def test_confirm_and_maintain_apply_only_to_formal_checks(project: Path, capsys) -> None:
    assert cli.main(["-C", str(project), "confirm", "unit", "--by", "Dev", "--reason", "x"]) == 2
    assert "works on tla and lean checks" in capsys.readouterr().err
    assert cli.main(["-C", str(project), "maintain", "unit"]) == 2


# --- rung draft acceptance ----------------------------------------------------------------------


def test_an_accepted_draft_records_confirmation_scope_skill_and_a_verdict(repo: Path, monkeypatch, capsys) -> None:
    (repo / "shop").mkdir()
    (repo / "shop" / "payments.py").write_text(PAYMENTS)
    (repo / "rung.toml").write_text('[project]\nname = "shop"\n\n[llm]\nprovider = "none"\n')
    commit_all(repo, "shop")
    reply = {**SPEC_REPLY, **CARD_REPLY, "files": _files(**{"Pay.tla": SPEC, "Pay.cfg": CFG})}
    monkeypatch.setattr(providers, "get", lambda llm: ScriptedProvider([reply]))
    monkeypatch.setattr(tla, "run", _finds)
    assert cli.main(["-C", str(repo), "draft", "tla", "shop/payments.py"]) == 0
    out = capsys.readouterr().out
    card = modelcard.load(repo, "payments-tla")
    assert card and card["confirmed"]["files"] == {"shop/payments.py": card["confirmed"]["files"]["shop/payments.py"]}
    assert card["confirmed"]["commit"] and card["scope"]["not_covered"] == ["gateway timeouts"]
    assert (repo / ".claude" / "skills" / "verify-payments-tla" / "SKILL.md").exists()
    assert "broken: A card is charged at most once. (AtMostOnce)" in out and "diagram: .rung/evidence/" in out
    assert "confirmed: the model describes the code at" in out
    assert (repo / ".rung" / "verdicts" / "payments-tla.json").exists()


# --- Lean ---------------------------------------------------------------------------------------


def test_a_drifted_lean_check_is_an_error_before_anything_builds(shop: Path, monkeypatch) -> None:
    def never(*_a: object, **_k: object) -> lean.LeanResult:
        raise AssertionError("lake must not build a model that may not describe the code")

    card = modelcard.new("model", "lean", ["shop/payments.py"])
    modelcard.describe(card, {"scope": {"bounds": [], "assumptions": ["amounts are whole cents"], "not_covered": []}})
    modelcard.confirm(shop, card, by="rung draft", reason="accepted")
    modelcard.save(shop, card)
    _change_code(shop)
    monkeypatch.setattr(lean, "run", never)
    outcome = LeanChecker().run(Context(root=shop), Check("model", "lean", [], {"project": "model"}))
    assert outcome.verdict == "error" and outcome.lines[-1] == "next: rung maintain model"


def test_lean_theorem_statements_are_what_maintain_protects(tmp_path: Path) -> None:
    (tmp_path / "M.lean").write_text("theorem total_nonneg (xs : List Nat) : 0 ≤ total xs := by\n  omega\n")
    assert lean.theorem_statements(tmp_path) == {"total_nonneg": "(xs : List Nat) : 0 ≤ total xs"}


def test_a_provider_failure_mid_maintenance_leaves_the_model_as_it_was(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    # The first reply is written and rejected (a weakened invariant); the provider then fails.
    code, _ = _maintain(shop, monkeypatch, [_maintain_reply(WEAKER)])
    assert code == 2 and "unexpected error" in capsys.readouterr().err
    assert (shop / "spec" / "Pay.tla").read_text() == SPEC


def test_model_only_pass_cannot_satisfy_an_approved_implementation_law(shop, monkeypatch, capsys):
    modelcard.path(shop, "pay").unlink()
    monkeypatch.setattr(tla, "run", _passes)
    assert cli.main(["-C", str(shop), "check", "pay", "--json"]) == 0
    check = json.loads(capsys.readouterr().out)["results"][0]
    assert check["verdict"] == "pass" and check["implementation_link"] == "model-only"
    data = report.build(load(shop))
    assert data["laws"][0]["state"] == "unproven"
    assert "Confirm a source link" in data["laws"][0]["why"]
    assert cli.main(["-C", str(shop), "gate", "--base", "main", "--json"]) == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["results"] == []
    assert gate["blocked"] == ["approved law charge-once needs a confirmed source link or an implementation check"]
