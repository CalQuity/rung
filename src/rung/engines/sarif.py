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
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from rung.core.redact import redact

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


def _relative(uri: str, root: Path, workdir: Path) -> str:
    """`file:///abs/repo/a.py`, `a.py` or `./a.py` -> `a.py`, relative to `root` when inside it."""
    if uri.startswith("file:"):
        uri = unquote(urlparse(uri).path)
    if os.path.isabs(uri):
        real_root = os.path.realpath(root)
        real = os.path.realpath(uri)
        if real == real_root or real.startswith(real_root + os.sep):
            return Path(os.path.relpath(real, real_root)).as_posix()
        return uri
    return (workdir.relative_to(root) / uri).as_posix().removeprefix("./") if uri else ""


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


REDACTED_SNIPPETS = {"REDACTED", "[REDACTED]", "***", "<redacted>"}


def _flagged_text_digest(root: Path, path: str, region: dict[str, Any], *, whole_line: bool = False) -> str:
    """A hash of the exact characters a finding points at, read from the file; the text is never kept.

    Secret scanners redact their snippets, so without this every secret of one rule in one file
    would share a fingerprint: replacing an accepted test token with a live one would look unchanged.
    """
    start, end = region.get("startLine"), region.get("endLine", region.get("startLine"))
    if not isinstance(start, int) or start < 1 or not isinstance(end, int) or end < start or not path:
        return ""
    try:
        source = (root / path).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            return ""
        with source.open(errors="replace") as content:
            lines = [line.rstrip("\n") for line in islice(content, start - 1, end)]
    except (OSError, ValueError, RuntimeError):
        return ""
    if not lines:
        return ""
    if not whole_line and region.get("endColumn"):
        lines[-1] = lines[-1][: region["endColumn"] - 1]
    if not whole_line and region.get("startColumn"):
        lines[0] = lines[0][region["startColumn"] - 1 :]
    return "flagged:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _stable_token(result: dict[str, Any], location: dict[str, Any], root: Path, path: str, tool: str) -> str:
    """What identifies a finding apart from its line number, which shifts with every edit.

    Order matters. The flagged code itself is the best identity. Next is the
    message with versions removed, so a vulnerability reported against
    `pkg@1.2.0` and later `pkg@1.2` is the same finding. Tool line hashes come
    last: several tools hash the whole file, so any edit to it changes them.
    """
    region = location.get("region", {})
    snippet = region.get("snippet", {}).get("text")
    if snippet and snippet.strip() not in REDACTED_SNIPPETS:
        return " ".join(snippet.split())
    # Gitleaks SARIF columns can move with unrelated lines. Keep hashing the exact
    # source bytes containing the secret without trusting those column offsets.
    if snippet and (flagged := _flagged_text_digest(root, path, region, whole_line=tool.lower() == "gitleaks")):
        return flagged
    message = _normalize_message(result.get("message", {}).get("text") or "")
    if message:
        return message
    partial = result.get("partialFingerprints") or {}
    return str(partial.get("primaryLocationLineHash", ""))


class SarifError(ValueError):
    """The document is not SARIF, or the tool says its analysis did not complete."""


def validate(doc: Any) -> None:
    """Reject output that is not a completed SARIF analysis.

    An empty object, a document without `runs`, or a run whose invocation reports
    `executionSuccessful: false` is a failed analysis, not a clean one.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("runs"), list) or not doc["runs"]:
        raise SarifError("no `runs` in the document")
    for run in doc["runs"]:
        if not isinstance(run, dict) or not run.get("tool", {}).get("driver", {}).get("name"):
            raise SarifError("a run has no `tool.driver.name`")
        for invocation in run.get("invocations") or []:
            if invocation.get("executionSuccessful") is False:
                reasons = [
                    n.get("message", {}).get("text", "") for n in invocation.get("toolExecutionNotifications") or []
                ]
                raise SarifError("the tool reports its analysis did not complete: " + "; ".join(filter(None, reasons)))


def _rule_id(result: dict[str, Any], rules: list[Any]) -> str:
    if result.get("ruleId"):
        return str(result["ruleId"])
    rule = result.get("rule") or {}
    if rule.get("id"):
        return str(rule["id"])
    index = result.get("ruleIndex", rule.get("index"))
    if isinstance(index, int) and 0 <= index < len(rules) and isinstance(rules[index], dict):
        return str(rules[index].get("id", "unknown"))
    return "unknown"


def _uri(location: dict[str, Any], run: dict[str, Any]) -> str:
    """A result's file URI, following `index` into `run.artifacts` and resolving `uriBaseId`."""
    artifact = location.get("artifactLocation", {})
    uri = artifact.get("uri", "")
    index = artifact.get("index")
    artifacts = run.get("artifacts") or []
    if not uri and isinstance(index, int) and 0 <= index < len(artifacts):
        artifact = {**artifacts[index].get("location", {}), **{k: v for k, v in artifact.items() if k != "index"}}
        uri = artifact.get("uri", "")
    base_id = artifact.get("uriBaseId")
    bases = run.get("originalUriBaseIds") or {}
    if base_id and base_id in bases and not uri.startswith(("file:", "/")):
        uri = bases[base_id].get("uri", "").rstrip("/") + "/" + uri
    return uri


def parse(
    sarif: str | dict[str, Any], root: Path, *, default_level: str = "warning", workdir: Path | None = None,
) -> list[Finding]:  # fmt: skip
    """Turn a SARIF document into findings with repository-relative paths."""
    doc = json.loads(sarif) if isinstance(sarif, str) else sarif
    validate(doc)
    findings: list[Finding] = []
    for run in doc["runs"]:
        driver = run["tool"]["driver"]
        tool = driver["name"]
        rules = driver.get("rules") or []
        rule_levels = _rule_levels(run)
        for result in run.get("results") or []:
            rule = _rule_id(result, rules)
            level = result.get("level") or rule_levels.get(rule) or default_level
            if level not in LEVELS:
                level = default_level
            locations = result.get("locations") or [{}]
            physical = locations[0].get("physicalLocation", {})
            path = _relative(_uri(physical, run), root, workdir or root)
            line = int(physical.get("region", {}).get("startLine") or 0)
            message = redact(" ".join((result.get("message", {}).get("text") or "").split()))
            token = _stable_token(result, physical, root, path, tool)
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
