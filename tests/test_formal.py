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
def shop(repo: Path, monkeypatch) -> Path:
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
    laws.set_status(repo, "charge-once", "approved")
    monkeypatch.setattr(tla_mutate, "run", _caught)
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
    assert tla_mutate.classify(tla.TLCResult("fail", "x", "AtMostOnce"), ["AtMostOnce"])[0] == "caught"
    assert tla_mutate.classify(tla.TLCResult("fail", "x", "TypeOK"))[0] == "survived"
    assert tla_mutate.classify(tla.TLCResult("pass", "x", distinct_states=4))[0] == "survived"
    assert tla_mutate.classify(tla.TLCResult("error", "TLC timed out after 60s"))[0] == "inconclusive"
    survived = tla_mutate.FailAbility("d", [tla_mutate.MutantResult("A", "g", "survived", "")])
    assert not survived.ok and "can be removed without any invariant noticing" in tla_mutate.repair_request(survived)
    assert not tla_mutate.FailAbility("d").ok  # untested is not evidence


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
    monkeypatch.setattr(tla, "run_process", fake)
    monkeypatch.setattr(tla, "module_paths", lambda spec: [spec])
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
    assert "fail-ability (selected properties only): 1/1 guard mutants caught by an invariant" in out
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


def test_maintain_updates_a_proposal_and_preserves_the_confirmed_commit(shop: Path, monkeypatch, capsys):
    _change_code(shop, "fix: hold a lock across check and charge")
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)])
    out = capsys.readouterr().out
    assert code == 1 and "UPDATED" in out and "[model-must-change]" in out
    prompt = provider.prompts[0]
    assert "+    async with lock:" in prompt and "fix: hold a lock across check and charge" in prompt
    assert "--- model file Pay.tla ---" in prompt and "a customer clicks Pay" in prompt
    assert "promise-may-be-broken" in provider.systems[0] and "never from a checker result" in provider.systems[0]
    assert (shop / "spec" / "Pay.tla").read_text() == LOCKED
    card = modelcard.load(shop, "pay")
    assert card and card["confirmed"]["commit"] != git.head(shop)
    assert card["confirmed"]["by"] == "rung draft"
    assert modelcard.drift(shop, "pay") is not None
    assert runner.status(load(shop), load(shop).check("pay")).state == "drifted"
    assert "review this proposal" in out


def test_maintain_rejects_a_weakened_invariant_without_cited_evidence(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(WEAKER), _maintain_reply(LOCKED)])
    assert code == 1 and "without citing evidence" in provider.prompts[1] and "AtMostOnce" in provider.prompts[1]
    assert (shop / "spec" / "Pay.tla").read_text() == LOCKED


def test_a_promise_changed_with_evidence_may_change_an_invariant(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    reply = _maintain_reply(WEAKER, "promise-changed", 'CHANGELOG.md:3 "a retry may charge twice; refunds follow"')
    code, provider = _maintain(shop, monkeypatch, [reply])
    out = capsys.readouterr().out
    assert code == 1 and len(provider.prompts) == 1 and "promises changed on purpose: AtMostOnce" in out
    assert "evidence: CHANGELOG.md:3" in out and "review this proposal" in out


def test_adding_an_invariant_or_a_type_conjunct_is_not_a_weakening() -> None:
    before = {"TypeOK": "/\\ a \\in S", "Safe": "/\\ x <= 1 /\\ y = 0"}
    assert maintain.weakened(before, {"TypeOK": "/\\ a \\in S /\\ b \\in T", "Safe": before["Safe"], "New": "z"}) == []
    assert maintain.weakened(before, {"TypeOK": "", "Safe": "/\\ x <= 1"}) == ["Safe"]
    assert maintain.weakened(before, {**before, "setting: state constraint": "x"}) == ["setting: state constraint"]


def test_a_counterexample_on_the_updated_proposal_still_requires_review(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)], tlc=_finds)
    out = capsys.readouterr().out
    assert code == 1 and "BUG" in out and "harm: The customer pays twice." in out
    assert "TLC: invariant AtMostOnce is violated" in out
    assert "AtMostOnce" not in "".join(provider.prompts[1:])  # the counterexample never goes back to the model
    assert modelcard.drift(shop, "pay") is not None
    assert runner.status(load(shop), load(shop).check("pay")).state == "drifted"


