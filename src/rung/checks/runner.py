"""Select checks, run them through their checkers, and record verdicts in the ledger."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rung.checks import registry
from rung.checks.base import Context, Outcome
from rung.checks.command import evidence_dir, is_attested, isolates_evidence, missing_artifacts
from rung.checks.presets import PRESETS
from rung.core import git, launch, ledger, modelcard
from rung.core.config import Check, Config, ConfigError
from rung.core.errors import RungError
from rung.core.redact import redact, redact_lines
from rung.engines import lean, tla

MAX_RECORDED_FINDINGS = 200


@dataclass
class Result:
    check: Check
    outcome: Outcome

    @property
    def verdict(self) -> str:
        return self.outcome.verdict


def covers(check: Check) -> list[str]:
    return registry.get(check.kind).covers(check)


def specs(check: Check, root: Path | None = None) -> list[str]:
    patterns = [*registry.get(check.kind).specs(check), "LAWS.md"]
    if check.kind in modelcard.KINDS:
        patterns.append((modelcard.FORMAL_DIR / f"{check.id}.json").as_posix())
    if root is not None and check.kind == "drt":
        for key in ("model", "impl"):
            cwd = root / check.get(f"{key}_cwd", ".")
            for path in launch.file_inputs(shlex.split(check.require(key)), cwd):
                if path.is_relative_to(root):
                    patterns.append(path.relative_to(root).as_posix())
    if root is not None and check.kind in ("lean", "drt"):
        projects = (
            [root / check.require("project")]
            if check.kind == "lean"
            else [root / check.get(f"{side}_cwd", ".") for side in ("model", "impl")]
        )
        for project in projects:
            packages = project / ".lake" / "packages"
            if not packages.exists():
                continue
            patterns.extend(
                p.relative_to(root).as_posix()
                for p in packages.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(packages).parts
            )
    return patterns


def definition(check: Check, root: Path | None = None) -> str:
    options = {**check.options, "isolate": True} if isolates_evidence(check) else check.options
    if check.kind in (*modelcard.KINDS, "drt"):
        options = {**options, "formal_protocol": 2}
    if root is not None and check.kind in (*modelcard.KINDS, "drt"):
        tools = tool_identity(root, check)
        options = {**options, "checker_files": {p: ledger.file_digest(Path(p)) for p in tools or {}}}
        if check.kind == "tla":
            options["java_options"] = {
                k: os.environ.get(k, "") for k in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS")
            }
    return ledger.definition_hash(check.kind, check.covers, options)


def tool_identity(root: Path, check: Check) -> dict[str, tuple[int, ...]] | None:
    """Identify the static executable chain. Skip reuse when a runner resolves its own environment."""
    paths: list[Path] = []
    for key in ("run", "model", "impl"):
        command = check.get(key)
        if not command and key == "run" and check.kind == "scan" and (preset := PRESETS.get(check.get("tool", ""))):
            command = preset.run
        if command:
            argv = shlex.split(command)
            cwd = root / check.get("cwd" if key == "run" else f"{key}_cwd", ".")
            paths.extend(launch.file_inputs(argv, cwd))
            while argv:
                found = launch.find_executable(argv[0], cwd)
                if not found:
                    return None
                paths.append(Path(found))
                inner = launch.delegated(argv, cwd)
                if not inner:
                    break
                if not inner.host_path:
                    return None
                argv, cwd = inner.argv, inner.cwd
    if check.kind == "tla":
        try:
            paths += [Path(tla.find_java()), tla.find_jar()]
        except RungError:
            return None
    if check.kind == "lean":
        paths.append(Path(lean.__file__).with_name("lean_audit.lean"))
        paths.append(Path(shutil.which("lake") or Path.home() / ".elan/bin/lake"))
        elan = shutil.which("elan") or str(Path.home() / ".elan/bin/elan")
        for tool in ("lean", "lake"):
            try:
                resolved = subprocess.run(
                    [elan, "which", tool],
                    cwd=root / check.require("project"),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if resolved.returncode:
                return None
            paths.append(Path(resolved.stdout.strip()))
    result = {}
    for path in {candidate for name in paths for candidate in launch.linked_paths(name)}:
        try:
            stat = path.lstat()
            result[str(path)] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_mode)
        except OSError:
            result[str(path)] = ()
    return result


def touched_by(check: Check, changed: set[str]) -> bool:
    """Does a change to `changed` files affect this check? Checks covering nothing always run."""
    patterns = covers(check) + specs(check)
    if not patterns:
        return True
    return any(git.glob_match(p, f) for p in patterns for f in changed)


def select(config: Config, ids: list[str], changed: set[str] | None) -> list[Check]:
    """Checks to run: all (or the named ones), narrowed to those a change touches.

    A check that is not currently fresh is always selected, so editing a check's own declaration,
    a baseline or a waiver, or breaking the ledger, can never be skipped by `--changed`.
    """
    chosen = [config.check(i) for i in ids] if ids else list(config.checks)
    if changed is None:
        return chosen
    view = ledger.View(config.root)
    return [c for c in chosen if touched_by(c, changed) or not status(config, c, view=view).trusted]


def run_check(
    config: Config, check: Check, *, base: str | None = None, seed: int | None = None, scenarios: tuple[str, ...] = (),
    merge_gate: bool = False,
) -> Result:  # fmt: skip
    """Run one check and record its verdict.

    With `seed` or `scenarios`, a scripted evidence check runs that one seed or those scenarios only, to
    reproduce a failure. Such a run does not stand for the check as declared, so it is not recorded.
    """
    root = config.root
    partial = seed is not None or bool(scenarios)
    if partial and (check.kind != "evidence" or is_attested(check)):
        raise ConfigError(f"--seed and --scenario apply to evidence checks with `run`, not {check.id!r} ({check.kind})")
    if is_attested(check) and (held := status(config, check)).trusted and held.entry:
        # Attested evidence cannot be re-run; while its files are unchanged, the attestation stands.
        return Result(check, Outcome("pass", held.entry.get("summary", "attested"), held.entry.get("evidence", [])))
    checker = registry.get(check.kind)
    cover_patterns, spec_patterns = checker.covers(check), specs(check, root)
    if check.kind == "lean":  # a first-use toolchain install must happen before the checker is identified
        lean.prepare_toolchain(root / check.require("project"))
    before = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    tools = tool_identity(root, check)
    checked_definition = definition(check, root)
    # A covered link that points outside the project is recorded by its target path, never followed:
    # pointing it somewhere else stales the verdict, but what it points at is not part of the evidence.
    try:
        context = Context(root=root, base=base, seed=seed, scenarios=scenarios, merge_gate=merge_gate)
        external = [p for snapshot in before for p, digest in snapshot.items() if digest.startswith("external-link:")]
        if check.kind in (*modelcard.KINDS, "drt") and external:
            outcome = Outcome("error", "external symlink inputs cannot be verified: " + ", ".join(external))
        else:
            outcome = checker.run(context, check)
    except (RungError, OSError, subprocess.TimeoutExpired) as exc:
        outcome = Outcome("error", str(exc))
    # Hash before and after: a file edited mid-run must not be recorded as verified.
    after = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, specs(check, root)))
    if definition(check, root) != checked_definition:
        outcome = Outcome("error", "checker identity or definition changed while the check ran", outcome.lines)
    if after != before:
        moved = sorted(p for b, a in zip(before, after, strict=True) for p in set(b) | set(a) if b.get(p) != a.get(p))
        hint = (
            "files it covers changed while it ran: " + ", ".join(moved[:5]) + (" ..." if len(moved) > 5 else "")
            + ". If the check writes them itself (coverage, reports), add them to .gitignore or narrow `covers`."
        )  # fmt: skip
        outcome = Outcome("error", hint, outcome.lines, details=outcome.details)
    if partial:
        return Result(check, outcome)
    entry = ledger.record(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict=outcome.verdict,
        covers=before[0],
        specs=before[1],
        summary=redact(outcome.summary),
        evidence=[f"{path} sha256:{digest}" for path, digest in outcome.evidence.items()],
        definition=checked_definition,
        context=base or "",
        valid_until=outcome.valid_until,
        details={
            "lines": redact_lines(outcome.lines[:60]),
            "base": base or "",
            "findings": outcome.findings[:MAX_RECORDED_FINDINGS],
            **outcome.details,
        },
    )
    if tools is not None and tools == tool_identity(root, check):
        ledger.record_environment(root, check.id, entry, tools)
    return Result(check, outcome)


def status(config: Config, check: Check, *, base: str | None = None, view: ledger.View | None = None) -> ledger.Status:
    """Whether `check`'s latest verdict still holds. With `base`, it must have been made against that base."""
    view = view or ledger.View(config.root)
    max_age = registry.get(check.kind).max_age_days(check)
    held = view.status(
        check.id, covers(check), specs(check, config.root), definition(check, config.root), base, max_age
    )
    if check.kind in modelcard.KINDS:
        card = modelcard.load(config.root, check.id)
        receipt_link = ((held.entry or {}).get("details") or {}).get("implementation_link", "model-only")
        held.implementation_link = (
            "source-confirmed"
            if modelcard.has_source_confirmation(card) and receipt_link == "source-confirmed"
            else "model-only"
        )
        if modelcard.path(config.root, check.id).exists() and card is None:
            return ledger.Status(
                check.id, "drifted", held.entry, ["model record is corrupt"], implementation_link="drifted"
            )
        if card and (drift := modelcard.drift(config.root, check.id, card)):
            return ledger.Status(check.id, "drifted", held.entry, [f"{drift.message}: {', '.join(drift.changed)}"],
                                 implementation_link="drifted")  # fmt: skip
    return held


