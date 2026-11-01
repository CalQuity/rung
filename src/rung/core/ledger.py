"""The evidence ledger: verdicts bound to the exact code and spec they checked.

Each entry records a check's verdict together with a content hash of every file it covers and
every spec or policy file it used, a hash of the check's own declaration, the comparison base,
and when the verdict stops being valid (a waiver that expires). A verdict is *fresh* only while
all of that still matches; anything else makes it *stale*.

Entries are hash-chained. The chain detects an entry edited or removed in the middle of the
ledger. It is an integrity aid, not an authenticated audit trail: someone who can rewrite the
whole file can rehash it, and dropping the last entries is not detectable from the file alone.
Treat changes to `.rung/` like any other change: through review.

The ledger lives at `.rung/ledger.jsonl` and is meant to be committed. Writes take a file lock,
so several agents or terminals can record verdicts at once.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rung.core import git

LEDGER_PATH = Path(".rung") / "ledger.jsonl"
LOCK_PATH = Path(".rung") / "ledger.lock"
GENESIS = "0" * 64
BROKEN_CHAIN = "(the ledger's hash chain is broken; run `rung ledger`)"


def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, or "missing" when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError):
        return "missing"


def snapshot(root: Path, patterns: list[str]) -> dict[str, str]:
    return {rel: file_digest(root / rel) for rel in git.expand(root, patterns)}


def _entry_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@contextlib.contextmanager
def _locked(root: Path) -> Iterator[None]:
    """Hold an exclusive lock on the ledger (POSIX advisory lock; a no-op where unavailable)."""
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            yield
            return
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read(root: Path) -> list[dict[str, Any]]:
    path = root / LEDGER_PATH
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"check": "(unreadable line)", "hash": "", "prev": ""})
    return entries


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
    context: str = "",
    valid_until: str = "",
) -> dict[str, Any]:
    """Append one verdict to the ledger and return the written entry."""
    if verdict not in ("pass", "fail", "error"):
        raise ValueError(f"verdict must be pass, fail or error, not {verdict!r}")
    with _locked(root):
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
            "context": context,
            "valid_until": valid_until,
            "evidence": evidence or [],
            "details": details or {},
            "prev": entries[-1].get("hash", "") if entries else GENESIS,
        }
        entry["hash"] = _entry_hash(entry)
        path = root / LEDGER_PATH
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


class View:
    """The ledger read and verified once, for every status decision in one command."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries = read(root)
        self.problems = verify_chain(self.entries)
        self.latest: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            self.latest[entry.get("check", "")] = entry

    def status(
        self, check_id: str, covers: list[str], specs: list[str], definition: str = "", context: str | None = None
    ) -> Status:
        """Compare a check's latest verdict with the files, its declaration and its context as they are now."""
        latest = self.latest.get(check_id)
        if latest is None:
            return Status(check_id, "never", None, [])
        if self.problems:
            return Status(check_id, "stale", latest, [BROKEN_CHAIN])
        if latest.get("verdict") != "pass":
            return Status(check_id, "failing", latest, [])
        now = snapshot(self.root, covers) | snapshot(self.root, specs)
        then = latest.get("covers", {}) | latest.get("specs", {})
        changed = sorted(p for p in set(now) | set(then) if now.get(p) != then.get(p))
        if definition and latest.get("definition", definition) != definition:
            changed.append("(the check's definition in rung.toml)")
        if context is not None and latest.get("context", "") != context:
            changed.append(f"(verified against {latest.get('context') or 'no base'}, not {context or 'no base'})")
        if (until := latest.get("valid_until")) and datetime.now(UTC).date().isoformat() > until:
            changed.append(f"(a waiver it relied on expired on {until})")
        return Status(check_id, "stale" if changed else "fresh", latest, changed)


def status(
    root: Path, check_id: str, covers: list[str], specs: list[str], definition: str = "", context: str | None = None
) -> Status:
    """One check's status; use `View` when deciding for several checks at once."""
    return View(root).status(check_id, covers, specs, definition, context)
