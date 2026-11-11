from __future__ import annotations

from pathlib import Path

from rung.core import laws
from rung.core.ledger import Status

SOURCE = """# Laws

## result-from-holder
A job's result always comes from the
worker that holds its lease.
- enforced by: protocol, model
- status: approved

## proposed-one
Something an agent suggested.
- enforced by: protocol
- status: proposed
"""


def _status(state: str) -> Status:
    return Status("x", state, None, [])


def test_parse_joins_wrapped_text() -> None:
    first, second = laws.parse(SOURCE)
    assert first.id == "result-from-holder"
    assert first.text == "A job's result always comes from the worker that holds its lease."
    assert first.enforced_by == ["protocol", "model"]
    assert second.status == "proposed"


def test_law_holds_only_when_every_check_is_fresh() -> None:
    law = laws.parse(SOURCE)[0]
    law.approval = laws.approval_digest(law)
    assert laws.state(law, {"protocol": _status("fresh"), "model": _status("fresh")})[0] == "held"
    assert laws.state(law, {"protocol": _status("fresh"), "model": _status("stale")})[0] == "unproven"
    assert laws.state(law, {"protocol": _status("failing"), "model": _status("fresh")})[0] == "broken"
    assert laws.state(law, {"protocol": _status("fresh")})[0] == "unenforced"


def test_proposed_law_never_holds() -> None:
    law = laws.parse(SOURCE)[1]
    assert laws.state(law, {"protocol": _status("fresh")})[0] == "proposed"


def test_approve_rewrites_only_that_law(tmp_path: Path) -> None:
    (tmp_path / laws.LAWS_FILE).write_text(SOURCE)
    laws.set_status(tmp_path, "proposed-one", "approved")
    parsed = {law.id: law for law in laws.load(tmp_path)}
    assert parsed["proposed-one"].status == "approved"
    assert parsed["result-from-holder"].status == "approved"


def test_finding_7_editing_an_approved_law_revokes_approval(tmp_path: Path) -> None:
    (tmp_path / "LAWS.md").write_text(SOURCE)
    laws.set_status(tmp_path, "result-from-holder", "approved")
    law = laws.load(tmp_path)[0]
    statuses = {"protocol": _status("fresh"), "model": _status("fresh")}
    assert laws.state(law, statuses)[0] == "held"
    text = (
        (tmp_path / "LAWS.md")
        .read_text()
        .replace("A job's result always comes from the", "A job's result never comes from the")
    )
    (tmp_path / "LAWS.md").write_text(text)
    assert laws.state(laws.load(tmp_path)[0], statuses)[0] == "unproven"
