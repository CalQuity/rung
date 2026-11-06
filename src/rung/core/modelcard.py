"""What each formal check's model is about, and the code it was last confirmed to describe.

A TLA+ spec or a Lean model is written once and then checked forever. When the code it describes
changes, its verdict goes stale and `rung check` reruns the checker, but on the *unchanged* model, which
usually passes again. That pass is about code that no longer exists. So each `tla` and `lean` check has
a small committed record, `.rung/formal/<check>.json`, holding:

- `sources`: the covered source files (globs) the model describes;
- `confirmed`: the commit and a digest of those files when the model was last confirmed to describe
  them (by an accepted draft, `rung maintain`, or a person with `rung confirm`), with who and why;
- what the model says in plain words: `scope` (bounds, assumptions, what is not covered), the
  `functions` it mirrors, `actions` labels and `invariants` statements with the harm if each breaks;
- `failability`: how many guard mutants of the spec an invariant caught, for one spec digest.

When the sources' digest differs from the confirmed one, the model has *drifted*: the check is an
error and the laws it enforces are unproven until the model is maintained or confirmed again. Like a
verdict, the record is not a signature; it keeps honest people from trusting a stale model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rung.core import git
from rung.core.config import write_atomic
from rung.core.ledger import inputs_digest, snapshot

FORMAL_DIR = Path(".rung") / "formal"
KINDS = ("tla", "lean")
HISTORY_LIMIT = 20


def path(root: Path, check_id: str) -> Path:
    return root / FORMAL_DIR / f"{check_id}.json"


def load(root: Path, check_id: str) -> dict[str, Any] | None:
    """The record for `check_id`, or None when there is none or it cannot be read."""
    try:
        card = json.loads(path(root, check_id).read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return card if isinstance(card, dict) and card.get("check") == check_id else None


def save(root: Path, card: dict[str, Any]) -> Path:
    target = path(root, card["check"])
    write_atomic(target, json.dumps(card, indent=1, sort_keys=True) + "\n")
    return target


def has_source_confirmation(card: dict[str, Any] | None) -> bool:
    confirmed = (card or {}).get("confirmed")
    if not isinstance(confirmed, dict) or not confirmed.get("digest"):
        return False
    files = confirmed.get("files")
    return isinstance(files, dict) and any(
        isinstance(value, str) and not value.endswith("missing") and not value.startswith("external-link:")
        for value in files.values()
    )


def new(check_id: str, kind: str, sources: list[str]) -> dict[str, Any]:
    return {"check": check_id, "kind": kind, "sources": list(sources), "confirmed": {}, "scope": {}, "functions": [],
            "actions": {}, "invariants": {}, "failability": {}, "history": []}  # fmt: skip


def describe(card: dict[str, Any], reply: dict[str, Any]) -> None:
    """Copy what a model said about its spec (scope, functions, action labels, invariants) into `card`."""
    if scope := reply.get("scope"):
        card["scope"] = {k: [s.strip() for s in scope.get(k, []) if s.strip()]
                         for k in ("bounds", "assumptions", "not_covered")}  # fmt: skip
    if functions := reply.get("functions"):
        card["functions"] = [f.strip() for f in functions if f.strip()]
    if actions := reply.get("actions"):
        card["actions"] = {a["name"].strip(): a["label"].strip() for a in actions if a["name"].strip()}
    if invariants := reply.get("invariants"):
        card["invariants"] = {
            i["name"].strip(): {"statement": i["statement"].strip(), "harm": i["harm"].strip()}
            for i in invariants
            if i["name"].strip()
        }


def confirm(root: Path, card: dict[str, Any], *, by: str, reason: str, outcome: str = "confirmed") -> None:
    """Record that the model describes the covered sources as they are now."""
    files = snapshot(root, card["sources"])
    card["confirmed"] = {
        "commit": git.head(root),
        "digest": inputs_digest(files),
        "files": files,
        "by": by,
        "reason": reason,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    entry = {k: card["confirmed"][k] for k in ("commit", "by", "reason", "at")} | {"outcome": outcome}
    card["history"] = [*(card.get("history") or [])[-(HISTORY_LIMIT - 1) :], entry]


@dataclass
class Drift:
    """The covered sources changed since the model was confirmed."""

    check_id: str
    commit: str
    changed: list[str]

    @property
    def short(self) -> str:
        return self.commit[:7] or "an uncommitted state"

    @property
    def message(self) -> str:
        return f"the code changed since the model was confirmed at {self.short}; the model may no longer describe it"

    def lines(self) -> list[str]:
        return [f"changed: {f}" for f in self.changed] + [
            f'if you have checked the model still describes the code: rung confirm {self.check_id} --by "NAME" '
            '--reason "TEXT"',
            f"next: rung maintain {self.check_id}",
        ]


def _same_content(now: str | None, then: str | None) -> bool:
    """Whether two snapshot values describe the same file.

    Before 0.4, a snapshot value was the file's sha256 alone (or `missing`); now it is
    `<mode>:<sha256>`, with a `link:` prefix for symbolic links. A value in the old form matches
    the content part of a new one, so upgrading does not read as a change to every confirmed model.
    """
    if now == then:
        return True
    if now is None or then is None or ":" in then:
        return False
    return not now.startswith(("link:", "external-link:")) and now.rpartition(":")[2] == then


def changed_files(root: Path, card: dict[str, Any]) -> list[str]:
    then: dict[str, str] = card.get("confirmed", {}).get("files", {})
    now = snapshot(root, card.get("sources", []))
    return sorted(p for p in set(now) | set(then) if not _same_content(now.get(p), then.get(p)))


def _legacy(confirmed: dict[str, Any]) -> bool:
    files = confirmed.get("files")
    return isinstance(files, dict) and any(isinstance(v, str) and ":" not in v for v in files.values())


def drift(root: Path, check_id: str, card: dict[str, Any] | None = None) -> Drift | None:
    """How the covered sources moved since the model was confirmed, or None when they did not (or it never was)."""
    card = card if card is not None else load(root, check_id)
    confirmed = (card or {}).get("confirmed")
    if not card or not isinstance(confirmed, dict) or not confirmed.get("digest"):
        return None
    if inputs_digest(snapshot(root, card.get("sources", []))) == confirmed["digest"]:
        return None
    changed = changed_files(root, card)
    if not changed and _legacy(confirmed):
        return None  # confirmed before snapshots recorded file modes, and the contents still match
    return Drift(check_id, confirmed.get("commit", ""), changed or ["(covered files changed)"])


def scope_lines(card: dict[str, Any] | None) -> list[str]:
    """`bounds: ...`, `assumes: ...`, `not covered: ...`, for output."""
    scope = (card or {}).get("scope") or {}
    out = []
    for key, label in (("bounds", "bounds"), ("assumptions", "assumes"), ("not_covered", "not covered")):
        if items := scope.get(key):
            out.append(f"{label}: " + "; ".join(items))
    return out


def failability_line(card: dict[str, Any] | None, spec_digest: str = "") -> str:
    """The fail-ability score for output, or a note that it was measured for another version of the spec."""
    fa = (card or {}).get("failability") or {}
    if not fa:
        return ""
    if spec_digest and fa.get("spec_digest") != spec_digest:
        return "fail-ability: not measured for this version of the spec"
    return "fail-ability: " + fa.get("summary", "")
