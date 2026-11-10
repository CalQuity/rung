"""Immutable diagnostic runs, separate from configured-check verdicts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rung.core import git, ledger
from rung.core.config import Check, Config, ConfigError, managed_path, write_atomic
from rung.core.redact import redact

ROOT = Path(".rung/evidence/diagnostics")
KINDS = {"coverage", "flaky"}
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _definition(check: Check) -> str:
    return ledger.definition_hash(check.kind, check.covers, check.options)


def _inputs(root: Path) -> str:
    files = ledger.snapshot(root, ["**/*"])
    # Normal verdicts track check definitions separately and exclude these files.
    # Explicit diagnostics also bind their on-disk bytes during execution.
    for name in ("rung.toml", "LAWS.md"):
        files[name] = ledger.file_digest(root / name)
    return ledger.inputs_digest(files)


def _comparison_current(root: Path, parameters: dict[str, Any]) -> bool:
    if not parameters.get("base_ref"):
        return True
    try:
        return git.merge_base(root, parameters["base_ref"]) == parameters.get("base")
    except git.GitError:
        return False


def _safe(root: Path, relative: str | Path) -> Path:
    path = managed_path(root, relative)
    if path.resolve() != path.absolute():
        raise ConfigError("diagnostic evidence paths must not contain symbolic links")
    return path


class Session:
    """Create one evidence directory; finish only after inspecting analyzer output."""

    def __init__(self, config: Config, check: Check, kind: str, parameters: dict[str, Any]):
        if kind not in KINDS:
            raise ConfigError(f"unsupported diagnostic kind: {kind}")
        self.config, self.check, self.kind = config, check, kind
        self.parameters = parameters
        self.created_at = datetime.now(UTC).isoformat()
        self.head = git.head(config.root)
        self.inputs = _inputs(config.root)
        self.definition = _definition(check)
        name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
        self.directory = _safe(config.root, ROOT / kind / name)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.finished = False

    def path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name in {"", ".", "manifest.json"}:
            raise ConfigError("diagnostic artifact must be a relative path inside its run")
        return _safe(self.config.root, self.directory.relative_to(self.config.root) / relative)

    def finish(self, result: dict[str, Any]) -> dict[str, Any]:
        if self.finished:
            raise ConfigError("a diagnostic session can only be finished once")
        self.finished = True
        artifacts = {}
        binary_artifacts = []
        for file in sorted(self.directory.rglob("*")):
            _safe(self.config.root, file.relative_to(self.config.root))
            if file.is_file():
                # Reports and logs are text; sanitise before binding their final bytes.
                raw = file.read_bytes()
                try:
                    text = raw.decode("utf-8")
                    if "\x00" in text:
                        raise UnicodeError("binary content")
                except UnicodeError:
                    relative = file.relative_to(self.config.root).as_posix()
                    artifacts[relative] = ledger.file_digest(file)
                    binary_artifacts.append(relative)
                    continue
                if file.suffix == ".json":
                    try:
                        text = json.dumps(_clean(json.loads(text)), ensure_ascii=True, indent=2) + "\n"
                    except ValueError:
                        text = redact(text)
                elif file.suffix == ".jsonl":
                    cleaned = []
                    for line in text.splitlines():
                        try:
                            cleaned.append(json.dumps(_clean(json.loads(line)), ensure_ascii=True))
                        except ValueError:
                            cleaned.append(redact(line))
                    text = "\n".join(cleaned) + "\n"
                else:
                    text = redact(text)
                write_atomic(file, text)
                artifacts[file.relative_to(self.config.root).as_posix()] = ledger.file_digest(file)
        current = self.inputs == _inputs(self.config.root)
        current = current and self.head == git.head(self.config.root) and self.definition == _definition(self.check)
        current = current and _comparison_current(self.config.root, self.parameters)
        data = _clean(result)
        if not current:
            data["state"], data["exit_code"] = "incomplete", 2
            data.setdefault("errors", []).append("Project inputs changed during the diagnostic; rerun it.")
            data["summary"] = "Incomplete evidence: project inputs changed during execution."
        manifest = self.directory / "manifest.json"
        data.update(
            schema_version=1,
            kind=self.kind,
            check_id=self.check.id,
            created_at=self.created_at,
            head=self.head,
            parameters=_clean(self.parameters),
            inputs_digest=self.inputs,
            definition_hash=self.definition,
            artifacts=artifacts,
            unredacted_binary_artifacts=binary_artifacts,
            manifest_path=manifest.relative_to(self.config.root).as_posix(),
            current=current,
        )
        data["content_digest"] = _digest(data)
        write_atomic(
            _safe(self.config.root, data["manifest_path"]), json.dumps(data, indent=2, ensure_ascii=True) + "\n"
        )
        return data


def saved(config: Config) -> list[dict[str, Any]]:
    """Newest result for each kind/check, including unusable and historical results."""
    root = _safe(config.root, ROOT)
    if not root.exists():
        return []
    inputs = _inputs(config.root)
    head = git.head(config.root)
    definitions = {check.id: _definition(check) for check in config.checks}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(root.glob("*/*/manifest.json"), reverse=True):
        try:
            _safe(config.root, path.relative_to(config.root))
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("manifest exceeds supported size")
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("manifest is not an object")
            digest = data.pop("content_digest", None)
            if digest != _digest(data) or data.get("schema_version") != 1:
                raise ValueError("manifest integrity check failed")
            if data.get("kind") not in KINDS or not isinstance(data.get("check_id"), str):
                raise ValueError("invalid diagnostic identity")
            if data["kind"] != path.parent.parent.name or not isinstance(data.get("artifacts"), dict):
                raise ValueError("invalid diagnostic location or artifacts")
            if not isinstance(data.get("parameters"), dict) or not isinstance(data.get("current"), bool):
                raise ValueError("invalid diagnostic metadata")
            reasons = []
            if not data["current"] or data.get("inputs_digest") != inputs or data.get("head") != head:
                reasons.append("Project inputs or Git revision changed, or execution changed its inputs.")
            if definitions.get(data["check_id"]) != data.get("definition_hash"):
                reasons.append("The selected check was removed or reconfigured.")
            if data["kind"] == "coverage" and data["parameters"].get("base_ref"):
                try:
                    compared = git.merge_base(config.root, data["parameters"]["base_ref"])
                    if compared != data["parameters"].get("base"):
                        reasons.append("The comparison target's merge base changed.")
                except git.GitError:
                    reasons.append("The comparison target is no longer available.")
            for name, expected in data["artifacts"].items():
                artifact = _safe(config.root, name)
                if not artifact.is_relative_to(path.parent) or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
                    raise ValueError("invalid artifact declaration")
                if ledger.file_digest(artifact) != expected:
                    reasons.append("A recorded artifact is missing or modified.")
                    break
            data.update(current=not reasons, stale_reasons=reasons, content_digest=digest)
        except (OSError, ValueError, TypeError, KeyError, ConfigError, OverflowError, RecursionError) as exc:
            data = {
                "kind": path.parent.parent.name,
                "check_id": "unknown",
                "state": "error",
                "current": False,
                "summary": "Unusable diagnostic manifest; rerun the diagnostic.",
                "errors": [redact(str(exc))],
                "manifest_path": path.relative_to(config.root).as_posix(),
                "metrics": {},
                "stale_reasons": ["The saved evidence cannot be verified."],
            }
        key = data["kind"], data["check_id"]
        if key not in latest or data.get("created_at", "") > latest[key].get("created_at", ""):
            latest[key] = data
    return list(latest.values())
