"""Command checkers: any test suite, linter or script, and evidence-producing drive scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from rung.checks import prove
from rung.checks.base import Checker, Context, Outcome
from rung.core import launch, ledger, proc, sandbox
from rung.core.config import Check, ConfigError, managed_path, write_atomic
from rung.core.redact import plain_text, redact
from rung.engines import evidence_page

# A command check that declares no coverage covers the whole project: any change stales it.
# Narrow `covers` to make it cheaper to keep fresh, never to make it easier to trust.
WHOLE_PROJECT = ["**/*"]


def package_script_commands(cwd: Path, argv: list[str]) -> list[list[str]]:
    """Read package scripts invoked directly by a configured Node package manager."""
    if not argv or Path(argv[0]).name not in {"npm", "pnpm", "yarn", "bun"}:
        return []
    args = argv[2:] if argv[1:2] == ["run"] else argv[1:]
    if not args or args[0].startswith("-"):
        return []
    name = args[0]
    try:
        scripts = json.loads((cwd / "package.json").read_text()).get("scripts", {})
        return [shlex.split(scripts[key]) for key in (name, f"pre{name}", f"post{name}") if key in scripts]
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def tail(result: subprocess.CompletedProcess[str], lines: int = 15) -> list[str]:
    """The end of the output, where test runners and scripts print their verdict.

    A passing run shows the end of stdout (stderr only if stdout is empty), because tools like
    `uv run` write build chatter to stderr. On failure, preserve the start of the stderr
    diagnostic as well as its end, where runners often print a summary.
    """
    out, err = result.stdout.strip().splitlines(), result.stderr.strip().splitlines()
    if result.returncode == 0 or not err:
        return (out or err)[-lines:]
    prefix = [*out[-3:], "stderr:"] if out and lines > 5 else []
    room = lines - len(prefix)
    if len(err) <= room or room < 3:
        return [*prefix, *err[-room:]]
    leading = (room + 1) // 2
    trailing = room - leading - 1
    return [*prefix, *err[:leading], "…", *(err[-trailing:] if trailing else [])]


def _run(
    ctx: Context, check: Check, env: dict[str, str] | None = None, *, argv: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:  # fmt: skip
    reserved = {"RUNG_EVIDENCE_DIR", "RUNG_SEED", "RUNG_FAULTS", "RUNG_SCENARIOS"}
    source = os.environ if ctx.environment is None else ctx.environment
    inherited = {key: value for key, value in source.items() if key not in reserved}
    return proc.run(
        [*ctx.command_prefix, *(argv or shlex.split(check.require("run")))],
        cwd=ctx.root / check.get("cwd", "."),
        env=inherited | (env or {}),
        timeout=check.get_int("timeout", 1800),
    )


def cargo_test(argv: list[str]) -> bool:
    if not argv or Path(argv[0]).name not in {"cargo", "cargo.exe"}:
        return False
    args = argv[2:] if len(argv) > 1 and argv[1].startswith("+") else argv[1:]
    return args[:1] == ["test"]


def _unittest_passed(output: str) -> bool:
    counts = re.findall(r"^Ran (\d+) tests? in ", output, re.M)
    summary = re.findall(r"^OK(?: \(([^)]*)\))?$", output, re.M)
    if not counts or not summary:
        return False
    excluded = re.findall(r"(?:skipped|expected failures)=(\d+)", summary[-1])
    return int(counts[-1]) > sum(map(int, excluded))


def _default_node_tests(argv: list[str]) -> bool:
    return (
        bool(argv)
        and Path(argv[0]).name in {"node", "node.exe"}
        and "--test" in argv
        and not any(arg.startswith("--test-reporter") for arg in argv)
    )


def _junitxml_option(argv: list[str]) -> str | None:
    """The report path a pytest command already passes with `--junitxml` (or `--junit-xml`), if any."""
    end = argv.index("--") if "--" in argv and prove.runs_pytest(argv[: argv.index("--")]) else len(argv)
    found = None
    for i, arg in enumerate(argv[:end]):
        flag, equals, value = arg.partition("=")
        if flag in {"--junitxml", "--junit-xml"}:
            found = value if equals else (argv[i + 1] if i + 1 < end else None)
    return found or None


class CommandChecker(Checker):
    kind = "command"
    summary = "Run a command; exit status 0 passes"

    def default_covers(self, check: Check) -> list[str]:
        return list(WHOLE_PROJECT)

    def run(self, ctx: Context, check: Check) -> Outcome:
        argv = shlex.split(check.require("run"))
        collect_only = "--collect-only" in argv or "--co" in argv
        if prove.runs_pytest(argv) and (ctx.merge_gate or not collect_only):
            return self._run_pytest(ctx, check, argv)
        cwd = ctx.root / check.get("cwd", ".")
        inner_argv = argv
        while inner := launch.delegated(inner_argv, cwd):
            inner_argv, cwd = inner.argv, inner.cwd
        tool = Path(inner_argv[0]).name
        if tool in {"go", "go.exe"} and inner_argv[1:2] == ["test"]:
            return self._run_go(ctx, check, argv)
        node_suites = [inner_argv, *package_script_commands(cwd, inner_argv)]
        if any(map(_default_node_tests, node_suites)):
            return self._run_node(ctx, check, argv)
        result = _run(ctx, check, argv=argv)
        if (
            result.returncode in {0, 5}
            and launch.python_entrypoint(inner_argv) == ("module", "unittest")
            and not _unittest_passed(plain_text(result.stdout + "\n" + result.stderr))
        ):
            return Outcome("fail", "unittest reported no passing test cases", tail(result))
        if (
            cargo_test(inner_argv)
            and (ctx.merge_gate or "--no-run" not in inner_argv)
            and not result.returncode
            and not re.search(r"^test result: ok\. [1-9]\d* passed;", plain_text(result.stdout), re.M)
        ):
            return Outcome("fail", "Cargo reported no passing test cases", tail(result))
        verdict = "pass" if result.returncode == 0 else "fail"
        return Outcome(verdict, f"`{check.require('run')}` exited {result.returncode}", tail(result))

    def _run_node(self, ctx: Context, check: Check, argv: list[str]) -> Outcome:
        with tempfile.TemporaryDirectory(prefix="rung-node-cases-") as tmp:
            report = Path(tmp) / "cases.jsonl"
            inherited = os.environ if ctx.environment is None else ctx.environment
            flags = " ".join(json.dumps(flag) for flag in prove.node_reporter_flags(report))
            options = f"{inherited.get('NODE_OPTIONS', '')} {flags}".strip()
            result = _run(ctx, check, {"NODE_OPTIONS": options}, argv=argv)
            if result.returncode:
                return Outcome("fail", f"Node test command exited {result.returncode}", tail(result))
            records = prove.node_records(report, ctx.root.resolve())
            passed = sum(state == "passed" for _, _, state in records)
            if not passed:
                return Outcome("fail", "Node reported no passing test cases", tail(result))
            return Outcome("pass", f"Node passed with {passed} passing test case(s)", tail(result))

    def _run_go(self, ctx: Context, check: Check, argv: list[str]) -> Outcome:
        """Count individual test passes, excluding package-level success events."""
        boundary = argv.index("-args") if "-args" in argv else len(argv)
        args = argv if "-json" in argv[:boundary] else [*argv[:boundary], "-json", *argv[boundary:]]
        result = _run(ctx, check, argv=args)
        passed = 0
        output = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                output.append(line)
                continue
            if not isinstance(event, dict):
                continue
            if event.get("Action") == "pass" and event.get("Test"):
                passed += 1
            if message := event.get("Output"):
                output.append(message.rstrip("\n"))
        result.stdout = "\n".join(output)
        if result.returncode:
            return Outcome("fail", f"go test exited {result.returncode}", tail(result))
        if not passed:
            return Outcome("fail", "Go reported no passing test cases", tail(result))
        return Outcome("pass", f"Go passed with {passed} passing test case(s)", tail(result))

    def _run_pytest(self, ctx: Context, check: Check, argv: list[str]) -> Outcome:
        """Require a passing test case in a fresh report before trusting an exit of zero."""
        with tempfile.TemporaryDirectory(prefix="rung-pytest-") as tmp:
            if own := _junitxml_option(argv):
                # The user's report path is kept: appending a second option would silently replace it.
                report = ctx.root / check.get("cwd", ".") / own
                args = argv
            else:
                report = Path(tmp) / "results.xml"
                option = f"--junitxml={report}"
                if "--" in argv and prove.runs_pytest(argv[: argv.index("--")]):
                    separator = argv.index("--")
                    args = [*argv[:separator], option, *argv[separator:]]
                else:
                    args = [*argv, option]
            result = _run(ctx, check, argv=args)
            if result.returncode:
                return Outcome("fail", f"pytest exited {result.returncode}", tail(result))
            try:
                cases = list(ET.parse(report).iter("testcase"))
            except (OSError, ET.ParseError):
                return Outcome("error", "pytest exited zero without a usable case report", tail(result))
            passed = sum(not {node.tag for node in case} & {"skipped", "error", "failure"} for case in cases)
            if not passed:
                return Outcome("fail", "pytest exited zero but reported no passing test cases", tail(result))
            return Outcome("pass", f"pytest passed with {passed} passing test case(s)", tail(result))


def evidence_dir(root: Path, check_id: str) -> Path:
    """A new directory, unique to this run, under `.rung/evidence/<check>/`."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = managed_path(root, Path(".rung/evidence") / check_id / f"{stamp}-{uuid.uuid4().hex[:8]}")
    out_dir.mkdir(parents=True, exist_ok=False)
    return out_dir