def statuses(config: Config, *, base: str | None = None) -> list[ledger.Status]:
    view = ledger.View(config.root)
    return [status(config, c, base=base, view=view) for c in config.checks]


def attest(config: Config, check: Check, files: list[Path], *, by: str, note: str = "") -> Result:
    """Record evidence an agent or a person captured (a browser recording, screenshots) for an attested check.

    The files are copied into `.rung/evidence/` and hashed into the verdict, which binds them to the
    code as it is now. Nothing is re-run: the verdict says who recorded what, not that Rung saw it work.
    """
    if not is_attested(check):
        raise ConfigError(
            f'check {check.id!r} is not an attested evidence check (kind = "evidence" without `run`); '
            "scripted checks record their own evidence with `rung check`"
        )
    if not by.strip():
        raise ConfigError("say which tool or person recorded the evidence with --by")
    empty = [str(f) for f in files if not f.is_file() or f.stat().st_size == 0]
    if empty:
        raise ConfigError(f"not a non-empty file: {', '.join(empty)}")
    if missing := missing_artifacts(check, files):
        raise ConfigError(f"check {check.id!r} requires evidence ending in {', '.join(missing)}")
    root = config.root
    cover_patterns, spec_patterns = covers(check), specs(check)
    before = (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns))
    out_dir = evidence_dir(root, check.id)
    artifacts: dict[str, str] = {}
    for i, f in enumerate(files):
        target = out_dir / f"{i + 1:02d}-{f.name}"
        shutil.copyfile(f, target)
        artifacts[target.relative_to(root).as_posix()] = ledger.file_digest(target)
    if (ledger.snapshot(root, cover_patterns), ledger.snapshot(root, spec_patterns)) != before:
        raise RungError("files the check covers changed while the evidence was being recorded; attest again")
    summary = redact(f"attested by {by.strip()}: {len(artifacts)} artifact(s), not re-run by Rung")
    lines = [f"evidence: {path}" for path in artifacts] + ([redact(f"note: {note}")] if note else [])
    ledger.record(
        root,
        check_id=check.id,
        kind=check.kind,
        verdict="pass",
        covers=before[0],
        specs=before[1],
        summary=summary,
        evidence=[f"{path} sha256:{digest}" for path, digest in artifacts.items()],
        definition=definition(check, root),
        details={"lines": lines, "attested_by": by.strip(), "note": redact(note)},
    )
    return Result(check, Outcome("pass", summary, lines, artifacts))
