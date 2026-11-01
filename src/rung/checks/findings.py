"""Baselines and waivers: how a team adopts a scanner without drowning in old findings.

Turning on a security scanner in a real codebase usually produces hundreds of
existing findings, and a gate that fails on all of them gets switched off. Rung
gates on what a change *introduces* instead. The comparison point is either the
merge base of the branch (computed fresh) or a committed baseline file recorded
deliberately with `rung baseline`.

A finding that must stay can be waived, but only with a reason, an approver and
an expiry date. Expired waivers stop applying and are reported, so "temporary"
exceptions cannot quietly become permanent.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from rung.core.config import toml_value, write_atomic
from rung.engines.sarif import Finding, at_least, diff

BASELINE_DIR = Path(".rung") / "baselines"
WAIVERS_FILE = Path(".rung") / "waivers.toml"


def baseline_path_rel(check_id: str) -> str:
    return (BASELINE_DIR / f"{check_id}.json").as_posix()


def baseline_path(root: Path, check_id: str) -> Path:
    return root / baseline_path_rel(check_id)


def load_baseline(root: Path, check_id: str) -> list[Finding] | None:
    path = baseline_path(root, check_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return [Finding.from_dict(f) for f in data.get("findings", [])]


def save_baseline(root: Path, check_id: str, findings: list[Finding], commit: str) -> Path:
    path = baseline_path(root, check_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "check": check_id,
        "recorded": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "findings": [f.to_dict() for f in sorted(findings, key=lambda f: (f.path, f.rule, f.fingerprint))],
    }
    write_atomic(path, json.dumps(payload, indent=2) + "\n")
    return path


@dataclass
class Waiver:
    check: str
    fingerprint: str
    reason: str
    approved_by: str
    expires: date

    def active(self, today: date) -> bool:
        return today <= self.expires


def load_waivers(root: Path) -> list[Waiver]:
    path = root / WAIVERS_FILE
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text())
    out = []
    for w in raw.get("waiver", []):
        expires = w["expires"]
        if isinstance(expires, str):
            expires = date.fromisoformat(expires)
        out.append(Waiver(w["check"], w["fingerprint"], w["reason"], w["approved_by"], expires))
    return out


def add_waiver(root: Path, waiver: Waiver) -> Path:
    if not waiver.reason.strip():
        raise ValueError("a waiver needs a reason")
    path = root / WAIVERS_FILE
    existing = path.read_text() if path.exists() else ""
    block = (
        "\n[[waiver]]\n"
        f"check = {toml_value(waiver.check)}\n"
        f"fingerprint = {toml_value(waiver.fingerprint)}\n"
        f"reason = {toml_value(waiver.reason)}\n"
        f"approved_by = {toml_value(waiver.approved_by)}\n"
        f"expires = {waiver.expires.isoformat()}\n"
    )
    text = existing + block
    tomllib.loads(text)  # never write a waivers file Rung could not read back
    write_atomic(path, text)
    return path


@dataclass
class Triage:
    blocking: list[Finding] = field(default_factory=list)
    nonblocking: list[Finding] = field(default_factory=list)
    waived: list[Finding] = field(default_factory=list)
    resolved: list[Finding] = field(default_factory=list)
    expired: list[Waiver] = field(default_factory=list)
    compared_with: str = "nothing (every finding counts as new)"
    valid_until: str = ""  # the earliest expiry among waivers this result relied on


def triage(
    check_id: str,
    head: list[Finding],
    base: list[Finding] | None,
    waivers: list[Waiver],
    *,
    fail_on: str,
    today: date | None = None,
    compared_with: str = "",
) -> Triage:
    """Split the head run's findings into what blocks, what does not, and what is waived."""
    today = today or date.today()
    mine = [w for w in waivers if w.check == check_id]
    active = {w.fingerprint for w in mine if w.active(today)}
    expired = [w for w in mine if not w.active(today)]
    introduced, resolved = diff(base or [], head)
    # A finding that already existed below the threshold and now meets it has escalated: it blocks too.
    base_levels: dict[str, str] = {}
    for f in base or []:
        if f.fingerprint not in base_levels or at_least(f.level, base_levels[f.fingerprint]):
            base_levels[f.fingerprint] = f.level
    new_ids = {id(f) for f in introduced}
    escalated = [
        f for f in head
        if id(f) not in new_ids and f.fingerprint in base_levels
        and at_least(f.level, fail_on) and not at_least(base_levels[f.fingerprint], fail_on)
    ]  # fmt: skip
    result = Triage(resolved=resolved, expired=expired)
    if compared_with:
        result.compared_with = compared_with
    for f in [*introduced, *escalated]:
        if f.fingerprint in active:
            result.waived.append(f)
        elif at_least(f.level, fail_on):
            result.blocking.append(f)
        else:
            result.nonblocking.append(f)
    used = [w.expires for w in mine if w.active(today) and any(f.fingerprint == w.fingerprint for f in result.waived)]
    result.valid_until = min(used).isoformat() if used else ""
    return result
