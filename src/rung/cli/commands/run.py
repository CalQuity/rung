"""Commands that run and report checks: check, status, report, prove, ledger."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rung.analysis import plan, report
from rung.checks import daytona, prove, runner
from rung.checks.command import cargo_test, is_attested, package_script_commands
from rung.cli.commands.common import GATE_FAILED, OK, load_config, resolve_base
from rung.cli.output import STATE_STYLE, VERDICT_STYLE, Output
from rung.core import git, launch, laws, ledger, modelcard
from rung.core.config import (
    CONFIG_NAME,
    DEFAULT_APPROVAL_LABEL,
    GATE_KEYS,
    Check,
    Config,
    ConfigError,
    find_root,
    parse_gate,
)
from rung.core.redact import redact, redact_lines

POLICY_PATHS = (
    "**/rung.toml",
    "**/LAWS.md",
    "**/.rung",
    "**/.rung/waivers.toml",
    "**/.rung/baselines/**",
    "**/.gitleaks.toml",
    "**/.gitleaksignore",
    "**/.semgrep.yml",
    "**/.semgrepignore",
    "**/.rung/verify/**",
    "**/.rung/formal/**",
    ".github/workflows/**",
    ".github/actions/**",
)


def _print_result(result: runner.Result, out: Output) -> None:
    o = result.outcome
    label = f"{result.check.id} ({result.check.kind})"
    if out.columns and len(label) + 6 > out.columns:
        out.line(out.badge(o.verdict, VERDICT_STYLE, 0))
        out.detail(label, indent=2)
    else:
        out.line(f"{out.badge(o.verdict, VERDICT_STYLE, 6)}{out.style(label, 'bold')}")
    out.detail(redact(o.summary))
    report_path = str(o.details.get("report") or "")
    compact_evidence = out.columns and out.columns < 60 and report_path
    for line in redact_lines(o.lines):
        if o.details.get("reproduce") and line.startswith("reproduce: "):
            continue
        if compact_evidence and line.startswith(("evidence: ", "evidence page: ")):
            continue
        label, separator, path = line.partition(": ")
        location = re.match(r'^\s*File "([^"]+)", line (\d+)(?:, in (.*))?$', line)
        if out.columns and location:
            file, number, frame = location.groups()
            source = Path(file)
            if source.is_relative_to(Path.cwd()):
                source = source.relative_to(Path.cwd())
            out.command(f"{source}:{number}", prefix="at ", indent=6)
            if frame and frame != "<module>":
                out.detail(f"in {frame}", indent=8)
        elif separator and (path in o.evidence or path == report_path):
            out.location(path, label=label + separator)
        else:
            out.detail(line)
    if compact_evidence:
        count = len(o.evidence) - (report_path in o.evidence)
        out.detail(f"Evidence · {count} file(s)", indent=2)
        out.command(redact(report_path), prefix="Report: ", indent=2)


def _result_data(result: runner.Result) -> dict:
    o = result.outcome
    return {**o.details, "id": result.check.id, "kind": result.check.kind, "verdict": o.verdict,
            "summary": redact(o.summary), "lines": redact_lines(o.lines), "findings": o.findings,
            "artifacts": o.evidence}  # fmt: skip


def _run_checks(
    cfg: Config, selected: list[Check], out: Output, *, base: str | None,
    seed: int | None = None, scenarios: tuple[str, ...] = (), merge_gate: bool = False,
) -> list[runner.Result]:  # fmt: skip
    results = []
    for i, check in enumerate(selected, 1):
        out.progress(f"[{i}/{len(selected)}] Running {check.id}")
        result = runner.run_check(cfg, check, base=base, seed=seed, scenarios=scenarios, merge_gate=merge_gate)
        results.append(result)
        _print_result(result, out)
    return results


def _print_summary(results: list[runner.Result], out: Output) -> None:
    if not results:
        return
    counts = Counter(result.verdict for result in results)
    items = [
        out.style(f"{counts[state]} {label}", color)
        for state, label, color in (
            ("pass", "passed", "green"),
            ("fail", "failed", "red"),
            ("error", "errors", "yellow"),
        )
        if counts[state]
    ]
    out.line()
    out.line("  ".join(items))
    if reproduce := next((r.outcome.details["reproduce"] for r in results if r.outcome.details.get("reproduce")), ""):
        out.command(reproduce, prefix="reproduce: ")


def _commands(checks: list[Check]) -> list[list[str]]:
    return [shlex.split(command) for check in checks for option in ("run", "model", "impl")
            if (command := check.get(option))]  # fmt: skip


def _manifest_policy(name: str, source: str) -> tuple:
    if name == "package.json":
        data = json.loads(source)
        return data.get("packageManager"), data.get("scripts", {})
    tool = tomllib.loads(source).get("tool", {})
    return (tool.get("pytest", {}),)


def _runner_policy_changes(root: Path, base: str, changed: set[str], checks: list[Check]) -> set[str]:
    """Known config files and script definitions that can silently disable configured runners."""
    commands = _commands(checks)
    executables = {Path(argv[0]).name for argv in commands}
    manifests = set()
    patterns: list[str] = []
    if executables & {"npm", "pnpm", "yarn", "bun", "node", "npx"}:
        manifests.add("package.json")
        patterns += [
            "**/jest.config.*",
            "**/vitest.config.*",
            "**/.npmrc",
            "**/.yarnrc",
            "**/.yarnrc.yml",
            "**/.pnpmfile.cjs",
            "**/pnpm-workspace.yaml",
        ]
    if executables & {"make", "gmake"}:
        patterns += ["**/Makefile", "**/makefile", "**/GNUmakefile", "**/*.mk"]
    if any(prove.runs_pytest(argv) for argv in commands):
        manifests.add("pyproject.toml")
        patterns += [
            "**/pytest.ini",
            "**/.pytest.ini",
            "**/tox.ini",
            "**/setup.cfg",
            "**/.coveragerc",
            "**/conftest.py",
        ]

    protected = {path for path in changed if any(git.glob_match(pattern, path) for pattern in patterns)}
    for name in changed:
        filename = Path(name).name
        if filename not in manifests:
            continue
        empty = "{}" if filename == "package.json" else ""
        try:
            old = git.show(root, base, name)
        except git.GitError:
            old = empty
        path = root / name
        if path.is_symlink():
            protected.add(name)
            continue
        try:
            current = path.read_text() if path.is_file() else empty
            if _manifest_policy(filename, old) != _manifest_policy(filename, current):
                protected.add(name)
        except (OSError, ValueError, AttributeError):
            protected.add(name)
    return protected


def _launch_scripts(repo: Path, config_root: Path, checks: list[Check]) -> set[str]:
    """Treat direct launch scripts, including those named by package scripts, as policy."""
    scripts = set()
    for check in checks:
        for option in ("run", "model", "impl"):
            if not (command := check.get(option)):
                continue
            cwd_key = "cwd" if option == "run" else f"{option}_cwd"
            cwd = config_root / check.get(cwd_key, ".")
            argv = shlex.split(command)
            for invocation in [argv, *package_script_commands(cwd, argv)]:
                for path in launch.file_inputs(invocation, cwd):
                    scripts.update(
                        candidate.relative_to(repo).as_posix()
                        for candidate in launch.linked_paths(path)
                        if candidate.is_relative_to(repo)
                    )
    return scripts


_STANDARD_CHECKS = {
    "go": {"test", "vet"},
    "cargo": {"clippy"},
    "node": {"--test"},
    "mvn": {"test"},
    "mvnw": {"test"},
    "gradle": {"test"},
    "gradlew": {"test"},
    "mix": {"test"},
    "swift": {"test"},
    "rake": {"test"},
    "ruff": {"check", "format"},
    "next": {"typegen"},
}


def _standard_runner(argv: list[str], cwd: Path, *, package_scripts: bool = True) -> bool:
    if prove.runs_pytest(argv) or cargo_test(argv):
        return True
    while inner := launch.delegated(argv, cwd):
        argv, cwd = inner.argv, inner.cwd
    if not argv:
        return False
    tool = Path(argv[0]).name
    if tool == "npx" and argv[1:2] == ["--no-install"]:
        return _standard_runner(argv[2:], cwd, package_scripts=False)
    if tool in {"pnpm", "yarn", "bun", "bundle"} and argv[1:2] == ["exec"]:
        return _standard_runner(argv[2:], cwd, package_scripts=False)
    if tool in {"vitest", "jest", "mocha", "ava", "eslint", "tsc", "pyright", "mypy", "lint-imports"}:
        return True
    if tool == "node" and any(Path(arg).suffix in {".js", ".mjs", ".cjs", ".ts"} for arg in argv[1:]):
        return False
    if len(argv) > 1 and argv[1] in _STANDARD_CHECKS.get(tool, set()):
        return True
    if launch.python_entrypoint(argv) in {("module", "unittest"), ("module", "ruff"), ("module", "mypy")}:
        return True
    scripts = package_script_commands(cwd, argv) if package_scripts else []
    return bool(scripts) and all(_standard_package_script(script, cwd) for script in scripts)


def _standard_package_script(script: list[str], cwd: Path) -> bool:
    commands: list[list[str]] = [[]]
    for token in script:
        if token == "&&":
            commands.append([])
        elif any(operator in token for operator in (";", "|", "&", "`", "$(", ">", "<")):
            return False
        else:
            commands[-1].append(token)
    return all(_standard_runner(command, cwd, package_scripts=False) for command in commands)


def _unmapped_policy(
    repo: Path,
    config: Config,
    checks: list[Check],
    paths: set[str],
    required: set[str],
    extra: list[str],
) -> list[str]:
    """Require a reviewable verifier scope before a custom check can claim changed targets.

    Args:
        repo: The repository root.
        config: The project configuration.
        checks: The checks selected for the gate.
        paths: Changed project-relative paths.
        required: Changed project-relative paths that require coverage.
        extra: Repository-relative policy globs from the base `[gate]` table or the trusted workflow.

    Returns:
        The ids of checks whose verifier files are neither declared in `specs` nor protected by
        policy globs. Each check is decided on its own: a policy glob exempts only the checks whose
        launch files it matches.
    """
    required_checks = {check.id for check in plan.focused_checks(checks, required)}
    missing = []
    for check in plan.focused_checks(checks, paths):
        if "verifies" not in check.options and check.id not in required_checks:
            continue
        if check.kind not in {"command", "evidence"} or not (command := check.get("run")):
            continue
        cwd = config.root / check.get("cwd", ".")
        declared = git.expand(config.root, check.get_list("specs"))
        if any((config.root / path).is_file() for path in declared) or _standard_runner(shlex.split(command), cwd):
            continue
        if not _policy_covers_verifier(repo, config.root, check, extra):
            missing.append(check.id)
    return missing


def _policy_covers_verifier(repo: Path, config_root: Path, check: Check, extra: list[str]) -> bool:
    """Whether policy globs protect every launch file of this check's own command.

    A policy glob that matches some other file says nothing about this check's verifier, so it
    never exempts the check.
    """
    if not extra:
        return False
    launched = sorted(path for path in _launch_scripts(repo, config_root, [check]) if (repo / path).is_file())
    return bool(launched) and all(any(git.glob_match(glob, path) for glob in extra) for path in launched)


def _project_globs(prefix: Path, globs: list[str]) -> list[str]:
    """Turn project-relative globs into repository-relative ones (`..` may name repository files)."""
    return [posixpath.normpath((prefix / glob).as_posix()) for glob in globs]


@dataclass(frozen=True)
class _GateSettings:
    policy_changes: str
    policy_paths: list[str]
    require_coverage: list[str]
    approval_label: str
    source: str


def _gate_settings(cfg: Config, base: str, args) -> tuple[_GateSettings, bool]:
    """Read the `[gate]` table from the base revision; trusted workflow inputs fill unset keys.

    Args:
        cfg: The configuration under review (its `[gate]` table is compared, never applied).
        base: The merge-base commit.
        args: Parsed `rung gate` arguments.

    Returns:
        The effective settings, and whether the change edits the `[gate]` table.

    Raises:
        ConfigError: The base revision's `rung.toml` has an invalid `[gate]` table.
    """
    try:
        raw = tomllib.loads(git.show(cfg.root, base, CONFIG_NAME))
    except (git.GitError, tomllib.TOMLDecodeError):
        raw = {}
    try:
        reviewed = parse_gate(raw)
    except ConfigError as exc:
        raise ConfigError(f"{CONFIG_NAME} on the merge base: {exc}") from None
    declared = any(getattr(reviewed, key) is not None for key in GATE_KEYS)
    settings = _GateSettings(
        policy_changes=reviewed.policy_changes or ("warn" if args.allow_policy_changes else "fail"),
        policy_paths=reviewed.policy_paths if reviewed.policy_paths is not None else list(args.policy_path),
        require_coverage=(
            reviewed.require_coverage if reviewed.require_coverage is not None else list(args.require_coverage)
        ),
        approval_label=reviewed.approval_label or DEFAULT_APPROVAL_LABEL,
        source="base" if declared else "options",
    )
    return settings, reviewed != cfg.gate


def _head_mismatch(root: Path, head: str, pr_head: str) -> str:
    """Explain why HEAD is not the pull request head or a merge commit of it ("" when it is)."""
    expected = pr_head.strip().lower()
    if head and head == expected:
        return ""
    try:
        # Parents of HEAD, one per line; a pull_request checkout is a merge of the base and the head.
        parents = git._git(root, "rev-parse", f"{head}^@").split() if head else []
    except git.GitError:
        parents = []
    if len(parents) >= 2 and expected in parents:
        return ""
    return (
        f"HEAD {head[:12] or '(none)'} is not the pull request head {expected[:12]} or a merge commit of it. "
        "Check out the pull request head (with pull_request_target, set the checkout ref to the head SHA)."
    )


def cmd_gate(args, out: Output) -> int:
    """Run a fresh merge gate under policy that was reviewed on the base."""
    if (args.executor == "daytona") != bool(args.daytona_snapshot):
        raise ConfigError("--executor daytona and --daytona-snapshot NAME must be used together")
    cfg = load_config(args)
    base = resolve_base(cfg.root, args.base)
    repo = cfg.root
    for _ in Path(git.prefix(cfg.root)).parts:
        repo = repo.parent
    settings, gate_changed = _gate_settings(cfg, base, args)
    approved = settings.approval_label in args.pr_label
    allow_policy = settings.policy_changes == "warn" or approved
    changed = git.changed_files(repo, base)
    prefix = cfg.root.relative_to(repo)
    # Policy globs are project-relative, like every other path in rung.toml.
    policy_globs = _project_globs(prefix, settings.policy_paths)
    configured = {str(prefix / pattern) for check in cfg.checks for pattern in runner.specs(check)}
    selected = [cfg.check(check_id) for check_id in args.ids] if args.ids else cfg.checks
    protected = {
        path
        for path in changed
        if any(git.glob_match(pattern, path) for pattern in (*POLICY_PATHS, *policy_globs, *configured))
    }
    protected |= _runner_policy_changes(repo, base, changed, selected)
    protected |= changed & _launch_scripts(repo, cfg.root, selected)
    policy_paths = sorted(protected)
    ids = {check.id for check in selected}
    scoped_changes = git.changed_files(cfg.root, base) if cfg.root != repo else changed
    uncovered = plan.uncovered_paths(selected, {
        path for path in scoped_changes if not git.is_rung_output(path)
    })  # fmt: skip
    head = git.head(cfg.root)
    blocked = []
    if args.pr_head and (mismatch := _head_mismatch(cfg.root, head, args.pr_head)):
        blocked.append(mismatch)
    blocked += [f"policy changed: {path}" for path in policy_paths if not allow_policy]
    if gate_changed and not approved:
        blocked.append(f"the [gate] table in {CONFIG_NAME} changed; it needs the {settings.approval_label!r} approval")
    required = {
        path for path in scoped_changes if any(git.glob_match(pattern, path) for pattern in settings.require_coverage)
    }
    missing_coverage = plan.uncovered_paths(selected, required, declared=True)
    blocked += [f"required coverage missing: {path}" for path in missing_coverage]
    unmapped = _unmapped_policy(repo, cfg, selected, scoped_changes, required, policy_globs)
    blocked += [f"{check}: declare custom verifier files with specs or policy_paths" for check in unmapped]
    blocked += [
        f"{check.id}: external capture cannot be re-run in a merge gate" for check in selected if is_attested(check)
    ]
    if not selected:
        blocked.append(f"no checks declared in {CONFIG_NAME}")
    for law in laws.load(cfg.root):
        if law.status != "approved":
            continue
        if not law.enforced_by:
            blocked.append(f"approved law {law.id} has no enforcing checks")
        elif missing := sorted(set(law.enforced_by) - ids):
            blocked.append(f"approved law {law.id} needs checks in this gate: {', '.join(missing)}")
        elif all(cfg.check(check_id).kind in modelcard.KINDS for check_id in law.enforced_by) and not any(
            modelcard.has_source_confirmation(modelcard.load(cfg.root, check_id)) for check_id in law.enforced_by
        ):
            blocked.append(f"approved law {law.id} needs a confirmed source link or an implementation check")
    out.heading("Merge gate", f"{cfg.name} · target {base[:12]} · {len(selected)} selected")
    if protected:
        note = " · approved" if approved else " · reported only" if allow_policy else ""
        out.line(out.style(f"Policy changes ({len(protected)}){note}", "yellow"))
        for path in policy_paths[:8]:
            out.detail(path, indent=2)
    if uncovered:
        out.heading("Coverage gaps", f"{len(uncovered)} path(s) have no command, runtime or formal check scope.")
        for path in uncovered[:8]:
            out.detail(path, indent=2)
    if len(protected) > 8 or len(uncovered) > 8:
        out.detail("Use --json for the full lists.", indent=2)
    if blocked:
        out.line(out.style("BLOCKED · No repository checks ran", "red"))
        for reason in blocked:
            if not reason.startswith("policy changed: "):
                out.detail(reason, indent=2)
        if (protected and not allow_policy) or (gate_changed and not approved):
            out.detail(
                f"Have a maintainer review the policy edits, then apply the {settings.approval_label!r} label.",
                indent=2,
            )
    receipt = {
        "schema_version": 1,
        "executor": args.executor,
        "base": base,
        "head": head,
        "passed": False,
        "policy_changes": policy_paths,
        "policy_approved": approved,
        "gate_settings": {
            "source": settings.source,
            "policy_changes": settings.policy_changes,
            "policy_paths": settings.policy_paths,
            "require_coverage": settings.require_coverage,
            "approval_label": settings.approval_label,
        },
        "uncovered_paths": uncovered,
        "required_coverage_missing": missing_coverage,
        "verifier_policy_missing": unmapped,
        "blocked": blocked,
        "stale": {},
        "selected": [check.id for check in selected],
        "results": [],
    }
    if blocked:
        out.emit(receipt)
        return GATE_FAILED
    if args.executor == "daytona":
        # The sandbox repeats the preflight from the same history, so it reads the same base [gate] table.
        options = [f"--policy-path={path}" for path in args.policy_path]
        options += [f"--require-coverage={path}" for path in args.require_coverage]
        options += [f"--pr-label={label}" for label in args.pr_label]
        if args.pr_head:
            options.append(f"--pr-head={args.pr_head}")
        if args.allow_policy_changes:
            options.append("--allow-policy-changes")
        request = daytona.GateRequest(repo, prefix, base, head, tuple(receipt["selected"]), tuple(options))
        out.heading("Daytona", "Running checks in an ephemeral sandbox")
        try:
            receipt.update(daytona.execute(request, args.daytona_snapshot))
        except daytona.SandboxError as exc:
            receipt["executor_error"] = str(exc)
            out.emit(receipt)
            out.error(str(exc))
            return GATE_FAILED
        out.emit(receipt)
        out.detail(f"Snapshot {receipt['snapshot']} · id {receipt['snapshot_id']}")
        for row in receipt["results"]:
            out.line(f"{out.badge(row['verdict'], VERDICT_STYLE, 6)}{row['id']}")
            out.detail(redact(row["summary"]))
        for reason in receipt["blocked"]:
            out.detail(reason)
        for check_id in receipt["stale"]:
            out.detail(f"No fresh passing evidence for {check_id}.")
        passed = receipt["passed"]
        out.line(out.style("Gate passed" if passed else "Gate failed", "green" if passed else "red"))
        return OK if passed else GATE_FAILED
    results = _run_checks(cfg, selected, out, base=base, merge_gate=True)
    view = ledger.View(cfg.root)
    stale = {
        result.check.id: status.changed
        for result in results
        if result.verdict == "pass" and not (status := runner.status(cfg, result.check, base=base, view=view)).trusted
    }
    if git.head(cfg.root) != head:
        stale = {result.check.id: ["HEAD changed during gate execution"] for result in results}
    passed = not stale and all(result.verdict == "pass" for result in results)
    receipt.update(passed=passed, stale=stale, results=[_result_data(result) for result in results])
    out.emit(receipt)
    _print_summary(results, out)
    for check_id, reasons in stale.items():
        out.detail(f"No fresh passing evidence for {check_id}.", indent=2)
        for reason in reasons:
            out.detail(reason, indent=4)
    out.line(out.style("Gate passed" if passed else "Gate failed", "green" if passed else "red"))
    return OK if passed else GATE_FAILED


def cmd_check(args, out: Output) -> int:
    if args.focused and not args.changed:
        raise ConfigError("--focused requires --changed")
    cfg = load_config(args)
    base: str | None = resolve_base(cfg.root, args.base) if args.changed or args.base else None
    changed = git.changed_files(cfg.root, base) if args.changed and base else None
    uncovered = plan.uncovered_paths(cfg.checks, {path for path in changed or set() if not git.is_rung_output(path)})
    selected = runner.select(cfg, args.ids, changed)
    omitted: list[str] = []
    selection = "standard"
    if args.focused:
        focused = plan.focused_selection(selected, changed or set())
        if focused:
            selection = "focused"
            omitted = [check.id for check in selected if check not in focused]
            selected = focused
        else:
            # Nothing declares this change as its target: run the full selection rather than nothing.
            selection = "full-fallback"
    scenarios = tuple(args.scenario or ())
    partial = args.seed is not None or bool(scenarios)
    if partial and (len(args.ids) != 1 or args.changed or args.ci):
        raise ConfigError("--seed and --scenario reproduce one check: name exactly one, without --changed or --ci")
    if args.ci:
        # Reuse only a local fresh verdict made against the same base and environment.
        view = ledger.View(cfg.root)
        selected = [
            check
            for check in selected
            if (changed is not None and runner.touched_by(check, changed))
            or not ledger.same_environment(
                cfg.root, runner.status(cfg, check, base=base or "", view=view), runner.tool_identity(cfg.root, check)
            )
        ]
    if not cfg.checks:
        out.error(f"no checks declared in {CONFIG_NAME}; see `rung plan` or `rung add`", code=GATE_FAILED)
        return GATE_FAILED
    scope = f"base {base[:12]}" if base else "working tree"
    if selection == "focused":
        scope += " · focused"
    out.heading("Verification checks", f"{cfg.name} · {scope} · {len(selected)} selected")
    if selection == "full-fallback":
        out.detail("No declared verification target matches this change; running every check it touches.", indent=2)
    if omitted:
        out.detail(f"{len(omitted)} checks outside the focused selection. Run rung check --changed for the full scope.",
                   indent=2)  # fmt: skip
    if uncovered:
        out.heading("Coverage gaps", f"No command, runtime or formal check scope for {len(uncovered)} path(s).")
        for path in uncovered[:8]:
            out.detail(path, indent=2)
        if len(uncovered) > 8:
            out.detail("Use --json for the full list.", indent=2)
    results = _run_checks(cfg, selected, out, base=base, seed=args.seed, scenarios=scenarios)
    out.emit(
        {
            "schema_version": 1,
            "base": base,
            "head": git.head(cfg.root) or None,
            "recorded": bool(results) and not partial,
            "selected": [check.id for check in selected],
            "selection": selection,
            "omitted": omitted,
            "uncovered_paths": uncovered,
            "results": [_result_data(result) for result in results],
        }
    )
    if not results:
        out.line("Nothing to run: every selected check is fresh or untouched by this change.")
    _print_summary(results, out)
    if partial:
        out.line(f"Reproduction run: not recorded in the ledger. `rung check {args.ids[0]}` records a verdict.")
    return GATE_FAILED if any(r.verdict != "pass" for r in results) else OK


def cmd_status(args, out: Output) -> int:
    cfg = load_config(args)
    rows = runner.statuses(cfg)
    problems = ledger.problems(cfg.root)
    fresh = sum(s.trusted for s in rows)
    out.heading("Verification status", f"{cfg.name} · {fresh}/{len(rows)} fresh")
    out.emit({
        "schema_version": 1,
        "checks": [{"id": s.check_id, "state": s.state, "at": (s.entry or {}).get("at"), "changed": s.changed,
                    **({"implementation_link": s.implementation_link} if s.implementation_link else {})}
                   for s in rows],
        "ledger_problems": problems,
    })  # fmt: skip
    for s in rows:
        when = (s.entry or {}).get("at", "")
        out.line(f"{out.badge(s.state, STATE_STYLE, 10)} {s.check_id:28} {out.style(when, 'dim')}")
        if s.implementation_link == "model-only":
            out.detail("Model-only evidence", indent=11)
        for path in s.changed:
            out.detail(f"changed: {path}", indent=11)
    for p in problems:
        out.line(out.style(f"CORRUPT    {p}", "red"))
    if args.ci:
        return OK if rows and all(s.trusted for s in rows) and not problems else GATE_FAILED
    return OK


def cmd_report(args, out: Output) -> int:
    cfg = load_config(args)
    data = report.build(cfg, evidence_url=args.evidence_url or "")
    if out.json_mode or args.format == "json":
        out.json_mode = True
        out.emit(data)
    else:
        out.document(report.markdown(data))
    return OK


def cmd_evidence(args, out: Output) -> int:
    """Write the HTML page for an evidence check's latest run: results, scenarios, screenshots, video, trace."""
    cfg = load_config(args)
    check = cfg.check(args.check)
    page = report.evidence_page(cfg, check, seed=args.seed, out=Path(args.out) if args.out else None)
    out.emit({"check": check.id, "page": str(page)})
    out.line(f"wrote {page}")
    out.detail("open it in a browser: results, scenarios, screenshots, video, and links to the trace and HAR", indent=2)
    return OK


