"""LAWS.md: the rules the software must never break, in plain language.

Bend 2 introduced the idea of a LAWS file whose rules the compiler forces an AI
to prove. Rung keeps that contract but works with any language and any checker:
each law is a sentence a person can read and approve, mapped to the checks
(TLA+ invariants, Lean theorems, differential tests, test suites) that enforce
it. A law is only *held* when it is approved and every enforcing check has a
fresh passing verdict.

Format:

    ## lease-result-from-holder
    A job's result is always written by the worker that holds a valid lease.
    - harm: a worker whose lease expired overwrites another worker's result.
    - enforced by: lease-protocol, lease-model-proofs, impl-matches-model
    - status: approved

`harm` (optional) says, in one sentence, what goes wrong for a person if the law breaks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rung.core.ledger import Status

LAWS_FILE = "LAWS.md"
_HEADING = re.compile(r"^##\s+(\S+)\s*$")
_FIELD = re.compile(r"^-\s*(enforced by|status|harm):\s*(.*)$", re.I)

TEMPLATE = """# Laws

Rules this software must never break. Each law names the Rung checks that
enforce it; `rung laws` shows which laws currently hold. A law an agent proposes
stays `proposed` until a person reads it and changes it to `approved`.
"""


@dataclass
class Law:
    id: str
    text: str
    enforced_by: list[str] = field(default_factory=list)
    status: str = "proposed"
    harm: str = ""  # what goes wrong for a person if it breaks


def parse(source: str) -> list[Law]:
    laws: list[Law] = []
    current: Law | None = None
    for line in source.splitlines():
        if m := _HEADING.match(line):
            current = Law(m.group(1), "")
            laws.append(current)
            continue
        if current is None:
            continue
        if m := _FIELD.match(line.strip()):
            key, value = m.group(1).lower(), m.group(2).strip()
            if key == "enforced by":
                current.enforced_by = [v.strip() for v in value.split(",") if v.strip()]
            elif key == "harm":
                current.harm = value
            else:
                current.status = value.lower() or "proposed"
        elif line.strip() and not line.startswith("#"):
            current.text = f"{current.text} {line.strip()}".strip()
    return laws


def load(root: Path) -> list[Law]:
    path = root / LAWS_FILE
    return parse(path.read_text()) if path.exists() else []


def propose(root: Path, proposals: list[dict[str, str]], enforced_by: list[str]) -> list[str]:
    """Append laws as `proposed`, skipping ids that already exist. Returns the ids added."""
    path = root / LAWS_FILE
    existing = {law.id for law in load(root)}
    text = path.read_text() if path.exists() else TEMPLATE
    added = []
    for item in proposals:
        law_id = re.sub(r"[^a-z0-9-]+", "-", item.get("id", "").lower()).strip("-")
        sentence = " ".join(item.get("text", "").split())
        harm = " ".join(item.get("harm", "").split())
        if not law_id or not sentence or law_id in existing:
            continue
        text = (
            text.rstrip()
            + f"\n\n## {law_id}\n{sentence}\n"
            + (f"- harm: {harm}\n" if harm else "")
            + f"- enforced by: {', '.join(enforced_by)}\n- status: proposed\n"
        )
        existing.add(law_id)
        added.append(law_id)
    if added:
        path.write_text(text)
    return added


def set_status(root: Path, law_id: str, status: str) -> None:
    path = root / LAWS_FILE
    lines = path.read_text().splitlines()
    inside = False
    for i, line in enumerate(lines):
        if m := _HEADING.match(line):
            inside = m.group(1) == law_id
            continue
        if inside and _FIELD.match(line.strip()) and line.strip().lower().startswith("- status"):
            lines[i] = f"- status: {status}"
            path.write_text("\n".join(lines) + "\n")
            return
    raise KeyError(f"law {law_id!r} not found in {LAWS_FILE}")


def state(law: Law, statuses: dict[str, Status]) -> tuple[str, str]:
    """Return (state, explanation) for one law given the current check statuses."""
    if law.status != "approved":
        return "proposed", "awaiting a person's approval"
    if not law.enforced_by:
        return "unenforced", "no check enforces it"
    unknown = [c for c in law.enforced_by if c not in statuses]
    if unknown:
        return "unenforced", f"unknown check(s): {', '.join(unknown)}"
    failing = [c for c in law.enforced_by if statuses[c].state == "failing"]
    if failing:
        return "broken", f"failing: {', '.join(failing)}"
    drifted = [statuses[c] for c in law.enforced_by if statuses[c].state == "drifted"]
    if drifted:
        why = "; ".join(f"{s.check_id}: {s.changed[0]}" if s.changed else s.check_id for s in drifted)
        return "unproven", f"model drift ({why}); run `rung maintain {drifted[0].check_id}`"
    pending = [c for c in law.enforced_by if statuses[c].state != "fresh"]
    if pending:
        return "unproven", f"stale or never run: {', '.join(pending)}"
    return "held", "every enforcing check is fresh and passing"
