"""Scan checker: run any SARIF-emitting analyzer and gate on what a change introduces."""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
from datetime import date
from pathlib import Path

from rung.checks import findings
from rung.checks.base import Checker, Context, Outcome
from rung.checks.presets import PRESETS, Preset
from rung.core import git
from rung.core.config import Check, ConfigError
from rung.core.errors import RungError, ToolMissing
from rung.core.proc import run as run_process
from rung.engines import sarif

MAX_LISTED = 10


class ScanChecker(Checker):
    kind = "scan"
    summary = "Run a SARIF analyzer (secrets, dependencies, code patterns) and gate on new findings"

    def _preset(self, check: Check) -> Preset | None:
        name = check.get("tool")
        if name is None:
            return None
        if name not in PRESETS:
            raise ConfigError(f"check {check.id!r}: unknown scanner {name!r}; choose from {', '.join(PRESETS)}")
        return PRESETS[name]

    def _command(self, check: Check) -> str:
        if run := check.get("run"):
            return str(run)
        preset = self._preset(check)
        if preset is None:
            raise ConfigError(f"check {check.id!r} (scan) needs `tool` (a preset) or `run`")
        return preset.run

    def fail_on(self, check: Check) -> str:
        preset = self._preset(check)
        level = str(check.get("fail_on", preset.fail_on if preset else "warning"))
        if level not in sarif.LEVELS:
            raise ConfigError(f"check {check.id!r}: fail_on must be one of {', '.join(sarif.LEVELS)}")
        return level

    def specs(self, check: Check) -> list[str]:
        """The policy a verdict depends on: this check's baseline and the waivers file."""
        return [*check.get_list("specs"), findings.baseline_path_rel(check.id), findings.WAIVERS_FILE.as_posix()]

    def default_covers(self, check: Check) -> list[str]:
        preset = self._preset(check)
        return list(preset.covers) if preset else ["**/*"]

    def collect(self, cwd: Path, check: Check, timeout: int) -> list[sarif.Finding]:
        """Run the analyzer in `cwd` and parse its SARIF."""
        command = self._command(check)
        executable = shlex.split(command)[0]
        if not shutil.which(executable):
            preset = self._preset(check)
            hint = f" Install: {preset.install}" if preset else ""
            raise ToolMissing(f"`{executable}` is not installed.{hint}")
        with tempfile.TemporaryDirectory(prefix="rung-scan-") as tmp:
            out = Path(tmp) / "result.sarif"
            argv = [part.replace("{out}", str(out)) for part in shlex.split(command)]
            result = run_process(argv, cwd=cwd / str(check.get("cwd", ".")), timeout=timeout)
            raw = out.read_text() if "{out}" in command and out.exists() else result.stdout
        detail = (result.stderr or result.stdout).strip()[-800:]
        preset = self._preset(check)
        default = str(check.get("default_level", preset.default_level if preset else "warning"))
        try:
            found = sarif.parse(raw, cwd, default_level=default)
        except (json.JSONDecodeError, sarif.SarifError, AttributeError, TypeError, KeyError) as exc:
            raise RungError(
                f"`{command}` exited {result.returncode} without a valid SARIF result ({exc}): {detail}"
            ) from exc
        # Scanners exit non-zero when they find something. Non-zero with nothing found means it failed.
        if result.returncode != 0 and not found:
            raise RungError(
                f"`{command}` exited {result.returncode} and reported no findings; it likely failed: {detail}"
            )
        return found

    def run(self, ctx: Context, check: Check) -> Outcome:
        timeout = check.get_int("timeout", 900)
        head = self.collect(ctx.root, check, timeout)

        if ctx.base:
            with git.worktree(ctx.root, ctx.base) as tree:
                base = self.collect(tree, check, timeout)
            compared = f"merge base {ctx.base[:10]}"
        else:
            base = findings.load_baseline(ctx.root, check.id)
            compared = "the recorded baseline" if base is not None else ""

        t = findings.triage(
            check.id,
            head,
            base,
            findings.load_waivers(ctx.root),
            fail_on=self.fail_on(check),
            today=date.today(),
            compared_with=compared,
        )
        lines = [
            f"{len(head)} finding(s); compared with {t.compared_with}: "
            f"{len(t.blocking)} new blocking, {len(t.nonblocking)} new below `{self.fail_on(check)}`, "
            f"{len(t.waived)} waived, {len(t.resolved)} resolved"
        ]
        for f in t.blocking[:MAX_LISTED]:
            lines.append(f"NEW {f.level:7} {f.rule}  {f.where}  [{f.fingerprint}]")
            if f.message:
                lines.append(f"    {f.message[:160]}")
        if len(t.blocking) > MAX_LISTED:
            lines.append(f"... and {len(t.blocking) - MAX_LISTED} more (`rung report` or `--json` lists all)")
        if not compared and t.blocking:
            lines.append(
                f"Existing code? `rung baseline {check.id}` accepts today's findings, or "
                "`rung check --changed` compares with the merge base. Either way only new findings block."
            )
        for w in t.expired:
            lines.append(f"EXPIRED waiver {w.fingerprint} ({w.reason}), expired {w.expires}")

        records = [f.to_dict() | {"status": "new"} for f in t.blocking]
        records += [f.to_dict() | {"status": "new-below-threshold"} for f in t.nonblocking]
        records += [f.to_dict() | {"status": "waived"} for f in t.waived]
        if t.blocking:
            return Outcome("fail", f"{len(t.blocking)} new finding(s) at or above `{self.fail_on(check)}`",
                           lines, findings=records)  # fmt: skip
        return Outcome("pass", "no new blocking findings", lines, findings=records, valid_until=t.valid_until)
