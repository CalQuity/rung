"""The formal-methods part of the report: for each TLA+ and Lean check, what its model covers and assumes,
the code it was confirmed to describe, whether that code has drifted, and how able to fail it is."""

from __future__ import annotations

from typing import Any

from rung.core import ledger, modelcard
from rung.core.config import Config


def rows(config: Config, statuses: dict[str, ledger.Status]) -> list[dict[str, Any]]:
    out = []
    for check in config.checks:
        card = modelcard.load(config.root, check.id) if check.kind in modelcard.KINDS else None
        if card is None:
            continue
        details = ((statuses[check.id].entry or {}).get("details")) or {}
        drift = modelcard.drift(config.root, check.id, card)
        out.append({
            "id": check.id,
            "source_confirmed": modelcard.has_source_confirmation(card) and statuses[check.id].trusted
                                and statuses[check.id].implementation_link == "source-confirmed",
            "confirmed": (card.get("confirmed") or {}).get("commit", ""),
            "confirmed_by": (card.get("confirmed") or {}).get("by", ""),
            "drifted": drift.changed if drift else [],
            "scope": card.get("scope") or {},
            "failability": (details.get("failability") or {}).get("summary", "") if statuses[check.id].trusted else "",
        })  # fmt: skip
    return out


def markdown(formal: list[dict[str, Any]]) -> list[str]:
    if not formal:
        return []
    out = ["**Formal models**", ""]
    for row in formal:
        if not row["source_confirmed"]:
            out.append(f"- `{row['id']}`: unconfirmed/model-only or stale; no current implementation claim.")
        else:
            state = (f"🟠 the code changed since it was confirmed ({', '.join(row['drifted'])}); run "
                 f"`rung maintain {row['id']}`" if row["drifted"] else "describes the code")  # fmt: skip
            out.append(f"- `{row['id']}`: confirmed at `{row['confirmed'][:7] or 'uncommitted'}` "
                   f"by {row['confirmed_by']}; {state}")  # fmt: skip
        scope = row["scope"]
        for key, label in (("bounds", "Bounds"), ("assumptions", "Assumes"), ("not_covered", "Not covered")):
            if scope.get(key):
                out.append(f"  - {label}: {'; '.join(scope[key])}")
        if row["failability"]:
            out.append(f"  - Fail-ability: {row['failability']}")
    out.append("")
    return out
