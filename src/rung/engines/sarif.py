"""SARIF: the one format every serious static analyzer can emit.

Gitleaks, osv-scanner, zizmor, Semgrep, Opengrep, Trivy, CodeQL and many linters
write SARIF 2.1.0. Parsing it once lets Rung treat all of them the same way:
normalize locations to repository-relative paths, fingerprint each finding so it
survives unrelated edits, and compare two runs to see what a change introduced.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

LEVELS = ("none", "note", "warning", "error")


@dataclass(frozen=True)
class Finding:
    tool: str
    rule: str
    level: str
    path: str
    line: int
    message: str
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


def at_least(level: str, threshold: str) -> bool:
    return LEVELS.index(level) >= LEVELS.index(threshold)


def _relative(uri: str, root: Path) -> str:
    """`file:///abs/repo/a.py`, `a.py` or `./a.py` -> `a.py`, relative to `root` when inside it."""
    if uri.startswith("file:"):
        uri = unquote(urlparse(uri).path)
    if os.path.isabs(uri):
        real_root = os.path.realpath(root)
        real = os.path.realpath(uri)
        if real == real_root or real.startswith(real_root + os.sep):
            return Path(os.path.relpath(real, real_root)).as_posix()
        return uri
    return Path(uri).as_posix().removeprefix("./")


def _rule_levels(run: dict[str, Any]) -> dict[str, str]:
    rules = run.get("tool", {}).get("driver", {}).get("rules", []) or []
    return {
        r["id"]: r.get("defaultConfiguration", {}).get("level", "") for r in rules if isinstance(r, dict) and "id" in r
    }


_VERSIONED = re.compile(r"@[^\s'\"]+")
_VERSION = re.compile(r"\b\d+(?:\.\d+)+\b")


def _normalize_message(text: str) -> str:
    """`Package 'jinja2@2.10.0' is vulnerable` -> `Package 'jinja2' is vulnerable`."""
    return " ".join(_VERSION.sub("N", _VERSIONED.sub("", text)).split())


def _stable_token(result: dict[str, Any], location: dict[str, Any]) -> str:
    """What identifies a finding apart from its line number, which shifts with every edit.

    Order matters. The flagged code itself is the best identity. Next is the
    message with versions removed, so a vulnerability reported against
    `pkg@1.2.0` and later `pkg@1.2` is the same finding. Tool line hashes come
    last: several tools hash the whole file, so any edit to it changes them.
    """
    snippet = location.get("region", {}).get("snippet", {}).get("text")
    if snippet:
        return " ".join(snippet.split())
    message = _normalize_message(result.get("message", {}).get("text") or "")
    if message:
        return message
    partial = result.get("partialFingerprints") or {}
    return str(partial.get("primaryLocationLineHash", ""))


def parse(sarif: str | dict[str, Any], root: Path, *, default_level: str = "warning") -> list[Finding]:
    """Turn a SARIF document into findings with repository-relative paths."""
    doc = json.loads(sarif) if isinstance(sarif, str) else sarif
    findings: list[Finding] = []
    for run in doc.get("runs", []) or []:
        tool = run.get("tool", {}).get("driver", {}).get("name", "unknown")
        rule_levels = _rule_levels(run)
        for result in run.get("results", []) or []:
            rule = result.get("ruleId") or result.get("rule", {}).get("id") or "unknown"
            level = result.get("level") or rule_levels.get(rule) or default_level
            if level not in LEVELS:
                level = default_level
            locations = result.get("locations") or [{}]
            physical = locations[0].get("physicalLocation", {})
            path = _relative(physical.get("artifactLocation", {}).get("uri", ""), root)
            line = int(physical.get("region", {}).get("startLine") or 0)
            message = " ".join((result.get("message", {}).get("text") or "").split())
            token = _stable_token(result, physical)
            digest = hashlib.sha256(f"{tool}\0{rule}\0{path}\0{token}".encode()).hexdigest()[:16]
            findings.append(Finding(tool, rule, level, path, line, message, digest))
    return findings


def diff(base: list[Finding], head: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Return (introduced, resolved) between two runs.

    Identical fingerprints are counted, so a second copy of the same leaked
    secret in the same file is still reported as new.
    """
    base_counts = Counter(f.fingerprint for f in base)
    head_counts = Counter(f.fingerprint for f in head)
    introduced, resolved = [], []
    seen: Counter[str] = Counter()
    for f in head:
        seen[f.fingerprint] += 1
        if seen[f.fingerprint] > base_counts[f.fingerprint]:
            introduced.append(f)
    seen.clear()
    for f in base:
        seen[f.fingerprint] += 1
        if seen[f.fingerprint] > head_counts[f.fingerprint]:
            resolved.append(f)
    return introduced, resolved
