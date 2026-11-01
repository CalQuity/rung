"""The evidence ledger: verdicts bound to the exact code and spec they checked.

Each entry records a check's verdict together with a content hash of every file
it covers and every spec file it ran. A verdict is only *fresh* while all those
hashes still match the working tree; editing a covered file or the spec makes it
*stale* without anyone having to remember to re-run anything. Entries are
hash-chained, so a rewritten or deleted entry breaks `verify_chain`.

The ledger lives at `.rung/ledger.jsonl` and is meant to be committed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rung.core import git

LEDGER_PATH = Path(".rung") / "ledger.jsonl"
GENESIS = "0" * 64


def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, or "missing" when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def snapshot(root: Path, patterns: list[str]) -> dict[str, str]:
    return {rel: file_digest(root / rel) for rel in git.expand(root, patterns)}


def _entry_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def read(root: Path) -> list[dict[str, Any]]:
    path = root / LEDGER_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(
    root: Path,
    *,
    check_id: str,
    kind: str,
    verdict: str,
    covers: dict[str, str],
    specs: dict[str, str],
    summary: str,
    evidence: list[str] | None = None,
    details: dict[str, Any] | None = None,
    definition: str = "",
) -> dict[str, Any]:
    """Append one verdict to the ledger and return the written entry."""
    if verdict not in ("pass", "fail", "error"):
        raise ValueError(f"verdict must be pass, fail or error, not {verdict!r}")
    entries = read(root)
    entry: dict[str, Any] = {
        "check": check_id,
        "kind": kind,
        "verdict": verdict,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": git.head(root),
        "covers": covers,
        "specs": specs,
        "definition": definition,
        "evidence": evidence or [],
        "details": details or {},
        "prev": entries[-1]["hash"] if entries else GENESIS,
    }
    entry["hash"] = _entry_hash(entry)
    path = root / LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def verify_chain(entries: list[dict[str, Any]]) -> list[str]:
    """Return one problem per broken link; an empty list means the chain is intact."""
    problems = []
    prev = GENESIS
    for i, entry in enumerate(entries, start=1):
        if entry.get("prev") != prev:
            problems.append(f"entry {i} ({entry.get('check')}): previous-hash link broken")
        if _entry_hash(entry) != entry.get("hash"):
            problems.append(f"entry {i} ({entry.get('check')}): contents do not match its hash")
        prev = entry.get("hash", "")
    return problems


@dataclass
class Status:
    check_id: str
    state: str  # fresh | stale | failing | never
    entry: dict[str, Any] | None
    changed: list[str]

    @property
    def trusted(self) -> bool:
        return self.state == "fresh"


def definition_hash(kind: str, covers: list[str], options: dict[str, Any]) -> str:
    """A hash of how a check is declared; editing its options must stale its verdict."""
    body = json.dumps({"kind": kind, "covers": covers, "options": options}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def status(root: Path, check_id: str, covers: list[str], specs: list[str], definition: str = "") -> Status:
    """Compare a check's latest verdict with the files, and its declaration, as they are now."""
    latest = next((e for e in reversed(read(root)) if e["check"] == check_id), None)
    if latest is None:
        return Status(check_id, "never", None, [])
    if latest["verdict"] != "pass":
        return Status(check_id, "failing", latest, [])
    now = snapshot(root, covers) | snapshot(root, specs)
    then = latest["covers"] | latest["specs"]
    changed = sorted(p for p in set(now) | set(then) if now.get(p) != then.get(p))
    if definition and latest.get("definition", definition) != definition:
        changed.append("(the check's definition in rung.toml)")
    return Status(check_id, "stale" if changed else "fresh", latest, changed)