def test_blocked_maintenance_leaves_the_model_and_the_confirmation_alone(shop: Path, monkeypatch, capsys) -> None:
    _change_code(shop)
    code, provider = _maintain(shop, monkeypatch, [_maintain_reply(LOCKED)] * 4, tlc=_parse_error)
    out = capsys.readouterr().out
    assert code == 1 and "BLOCKED" in out and "Parse Error" in provider.prompts[1]
    assert (shop / "spec" / "Pay.tla").read_text() == SPEC
    assert (shop / ".rung" / "cache" / "maintain" / "pay" / "Pay.tla").read_text() == LOCKED
    assert modelcard.drift(shop, "pay") is not None
    assert out.rstrip().splitlines()[-1].startswith("next: review this proposal, then rung confirm pay")


def test_maintain_refuses_a_model_file_it_does_not_own(shop: Path, monkeypatch) -> None:
    _change_code(shop)
    stray = {**_maintain_reply(LOCKED), "files": _files(**{"../shop/payments.py": "x"})}
    code, provider = _maintain(shop, monkeypatch, [stray, _maintain_reply(LOCKED)])
    assert code == 1 and "only change the model's own files" in provider.prompts[1]
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


def test_an_accepted_draft_records_unconfirmed_scope_skill_and_a_verdict(repo: Path, monkeypatch, capsys) -> None:
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
    assert card and card["confirmed"] == {}
    assert card["scope"]["not_covered"] == ["gateway timeouts"]
    assert (repo / ".claude" / "skills" / "verify-payments-tla" / "SKILL.md").exists()
    assert "broken: A card is charged at most once. (AtMostOnce)" in out and "diagram: .rung/evidence/" in out
    assert "unconfirmed proposal" in out
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
    assert "review" in data["laws"][0]["why"] or "Confirm a source link" in data["laws"][0]["why"]
    assert cli.main(["-C", str(shop), "gate", "--base", "main", "--json"]) == 1
    gate = json.loads(capsys.readouterr().out)
    assert gate["results"] == []
    assert "approved law charge-once needs a confirmed source link or an implementation check" in gate["blocked"]


# Regressions from the formal audit: proposals do not renew reviewed associations.


def test_finding_6_no_effect_opinion_cannot_clear_source_drift(shop, monkeypatch, capsys):
    _change_code(shop)
    before = modelcard.load(shop, "pay")["confirmed"]
    code, _ = _maintain(shop, monkeypatch, [_maintain_reply("", "no-effect")])
    assert code == 1 and modelcard.load(shop, "pay")["confirmed"] == before
    assert modelcard.drift(shop, "pay") is not None


def test_finding_8_replacing_a_formal_model_requires_new_confirmation(shop):
    (shop / "spec" / "Pay.tla").write_text(SPEC.replace("AtMostOnce == charges <= 1", "AtMostOnce == TRUE"))
    assert modelcard.drift(shop, "pay") is not None
    assert runner.run_check(load(shop), load(shop).check("pay")).verdict == "error"


def test_finding_8_corrupt_model_record_fails_closed(shop):
    modelcard.path(shop, "pay").write_text("{broken")
    assert runner.run_check(load(shop), load(shop).check("pay")).verdict == "error"
    assert runner.status(load(shop), load(shop).check("pay")).state == "drifted"


def test_finding_9_named_drt_scripts_stale_without_explicit_specs(tmp_path):
    import sys

    script = "import sys\nfor line in sys.stdin: print('same')\n"
    for name in ("model.py", "impl.py"):
        (tmp_path / name).write_text(script)
    (tmp_path / "rung.toml").write_text(
        f'[[check]]\nid="d"\nkind="drt"\nmodel="{sys.executable} model.py"\n'
        f'impl="{sys.executable} impl.py"\nalphabet=["a"]\ncases=5\n'
    )
    config = load(tmp_path)
    assert runner.run_check(config, config.check("d")).verdict == "pass"
    (tmp_path / "impl.py").write_text(script.replace("same", "wrong"))
    assert runner.status(config, config.check("d")).state == "stale"
    assert runner.select(config, [], {"impl.py"}) == config.checks


def test_finding_13_formal_helper_changes_revoke_law_review(shop):
    laws.set_status(shop, "charge-once", "approved")
    before = laws.load(shop)[0]
    assert before.approval == before.current_review
    (shop / "spec" / "Pay.tla").write_text(SPEC.replace("charges' = charges + 1", "charges' = 0"))
    after = laws.load(shop)[0]
    assert after.approval != after.current_review
    assert laws.state(after, {})[0] == "unproven"


def test_finding_16_no_mutants_or_only_timeouts_is_untested():
    assert not tla_mutate.FailAbility("x").ok
    assert not tla_mutate.FailAbility("x", [tla_mutate.MutantResult("A", "g", "inconclusive", "timeout")]).ok
    result = tla.TLCResult("fail", "type bound", "StateType")
    assert tla_mutate.classify(result, ["Safe"])[0] != "caught"


