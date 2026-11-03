"""Command checkers: any test suite, linter or script, and evidence-producing drive scripts."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rung.checks.base import Checker, Context, Outcome
from rung.core import ledger, proc
from rung.core.config import Check
from rung.core.redact import redact
from rung.engines import evidence_page

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


def evidence_dir(root: Path, check_id: str) -> Path:
    """A new directory, unique to this run, under `.rung/evidence/<check>/`."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / ".rung" / "evidence" / check_id / f"{stamp}-{uuid.uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=False)
    return out_dir


def missing_required(check: Check, artifacts: Iterable[str]) -> list[str]:
    """Required artifact suffixes (`require`) that none of `artifacts` has."""
    names = [a.lower() for a in artifacts]
    return [r for r in (s.lower() for s in check.get_list("require")) if not any(n.endswith(r) for n in names)]


def is_attested(check: Check) -> bool:
    """An evidence check without `run`: an agent or a person records the evidence (`rung attest`)."""
    return check.kind == "evidence" and "run" not in check.options


def default_seed(check_id: str) -> int:
    """The base seed of a check that sets none: derived from its id, so every run of the check is the same."""
    return int(hashlib.sha256(check_id.encode()).hexdigest()[:8], 16) % 100_000


def seeds(check: Check, only: int | None = None) -> list[int]:
    """The seeds a check runs with: `seed`, `seed + 1`, ... for `runs` runs, or just `only`."""
    if only is not None:
        return [only]
    base = check.get_int("seed", 0) if "seed" in check.options else default_seed(check.id)
    return [base + i for i in range(check.get_int("runs", 1))]


def reproduce_command(check_id: str, seed: int) -> str:
    return f"rung check {check_id} --seed {seed}"


