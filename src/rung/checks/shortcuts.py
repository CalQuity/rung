"""Shortcuts checker: block the type-checker silencers, skipped tests and relaxed configs a change adds.

It reuses the scan machinery (fingerprints, baselines, waivers), so it gates the same way: only what
a change introduces blocks. Without `--base` it compares with the recorded baseline if there is one,
otherwise with the merge base of the default branch, so existing shortcuts never block day one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rung.checks import findings
from rung.checks.base import Context, Outcome
from rung.checks.scan import MAX_LISTED, ScanChecker
from rung.core import git
from rung.core.config import Check
from rung.engines import sarif
from rung.engines import shortcuts as engine


def _working_files(root: Path) -> list[tuple[str, str]]:
    def read(rel: str) -> str | None:
        try:
            return (root / rel).read_text(errors="replace")
        except OSError:
            return None

    return engine.read_all((f for f in git.list_files(root) if not git.is_rung_output(f)), read)


def _files_at(root: Path, commit: str) -> list[tuple[str, str]]:
    paths = [p for p in git.files_at(root, commit) if engine.scannable(p)]
    blobs = git.show_many(root, commit, paths)

    def read(rel: str) -> str | None:
        try:
            return blobs[rel].decode() if rel in blobs else None
        except UnicodeDecodeError:  # not text
            return None

    return engine.read_all(paths, read)


class ShortcutsChecker(ScanChecker):
    kind = "shortcuts"
    summary = "Block new type-checker silencers, skipped or deleted tests, and relaxed checker configs"

    def _preset(self, check: Check) -> None:
        return None

    def max_age_days(self, check: Check) -> int | None:
        return check.get_int("max_age_days", 0) or None

    def default_covers(self, check: Check) -> list[str]:
        return ["**/*"]

    def collect(self, cwd: Path, check: Check, timeout: int) -> list[sarif.Finding]:
        return engine.find(_working_files(cwd), check.get_list("allow"))

    def _base(self, ctx: Context, check: Check) -> tuple[str | None, list[sarif.Finding] | None, str]:
        """The commit to compare with (if any), the findings there, and how to describe it."""
        if ctx.base:
            return ctx.base, None, f"merge base {ctx.base[:10]}"
        if (recorded := findings.load_baseline(ctx.root, check.id)) is not None:
            return None, recorded, "the recorded baseline"
        try:
            commit = git.merge_base(ctx.root, git.default_base(ctx.root))
        except git.GitError:
            return None, None, ""
        return commit, None, f"merge base {commit[:10]} with the default branch"

    def run(self, ctx: Context, check: Check) -> Outcome:
        allow = check.get_list("allow")
        head_files = _working_files(ctx.root)
        head = engine.find(head_files, allow)
        commit, base, compared = self._base(ctx, check)
        if commit:
            base_files = _files_at(ctx.root, commit)
            base = engine.find(base_files, allow)
            head += engine.compare(head_files, base_files, allow)
        t = findings.triage(check.id, head, base, findings.load_waivers(ctx.root),
                            fail_on=self.fail_on(check), today=date.today(), compared_with=compared)  # fmt: skip
        lines = [
            f"{len(head)} shortcut(s) in the code; compared with {t.compared_with}: {len(t.blocking)} new blocking, "
            f"{len(t.waived)} waived, {len(t.resolved)} removed"
        ]
        for f in t.blocking[:MAX_LISTED]:
            lines.append(f"NEW {f.rule:17} {f.where or '(project)'}  [{f.fingerprint}]")
            lines.append(f"    {f.message[:160]}")
        if len(t.blocking) > MAX_LISTED:
            lines.append(f"... and {len(t.blocking) - MAX_LISTED} more (`rung report` or `--json` lists all)")
        if t.blocking:
            lines.append(
                "Fix the code instead. If one is genuinely needed, a person can accept it: "
                f"`rung waive {check.id} <fingerprint> --reason ...`."
            )
        for w in t.expired:
            lines.append(f"EXPIRED waiver {w.fingerprint} ({w.reason}), expired {w.expires}")
        records = [f.to_dict() | {"status": "new"} for f in t.blocking]
        records += [f.to_dict() | {"status": "waived"} for f in t.waived]
        if t.blocking:
            return Outcome("fail", f"{len(t.blocking)} new shortcut(s)", lines, findings=records)
        return Outcome("pass", "no new shortcuts", lines, findings=records, valid_until=t.valid_until)