def test_finding_16_check_recomputes_sensitivity(shop, monkeypatch):
    monkeypatch.setattr(tla, "run", _passes)
    monkeypatch.setattr(tla_mutate, "run", lambda *a, **k: tla_mutate.FailAbility("x"))
    result = runner.run_check(load(shop), load(shop).check("pay"))
    assert result.verdict == "error" and "fail-ability" in result.outcome.summary


def test_finding_17_generated_adapter_cannot_write_the_original_project(tmp_path, monkeypatch):
    import sys

    from rung.drafting.lean import _drt_check

    out = tmp_path / "model"
    out.mkdir()
    marker = tmp_path / "sentinel.txt"
    (out / "drt_adapter.py").write_text(
        f"from pathlib import Path\nimport sys\nPath({str(marker)!r}).write_text('unsafe')\n"
        "for line in sys.stdin: print('ok')\n"
    )
    lake = tmp_path / "lake"
    lake.write_text(f"#!{sys.executable}\nimport sys\nfor line in sys.stdin: print('ok')\n")
    lake.chmod(0o755)
    monkeypatch.setattr(lean, "find_lake", lambda: str(lake))
    result = _drt_check(out, tmp_path, "model", ["a"])
    assert result.verdict != "pass" and not marker.exists()


def test_finding_21_unconfirmed_report_never_claims_to_describe_code(shop, monkeypatch):
    from rung.analysis import formal

    card = modelcard.load(shop, "pay")
    card["confirmed"] = {}
    modelcard.save(shop, card)
    monkeypatch.setattr(tla, "run", _passes)
    runner.run_check(load(shop), load(shop).check("pay"))
    text = "\n".join(formal.markdown(formal.rows(load(shop), {s.check_id: s for s in runner.statuses(load(shop))})))
    assert "unconfirmed/model-only" in text and "describes the code" not in text


def test_finding_10_ignored_lean_dependency_bytes_are_receipt_inputs(tmp_path: Path) -> None:
    from rung.core import ledger

    package = tmp_path / "model" / ".lake" / "packages" / "dep"
    package.mkdir(parents=True)
    source = package / "Rules.lean"
    source.write_text("def limit := 2\n")
    (tmp_path / ".gitignore").write_text(".lake/\n")
    check = Check("l", "lean", [], {"project": "model"})
    before = ledger.snapshot(tmp_path, runner.specs(check, tmp_path))
    assert "model/.lake/packages/dep/Rules.lean" in before
    source.write_text("def limit := 1\n")
    assert before != ledger.snapshot(tmp_path, runner.specs(check, tmp_path))


def test_finding_10_default_checker_jar_identity_is_bound(shop: Path, monkeypatch) -> None:
    jar = shop / "checker.jar"
    jar.write_bytes(b"checker one")
    monkeypatch.setattr(tla, "find_jar", lambda: jar)
    check = load(shop).check("pay")
    before = runner.definition(check, shop)
    assert str(jar) in runner.tool_identity(shop, check)
    jar.write_bytes(b"checker two")
    assert before != runner.definition(check, shop)


def test_finding_13_vacuous_draft_is_not_accepted(tmp_path: Path) -> None:
    if not tla.DEFAULT_JAR.exists():
        pytest.skip("cached TLC unavailable")
    spec = (
        "---- MODULE M ----\nEXTENDS Naturals\nVARIABLE x\nInit == x = 0\n"
        "Next == x' = 1-x\nSafe == TRUE\nSpec == Init /\\ [][Next]_x\n====\n"
    )
    reply = {
        **SPEC_REPLY,
        **CARD_REPLY,
        "files": _files(**{"M.tla": spec, "M.cfg": "SPECIFICATION Spec\nINVARIANT Safe\n"}),
        "invariants": [{"name": "Safe", "statement": "x is always zero", "harm": "wrong value"}],
    }
    result = tla_draft.draft(ScriptedProvider([reply]), tmp_path, [_source(tmp_path)], tmp_path / "spec", max_rounds=1)
    assert not result.ok and not result.failability


def test_finding_16_type_only_mutant_kill_does_not_count(tmp_path: Path) -> None:
    if not tla.DEFAULT_JAR.exists():
        pytest.skip("cached TLC unavailable")
    spec, cfg = tmp_path / "M.tla", tmp_path / "M.cfg"
    spec.write_text(
        "---- MODULE M ----\nEXTENDS Naturals\nVARIABLE x\nInit == x = 0\n"
        "A == /\\ x < 1\n     /\\ x' = x + 1\nNext == A \\/ x' = x\n"
        "StateType == x \\in 0..1\nSafe == TRUE\nSpec == Init /\\ [][Next]_x\n====\n"
    )
    cfg.write_text("SPECIFICATION Spec\nINVARIANTS StateType Safe\n")
    result = tla_mutate.run(spec, cfg, timeout=5, law_properties=["Safe"])
    assert not result.ok and result.count("caught") == 0 and result.count("survived") == 1