def missing_required(check: Check, artifacts: Iterable[str]) -> list[str]:
    """Required artifact suffixes (`require`) that none of `artifacts` has."""
    names = [a.lower() for a in artifacts]
    return [r for r in (s.lower() for s in check.get_list("require")) if not any(n.endswith(r) for n in names)]


def _png(content: BinaryIO) -> bool:
    if content.read(8) != b"\x89PNG\r\n\x1a\n":
        return False
    first, image_data = True, False
    while len(header := content.read(8)) == 8:
        length = int.from_bytes(header[:4], "big")
        chunk = header[4:]
        if first and (chunk != b"IHDR" or length != 13):
            return False
        if length > 0x7FFFFFFF:
            return False
        crc = zlib.crc32(chunk)
        remaining = length
        while remaining:
            data = content.read(min(remaining, 65536))
            if not data:
                return False
            crc = zlib.crc32(data, crc)
            remaining -= len(data)
        if content.read(4) != crc.to_bytes(4, "big"):
            return False
        if chunk == b"IEND":
            return image_data and length == 0 and not content.read(1)
        image_data |= chunk == b"IDAT" and length > 0
        first = False
    return False


def recognizable_artifact(path: Path) -> bool:
    """Reject mislabeled text. A recognizable file format cannot establish what the media depicts."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".webm":
            with path.open("rb") as content:
                start = content.read(1024 * 1024)
            return (
                start.startswith(b"\x1a\x45\xdf\xa3")
                and b"webm" in start[:256]
                and all(element in start for element in (b"\x18\x53\x80\x67", b"\x16\x54\xae\x6b", b"\x1f\x43\xb6\x75"))
            )
        if suffix == ".png":
            with path.open("rb") as content:
                return _png(content)
        if suffix == ".gif":
            with path.open("rb") as content:
                header = content.read(16)
                return (
                    len(header) == 16
                    and header.startswith((b"GIF87a", b"GIF89a"))
                    and (content.seek(-1, os.SEEK_END) > 16 and content.read(1) == b";")
                )
        if suffix in {".jpg", ".jpeg"}:
            with path.open("rb") as content:
                start = content.read(1024 * 1024)
                return (
                    start.startswith(b"\xff\xd8\xff")
                    and b"\xff\xda" in start
                    and content.seek(-2, os.SEEK_END) > 3
                    and content.read(2) == b"\xff\xd9"
                )
        if suffix == ".zip":
            return zipfile.is_zipfile(path)
        if suffix == ".har":
            log = json.loads(path.read_text()).get("log")
            return isinstance(log, dict) and isinstance(log.get("entries"), list)
    except (OSError, ValueError, AttributeError):
        return False
    return True


def missing_artifacts(check: Check, paths: Iterable[Path]) -> list[str]:
    """Known media types must be recognizable before satisfying a required suffix."""
    required = tuple(s.lower() for s in check.get_list("require"))
    return missing_required(
        check, (path.name for path in paths if path.name.lower().endswith(required) and recognizable_artifact(path))
    )


def is_attested(check: Check) -> bool:
    """An evidence check without `run`: an agent or a person records the evidence (`rung attest`)."""
    return check.kind == "evidence" and "run" not in check.options


def isolates_evidence(check: Check) -> bool:
    """Whether a scripted evidence check runs in an isolated copy: only when it says `isolate = true`.

    `rung draft runtime` writes `isolate = true` for the checks it registers. A check drafted before
    that (with only `drafted_by`) keeps running un-isolated until `isolate = true` is added.
    """
    return check.kind == "evidence" and not is_attested(check) and check.get("isolate") is True


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
    path = run_dir / "result.json"
    if path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text())
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


def structured_problem(result: dict[str, Any] | None) -> str:
    """Check the runtime result shape before trusting exit status and artifacts."""
    if result is None or type(result.get("seed")) is not int:
        return "result.json must be an object that records the integer seed"
    raw = result.get("scenarios")
    entries = _items(raw)
    if not isinstance(raw, list) or not entries or len(entries) != len(raw):
        return "result.json must record a non-empty array of scenarios"
    seen: set[str] = set()
    for scenario in entries:
        name = scenario.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen:
            return "scenario names must be non-empty and unique"
        seen.add(name)
        raw_assertions = scenario.get("assertions")
        assertions = _items(raw_assertions)
        if (
            not isinstance(raw_assertions, list)
            or not assertions
            or len(assertions) != len(raw_assertions)
            or any(not isinstance(assertion.get("passed"), bool) for assertion in assertions)
        ):
            return f"scenario {name!r} must record assertions with boolean outcomes"
    return ""


def assertion_outcomes(result: dict[str, Any] | None) -> list[tuple[str, str, bool]]:
    """(scenario, assertion, passed) for every assertion in a `result.json`, in a stable order."""
    if result is None:
        return []
    return sorted((s["name"], a["name"], a["passed"]) for s in scenarios(result) for a in s["assertions"])


MAX_LISTED_ARTIFACTS = 6
MAX_FAILURE_LINES = 12


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def failure_details(result: dict[str, Any] | None) -> list[str]:
    """`scenario: assertion (expected X, observed Y)` for each failed assertion in a `result.json`."""
    out: list[str] = []
    for s in _items((result or {}).get("scenarios")):
        for a in _items(s.get("assertions")):
            if a.get("passed") is True:
                continue
            name = str(a.get("name", a.get("assertion", "")))
            seen = ""
            if "expected" in a or "observed" in a:
                seen = f" (expected {_short(a.get('expected', '?'))}, observed {_short(a.get('observed', '?'))})"
            out.append(f"{s.get('name') or 'default'}: {name}{seen}")
    if len(out) > MAX_FAILURE_LINES:
        out = [*out[:MAX_FAILURE_LINES], f"... and {len(out) - MAX_FAILURE_LINES} more; see the evidence page"]
    return out


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
        if isolates_evidence(check) and not ctx.command_prefix:
            return self._run_isolated(ctx, check)
        return self._run_drive(ctx, check)

    def _run_isolated(self, ctx: Context, check: Check) -> Outcome:
        with sandbox.copy_of(ctx.root) as copy:
            copy = copy.resolve()
            if not (copy / check.get("cwd", ".")).resolve().is_relative_to(copy):
                raise ConfigError("the isolated drive's working directory must be inside its copy")
            argv = shlex.split(check.require("run"))
            install_packages = Path(argv[0]).name == "uv" and "--with" in argv
            guarded = replace(
                ctx,
                root=copy,
                command_prefix=sandbox.validated_write_guard(copy, network=install_packages),
                environment=sandbox.trial_environment(copy),
            )
            patterns = [*self.covers(check), *self.specs(check)]
            before = ledger.snapshot(copy, patterns)
            outcome = self._run_drive(guarded, check)
            if ledger.snapshot(copy, patterns) != before:
                return Outcome("error", "the isolated drive modified covered inputs in its copy")
            sandbox.retain_evidence(ctx.root, copy, outcome.details["report"])
            outcome.details["execution"] = "isolated"
            return outcome

    def _run_drive(self, ctx: Context, check: Check) -> Outcome:
        out_dir = evidence_dir(ctx.root, check.id)
        artifacts: dict[str, str] = {}
        runs = [self._run_seed(ctx, check, out_dir, seed, artifacts) for seed in seeds(check, ctx.seed)]
        if len(artifacts) <= MAX_LISTED_ARTIFACTS:
            lines = [f"evidence: {path}" for path in artifacts]
        else:  # every file is still hashed into the verdict; the evidence page shows them all
            lines = [f"evidence: {len(artifacts)} files in {out_dir.relative_to(ctx.root).as_posix()}/"]
        for r in runs:
            if len(runs) == 1:  # the headline already says it
                lines += r.lines
                continue
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
        managed_path(ctx.root, out_dir.relative_to(ctx.root) / "index.html")
        page = evidence_page.write(
            check.id, details["runs"], {r.seed: out_dir / f"seed-{r.seed}" for r in runs}, out_dir,
            summary=redact(outcome.summary), reproduce=details.get("reproduce", ""),
        )  # fmt: skip
        details["report"] = page.relative_to(ctx.root).as_posix()
        outcome.evidence[details["report"]] = ledger.file_digest(page)
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
            log = managed_path(ctx.root, Path(rel) / "run.log")
            write_atomic(log, redact(output))
            return Run(seed, "error", f"`{command}` timed out after {exc.timeout:g}s", rel)
        log = managed_path(ctx.root, Path(rel) / "run.log")
        write_atomic(log, redact(result.stdout + result.stderr))
        mine = {
            p.relative_to(ctx.root).as_posix(): ledger.file_digest(p)
            for p in sorted(run_dir.rglob("*"))
            if not p.is_symlink() and p.is_file() and p.name != "run.log" and p.stat().st_size > 0
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
                broken = [s["name"] or "default" for s in found if not s["passed"]]
                run.summary = f"{len(broken)} of {len(found)} scenario(s) failed: {', '.join(broken[:4])}"
                run.summary += " ..." if len(broken) > 4 else ""
                # What each failed assertion expected and what the app did, rather than the script's raw output.
                run.lines = failure_details(data)
            else:  # no result.json to explain it: the script's own last lines say why it stopped
                run.lines = [line if len(line) <= 240 else line[:237] + "..." for line in tail(result, 10)]
        elif not mine:
            run.verdict, run.summary = "fail", "the script exited 0 but recorded no evidence"
        elif missing := missing_artifacts(check, (ctx.root / name for name in mine)):
            run.verdict, run.summary = "fail", f"required evidence missing: {', '.join(missing)}"
        elif failed:
            run.verdict, run.summary = (
                "fail",
                f"the script exited 0, but result.json records failures: {'; '.join(failed[:3])}",
            )
            run.lines = failure_details(data)
        elif data is not None and "seed" in data and data["seed"] != seed:
            run.verdict, run.summary = "fail", f"result.json reports seed {data['seed']!r}, but Rung passed {seed}"
        elif (run_dir / "result.json").exists() and data is None:
            run.verdict, run.summary = "error", "result.json must contain a JSON object"
        elif data is not None and data.get("passed") is False:
            run.verdict, run.summary = "fail", "result.json reports passed=false"
        elif check.get("structured") and (problem := structured_problem(data)):
            run.verdict, run.summary = "error", problem
        elif absent := [f for f in (*faults, *wanted) if f not in names]:
            run.verdict = "fail"
            run.summary = f"result.json shows no scenario named {', '.join(absent)}, which Rung asked for"
        return run