def read_result(run_dir: Path) -> dict[str, Any] | None:
    """The script's `result.json`, if it wrote one that parses as an object."""
    try:
        data = json.loads((run_dir / "result.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _items(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


# What a drive script's scenarios exercise. `happy` always applies; the script may mark any other
# category not applicable to the feature, with a reason (`not_applicable` in result.json).
CATEGORIES = ("happy", "input", "state", "auth", "fault", "robustness")


def scenarios(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Each scenario the script drove: name, category, the faults it injected, its assertions, and whether it passed.

    A script without `scenarios` (one written before scenarios existed) has one unnamed scenario
    holding its top-level `assertions`.
    """
    found = _items(result.get("scenarios"))
    if not found and "assertions" in result:
        found = [{"name": "", "category": "happy", "assertions": result.get("assertions")}]
    out = []
    for s in found:
        assertions = [
            {"name": str(a.get("name", a.get("assertion", ""))), "passed": a.get("passed") is True}
            for a in _items(s.get("assertions"))
        ]
        passed = s["passed"] if isinstance(s.get("passed"), bool) else all(a["passed"] for a in assertions)
        out.append({
            "name": str(s.get("name", "")),
            "category": str(s.get("category", "")),
            "faults": [str(f) for f in s.get("faults") or [] if isinstance(f, (str, int, float))]
            if isinstance(s.get("faults"), list) else [],
            "assertions": assertions,
            "passed": passed and all(a["passed"] for a in assertions),
        })  # fmt: skip
    return out


def not_applicable(result: dict[str, Any] | None) -> dict[str, str]:
    """Categories the script declared not applicable to the feature, each with its reason."""
    value = (result or {}).get("not_applicable")
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v).strip() for k, v in value.items() if isinstance(v, str) and v.strip()}


def assertion_outcomes(result: dict[str, Any] | None) -> list[tuple[str, str, bool]]:
    """(scenario, assertion, passed) for every assertion in a `result.json`, in a stable order."""
    if result is None:
        return []
    return sorted((s["name"], a["name"], a["passed"]) for s in scenarios(result) for a in s["assertions"])


def failed_assertions(found: list[dict[str, Any]]) -> list[str]:
    """`scenario: assertion` for each failed assertion; a failed scenario with none failing is listed by name."""
    out = []
    for s in found:
        bad = [a["name"] for a in s["assertions"] if not a["passed"]]
        if not s["passed"] and not bad:
            bad = ["failed"]
        out += [f"{s['name']}: {a}" if s["name"] else a for a in bad]
    return out


@dataclass
class Run:
    """One run of a drive script, with one seed."""

    seed: int
    verdict: str
    summary: str
    directory: str
    lines: list[str] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # "scenario: assertion" for each failed assertion
    not_applicable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "verdict": self.verdict,
            "summary": redact(self.summary),
            "evidence_dir": self.directory,
            "scenarios": [
                {"name": redact(s["name"]), "category": s["category"], "faults": [redact(f) for f in s["faults"]],
                 "passed": s["passed"]}
                for s in self.scenarios
            ],
            "failed": [redact(f) for f in self.failed[:10]],
            "not_applicable": {k: redact(v) for k, v in self.not_applicable.items()},
        }  # fmt: skip


class EvidenceChecker(Checker):
    """Run a script that drives the real app and must leave artifacts behind.

    The script gets `RUNG_EVIDENCE_DIR`, a directory unique to this run; anything it writes there
    (a video, a trace, a terminal transcript, screenshots, response bodies) is hashed into the ledger.
    A script that exits 0 but records nothing fails: "it works" without evidence is exactly what
    this rung exists to reject. `require` can demand particular artifact types, e.g. `[".webm"]`.
    The drive script itself belongs in `specs`, so editing it stales the verdict.

    The script also gets `RUNG_SEED`, and runs once per seed (`runs`, from `seed` or a seed derived
    from the check id), each run in its own directory. It derives every random choice and injected
    fault from the seed, so a failing seed reproduces with `rung check <id> --seed N`. `faults`, when
    set, is passed as `RUNG_FAULTS`, and each named scenario must appear in the run's `result.json`:
    Rung does not take a script's word that it injected a fault it never reports.

    Without `run`, the check is *attested*: an agent drives the app with its own browser or
    computer-use tool, records it, and hands the files to `rung attest`. Rung cannot re-run that, so
    the verdict holds only until a covered file changes, and running the check just reports whether
    fresh evidence exists.
    """

    kind = "evidence"
    summary = "Drive the real app and record evidence artifacts"

    def default_covers(self, check: Check) -> list[str]:
        return list(WHOLE_PROJECT)

    def run(self, ctx: Context, check: Check) -> Outcome:
        if is_attested(check):
            return Outcome(
                "error",
                f"no fresh evidence: drive the feature, record it, then `rung attest {check.id} <files> --by <tool>`",
            )
        out_dir = evidence_dir(ctx.root, check.id)
        artifacts: dict[str, str] = {}
        runs = [self._run_seed(ctx, check, out_dir, seed, artifacts) for seed in seeds(check, ctx.seed)]
        lines = [f"evidence: {path}" for path in artifacts]
        for r in runs:
            lines.append(f"seed {r.seed}: {r.verdict}: {r.summary}")
            lines += [f"  {line}" for line in r.lines]
        details: dict[str, Any] = {"runs": [r.to_dict() for r in runs]}
        bad = [r for r in runs if r.verdict != "pass"]
        if not bad:
            span = f"seed {runs[0].seed}"
            if len(runs) > 1:
                span = f"{len(runs)} runs, seeds {runs[0].seed}-{runs[-1].seed}"
            outcome = Outcome("pass", f"{len(artifacts)} artifact(s) recorded ({span})", lines, artifacts)
        else:
            first = next((r for r in bad if r.verdict == "fail"), bad[0])
            details["failing_seed"] = first.seed
            broken = [s["name"] for s in first.scenarios if not s["passed"]]
            if broken:
                details["failing_scenario"] = redact(broken[0])
            details["reproduce"] = reproduce_command(check.id, first.seed)
            prefix = f"seed {first.seed}"
            if len(runs) > 1:
                prefix = f"{len(bad)} of {len(runs)} runs failed; {prefix}"
            verdict = "fail" if any(r.verdict == "fail" for r in runs) else "error"
            outcome = Outcome(verdict, f"{prefix}: {first.summary}", lines, artifacts)
        page = evidence_page.write(
            check.id, details["runs"], {r.seed: out_dir / f"seed-{r.seed}" for r in runs}, out_dir,
            summary=redact(outcome.summary), reproduce=details.get("reproduce", ""),
        )  # fmt: skip
        details["report"] = page.relative_to(ctx.root).as_posix()
        outcome.lines.append(f"evidence page: {details['report']}")
        if "reproduce" in details:  # a failure ends with the one command to run next
            outcome.lines.append(f"reproduce: {details['reproduce']}")
        outcome.details = details
        return outcome

    def _run_seed(self, ctx: Context, check: Check, out_dir: Path, seed: int, artifacts: dict[str, str]) -> Run:
        run_dir = out_dir / f"seed-{seed}"
        run_dir.mkdir()
        rel = run_dir.relative_to(ctx.root).as_posix()
        env = {"RUNG_EVIDENCE_DIR": str(run_dir), "RUNG_SEED": str(seed)}
        faults = check.get_list("faults")
        if "faults" in check.options:
            env["RUNG_FAULTS"] = ",".join(faults)
        wanted = list(ctx.scenarios) or check.get_list("scenarios")
        if wanted:
            env["RUNG_SCENARIOS"] = ",".join(wanted)
        command = check.require("run")
        try:
            result = _run(ctx, check, env)
        except subprocess.TimeoutExpired as exc:
            output = "".join(o if isinstance(o, str) else (o or b"").decode(errors="replace")
                             for o in (exc.stdout, exc.stderr))  # fmt: skip
            (run_dir / "run.log").write_text(redact(output))
            return Run(seed, "error", f"`{command}` timed out after {exc.timeout:g}s", rel)
        (run_dir / "run.log").write_text(redact(result.stdout + result.stderr))
        mine = {
            p.relative_to(ctx.root).as_posix(): ledger.file_digest(p)
            for p in sorted(run_dir.rglob("*"))
            if p.is_file() and p.name != "run.log" and p.stat().st_size > 0
        }
        artifacts.update(mine)
        data = read_result(run_dir)
        found = scenarios(data) if data else []
        failed = failed_assertions(found)
        names = {s["name"] for s in found}
        run = Run(seed, "pass", f"{len(mine)} artifact(s)", rel, scenarios=found, failed=failed,
                  not_applicable=not_applicable(data))  # fmt: skip
        if found:
            run.summary += f", {len(found)} scenario(s)"
        if result.returncode != 0:
            run.verdict, run.summary = "fail", f"`{command}` exited {result.returncode}"
            if failed:
                run.summary += f"; failed: {'; '.join(failed[:3])}"
            # result.json is the last stdout line by convention, and is already summarised above.
            run.lines = [line if len(line) <= 240 else line[:237] + "..." for line in tail(result, 10)]
        elif not mine:
            run.verdict, run.summary = "fail", "the script exited 0 but recorded no evidence"
        elif missing := missing_required(check, mine):
            run.verdict, run.summary = "fail", f"required evidence missing: {', '.join(missing)}"
        elif failed:
            run.verdict, run.summary = (
                "fail",
                f"the script exited 0, but result.json records failures: {'; '.join(failed[:3])}",
            )
        elif data is not None and "seed" in data and data["seed"] != seed:
            run.verdict, run.summary = "fail", f"result.json reports seed {data['seed']!r}, but Rung passed {seed}"
        elif absent := [f for f in (*faults, *wanted) if f not in names]:
            run.verdict = "fail"
            run.summary = f"result.json shows no scenario named {', '.join(absent)}, which Rung asked for"
        return run