def test_finding_8_deleting_a_previously_confirmed_record_fails_closed(shop, monkeypatch):
    monkeypatch.setattr(tla, "run", _passes)
    config = load(shop)
    assert runner.run_check(config, config.check("pay")).verdict == "pass"
    modelcard.path(shop, "pay").unlink()
    assert runner.run_check(config, config.check("pay")).verdict == "error"


def test_finding_9_external_named_script_identity_expires_a_receipt(tmp_path: Path) -> None:
    import sys

    root = tmp_path / "project"
    root.mkdir()
    script = tmp_path / "model.py"
    script.write_text("import sys\nfor line in sys.stdin: print('ok')\n")
    command = f"{sys.executable} {script}"
    (root / "rung.toml").write_text(
        f'[[check]]\nid="d"\nkind="drt"\nmodel="{command}"\nimpl="{command}"\nalphabet=["a"]\ncases=5\n'
    )
    config = load(root)
    assert runner.run_check(config, config.check("d")).verdict == "pass"
    script.write_text("import sys\nfor line in sys.stdin: print('changed')\n")
    assert runner.status(config, config.check("d")).state == "stale"


def test_finding_19_unparsed_counterexample_keeps_original_evidence(shop, monkeypatch):
    output = "Error: Temporal properties were violated.\nunsupported trace representation\n"
    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("fail", "temporal violation", output=output))
    config = load(shop)
    result = runner.run_check(config, config.check("pay"))
    assert result.verdict == "fail" and any("parsing incomplete" in line for line in result.outcome.lines)
    raw = next(path for path in result.outcome.evidence if path.endswith("tlc-output.txt"))
    assert (shop / raw).read_text() == output


def test_finding_17_generated_build_cannot_change_its_trial_inputs(tmp_path, monkeypatch):
    from rung.core import sandbox
    from rung.drafting.lean import _lean_check

    model = tmp_path / "model"
    model.mkdir()
    source = model / "Model.lean"
    source.write_text("theorem safe : True := True.intro\n")
    monkeypatch.setattr(sandbox, "validated_write_guard", lambda trial: ("unused",))

    def altered(project, theorems, **kwargs):
        (project / "Model.lean").write_text("theorem safe : False := by sorry\n")
        return lean.LeanResult("pass", "substituted input")

    monkeypatch.setattr(lean, "run", altered)
    result = _lean_check(model, tmp_path, ["safe"])
    assert result.verdict == "error" and "inputs changed" in result.reason
    assert source.read_text() == "theorem safe : True := True.intro\n"


def test_finding_17_generated_drt_cannot_edit_covered_inputs(tmp_path, monkeypatch):
    import sys

    from rung.core import sandbox

    (tmp_path / "source.py").write_text("original\n")
    (tmp_path / "model.py").write_text("import sys\nfor line in sys.stdin: print('ok')\n")
    (tmp_path / "impl.py").write_text(
        "from pathlib import Path\nimport sys\nPath('source.py').write_text('substituted')\n"
        "for line in sys.stdin: print('ok')\n"
    )
    (tmp_path / "rung.toml").write_text(
        f'[[check]]\nid="d"\nkind="drt"\nmodel="{sys.executable} model.py"\n'
        f'impl="{sys.executable} impl.py"\nalphabet=["a"]\ncases=3\ndrafted_by="test"\n'
    )
    monkeypatch.setattr(sandbox, "validated_write_guard", lambda trial: ("env",))
    config = load(tmp_path)
    result = runner.run_check(config, config.check("d"))
    assert result.verdict == "error" and "inputs changed" in result.outcome.summary
    assert (tmp_path / "source.py").read_text() == "original\n"


def test_finding_10_compiled_dependency_bytes_are_also_inputs(tmp_path: Path) -> None:
    from rung.core import ledger

    package = tmp_path / "model" / ".lake" / "packages" / "dep" / ".lake" / "build" / "lib" / "lean"
    package.mkdir(parents=True)
    compiled = package / "Rules.olean"
    compiled.write_bytes(b"compiled dependency one")
    check = Check("l", "lean", [], {"project": "model"})
    before = ledger.snapshot(tmp_path, runner.specs(check, tmp_path))
    assert compiled.relative_to(tmp_path).as_posix() in before
    compiled.write_bytes(b"compiled dependency two")
    assert before != ledger.snapshot(tmp_path, runner.specs(check, tmp_path))