def cmd_prove(args, out: Output) -> int:
    root = find_root(Path(args.dir))
    base = args.base or git.default_base(root)
    result = prove.prove(
        root, args.test_command, base, tests=args.tests or None, expect=args.expect, cwd=args.cwd, node_cases=args.case
    )

    def run_data(run: prove.Run | None) -> dict | None:
        return {"returncode": run.returncode, "tail": redact_lines(run.tail)} if run else None

    out.emit({
        "verdict": result.verdict, "summary": result.summary, "tests": result.tests,
        "base": run_data(result.base), "head": run_data(result.head),
        "scope": "case" if result.cases else "suite",
        "cases": [c.__dict__ for c in result.cases],
    })  # fmt: skip
    out.line(f"{out.badge(result.verdict, VERDICT_STYLE, 6)}{result.summary}")
    for t in result.tests:
        out.detail(f"test: {t}")
    for c in result.cases:
        label = "proven" if c.proven else "NOT PROVEN"
        out.detail(f"{label:10} {c.test}  (merge base: {c.base}, branch: {c.head})")
    if result.base and result.verdict != "pass":
        out.detail(f"merge base run (exit {result.base.returncode}):")
        for line in redact_lines(result.base.tail[-12:]):
            out.detail(line, indent=8)
    return OK if result.verdict == "pass" else GATE_FAILED


def cmd_ledger(args, out: Output) -> int:
    """Check every verdict file; with --prune, remove verdicts for checks no longer declared."""
    cfg = load_config(args)
    verdicts = ledger.read_all(cfg.root)
    problems = ledger.problems(cfg.root)
    declared = {c.id for c in cfg.checks}
    orphans = [k for k, v in verdicts.items() if k not in declared and v.get("kind") != "proof" and "invalid" not in v]
    if args.prune:
        for check_id in orphans:
            ledger.verdict_path(cfg.root, check_id).unlink(missing_ok=True)
    out.emit({"verdicts": len(verdicts), "problems": problems, "orphans": orphans, "pruned": bool(args.prune)})
    out.line(f"{len(verdicts)} verdict file(s); {len(problems)} corrupt")
    for p in problems:
        out.detail(p, indent=2)
    if orphans:
        verb = "removed" if args.prune else "for checks no longer in rung.toml (remove with --prune)"
        out.line(f"{len(orphans)} verdict(s) {verb}: {', '.join(orphans)}")
    return GATE_FAILED if problems else OK
