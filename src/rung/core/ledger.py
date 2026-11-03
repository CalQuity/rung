"""Verdicts: each check's latest result, bound to the exact inputs it checked.

Each check has one small file, `.rung/verdicts/<check>.json`, holding its latest verdict: the
outcome, a single digest over every covered and spec file (path and content), a hash of the check's
own declaration, the comparison base, and when it stops being valid (a waiver that expires). A
verdict is *fresh* only while all of that still matches and it is younger than the check's
`max_age_days`.

These files are meant to be committed, so teammates and CI see what was verified. One file per
check means two branches conflict only when both ran the same check, and a conflicted, truncated or
hand-edited file simply reads as "re-run this check". Each file carries a hash of its own contents,
which detects corruption and careless edits. It is not a signature: anyone who can write the file
can forge it, so CI should re-run the checks a change touches (`rung check --changed`) rather than
trust committed verdicts for them.

Per-file digests (to explain *which* file changed) and the full history of runs are kept in the
git-ignored `.rung/cache/`, so the committed files stay a few hundred bytes each.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rung.core import git
from rung.core.config import write_atomic

VERDICTS_DIR = Path(".rung") / "verdicts"
CACHE_DIR = Path(".rung") / "cache"
HISTORY_PATH = CACHE_DIR / "history.jsonl"
CORRUPT = "(the verdict file is corrupt or was edited; re-run the check)"


def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, or "missing" when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return "missing"


def snapshot(root: Path, patterns: list[str]) -> dict[str, str]:
    return {rel: file_digest(root / rel) for rel in git.expand(root, patterns)}


def inputs_digest(files: dict[str, str]) -> str:
    """One digest over every (path, content digest) pair, in sorted order."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(f"{path}\0{files[path]}\n".encode())
    return h.hexdigest()


def definition_hash(kind: str, covers: list[str], options: dict[str, Any]) -> str:
    """A hash of how a check is declared; editing its options must stale its verdict."""
    body = json.dumps({"kind": kind, "covers": covers, "options": options}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _self_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _file_name(check_id: str) -> str:
    # Proof ids look like `proof:<slug>`; ids are otherwise already filename-safe.
    return check_id.replace(":", "--") + ".json"


def verdict_path(root: Path, check_id: str) -> Path:
    return root / VERDICTS_DIR / _file_name(check_id)


def record(
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
    """Write a check's latest verdict, cache its per-file inputs, and append it to the local history."""
    if verdict not in ("pass", "fail", "error"):
        raise ValueError(f"verdict must be pass, fail or error, not {verdict!r}")
    files = covers | specs
    entry: dict[str, Any] = {
        "check": check_id,
        "kind": kind,
        "verdict": verdict,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": git.head(root),
        "inputs": {"digest": inputs_digest(files), "files": len(files)},
        "definition": definition,
        "context": context,
        "valid_until": valid_until,
        "evidence": evidence or [],
        "details": details or {},
    }
    entry["hash"] = _self_hash(entry)
    write_atomic(verdict_path(root, check_id), json.dumps(entry, indent=1, sort_keys=True) + "\n")
    write_atomic(root / CACHE_DIR / "inputs" / _file_name(check_id), json.dumps(files, sort_keys=True))
    history = root / HISTORY_PATH
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def _load(path: Path) -> dict[str, Any]:
    """A verdict file, or `{"invalid": reason}` if it cannot be trusted."""
    try:
        entry = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"invalid": "not valid JSON (a merge conflict or a truncated write?)"}
    if not isinstance(entry, dict) or not isinstance(entry.get("check"), str):
        return {"invalid": "not a verdict"}
    if _self_hash(entry) != entry.get("hash"):
        return {"invalid": "contents do not match the file's own hash (edited by hand?)", "check": entry["check"]}
    return entry


def read_all(root: Path) -> dict[str, dict[str, Any]]:
    """Every verdict file, keyed by check id (or by file name when the file is unreadable)."""
    out: dict[str, dict[str, Any]] = {}
    directory = root / VERDICTS_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        entry = _load(path)
        out[entry.get("check") or path.stem] = entry | {"_file": path.relative_to(root).as_posix()}
    return out


def problems(root: Path) -> list[str]:
    return [f"{e['_file']}: {e['invalid']}" for e in read_all(root).values() if "invalid" in e]


def history(root: Path) -> list[dict[str, Any]]:
    path = root / HISTORY_PATH
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@dataclass
class Status:
    check_id: str
    state: str  # fresh | stale | failing | never | drifted (a formal model's code changed since it was confirmed)
    entry: dict[str, Any] | None
    changed: list[str]

    @property
    def trusted(self) -> bool:
        return self.state == "fresh"


class View:
    """All verdicts read once, for every status decision in one command."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.verdicts = read_all(root)

    def _changed_files(self, check_id: str, now: dict[str, str]) -> list[str]:
        cached = self.root / CACHE_DIR / "inputs" / _file_name(check_id)
        try:
            then = json.loads(cached.read_text())
        except (OSError, json.JSONDecodeError):
            return ["(covered files changed)"]
        return sorted(p for p in set(now) | set(then) if now.get(p) != then.get(p)) or ["(covered files changed)"]

    def status(
        self,
        check_id: str,
        covers: list[str],
        specs: list[str],
        definition: str = "",
        context: str | None = None,
        max_age_days: int | None = None,
    ) -> Status:
        """Compare a check's verdict with its inputs, declaration, context and age as they are now."""
        entry = self.verdicts.get(check_id)
        if entry is None:
            return Status(check_id, "never", None, [])
        if "invalid" in entry:
            return Status(check_id, "stale", entry, [CORRUPT])
        if entry.get("verdict") != "pass":
            return Status(check_id, "failing", entry, [])
        changed: list[str] = []
        now = snapshot(self.root, covers) | snapshot(self.root, specs)
        if inputs_digest(now) != entry.get("inputs", {}).get("digest"):
            changed += self._changed_files(check_id, now)
        if definition and entry.get("definition", definition) != definition:
            changed.append("(the check's definition in rung.toml)")
        if context is not None and entry.get("context", "") != context:
            changed.append(f"(verified against {entry.get('context') or 'no base'}, not {context or 'no base'})")
        today = datetime.now(UTC)
        if (until := entry.get("valid_until")) and today.date().isoformat() > until:
            changed.append(f"(a waiver it relied on expired on {until})")
        if max_age_days is not None:
            try:
                at = datetime.fromisoformat(entry["at"])
            except (KeyError, ValueError):
                at = today - timedelta(days=max_age_days + 1)
            if today - at > timedelta(days=max_age_days):
                changed.append(f"(older than {max_age_days} day(s); its data source changes over time)")
        return Status(check_id, "stale" if changed else "fresh", entry, changed)
