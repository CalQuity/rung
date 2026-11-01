"""Command checkers: any test suite, linter or script, and evidence-producing drive scripts."""

from __future__ import annotations

import os
import shlex
import subprocess
import uuid
from datetime import UTC, datetime

from rung.checks.base import Checker, Context, Outcome
from rung.core import ledger, proc
from rung.core.config import Check
from rung.core.redact import redact

# A command check that declares no coverage covers the whole project: any change stales it.
# Narrow `covers` to make it cheaper to keep fresh, never to make it easier to trust.
WHOLE_PROJECT = ["**/*"]


def tail(result: subprocess.CompletedProcess[str], lines: int = 15) -> list[str]:
    """The end of the output, where test runners and scripts print their verdict.

    A passing run shows the end of stdout (stderr only if stdout is empty), because tools like
    `uv run` write build chatter to stderr. A failing run shows the end of both: a script that
    prints progress to stdout usually reports the actual failure on stderr.
    """
    out, err = result.stdout.strip().splitlines(), result.stderr.strip().splitlines()
    if result.returncode == 0 or not err:
        return (out or err)[-lines:]
    if not out:
        return err[-lines:]
    return [*out[-(lines - 6) :], "stderr:", *err[-5:]]


def _run(ctx: Context, check: Check, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return proc.run(
        shlex.split(check.require("run")),
        cwd=ctx.root / check.get("cwd", "."),
        env={**os.environ, **(env or {})},
        timeout=check.get_int("timeout", 1800),
    )


class CommandChecker(Checker):
    kind = "command"
    summary = "Run a command; exit status 0 passes"

    def default_covers(self, check: Check) -> list[str]:
        return list(WHOLE_PROJECT)

    def run(self, ctx: Context, check: Check) -> Outcome:
        result = _run(ctx, check)
        verdict = "pass" if result.returncode == 0 else "fail"
        return Outcome(verdict, f"`{check.require('run')}` exited {result.returncode}", tail(result))


class EvidenceChecker(Checker):
    """Run a script that drives the real app and must leave artifacts behind.

    The script gets `RUNG_EVIDENCE_DIR`, a directory unique to this run; anything it writes there
    (a video, a terminal transcript, screenshots, response bodies) is hashed into the ledger. A
    script that exits 0 but records nothing fails: "it works" without evidence is exactly what
    this rung exists to reject. `require` can demand particular artifact types, e.g. `[".webm"]`.
    The drive script itself belongs in `specs`, so editing it stales the verdict.
    """

    kind = "evidence"
    summary = "Drive the real app and record evidence artifacts"

    def default_covers(self, check: Check) -> list[str]:
        return list(WHOLE_PROJECT)

    def run(self, ctx: Context, check: Check) -> Outcome:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = ctx.root / ".rung" / "evidence" / check.id / f"{stamp}-{uuid.uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=False)
        result = _run(ctx, check, {"RUNG_EVIDENCE_DIR": str(out_dir)})
        (out_dir / "run.log").write_text(redact(result.stdout + result.stderr))
        artifacts = {
            p.relative_to(ctx.root).as_posix(): ledger.file_digest(p)
            for p in sorted(out_dir.rglob("*"))
            if p.is_file() and p.name != "run.log"
        }
        lines = [f"evidence: {path}" for path in artifacts]
        command = check.require("run")
        if result.returncode != 0:
            return Outcome("fail", f"`{command}` exited {result.returncode}", lines + tail(result, 10), artifacts)
        if not artifacts:
            return Outcome("fail", "the script exited 0 but recorded no evidence", lines)
        required = [str(r).lower() for r in check.get_list("require")]
        missing = [r for r in required if not any(p.lower().endswith(r) for p in artifacts)]
        if missing:
            return Outcome("fail", f"required evidence missing: {', '.join(missing)}", lines, artifacts)
        return Outcome("pass", f"{len(artifacts)} artifact(s) recorded", lines, artifacts)
