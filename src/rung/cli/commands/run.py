"""Commands that run and report checks: check, status, report, prove, ledger."""

from __future__ import annotations

from pathlib import Path

from rung.analysis import report
from rung.checks import prove, runner
from rung.cli.commands.common import GATE_FAILED, OK, load_config, resolve_base
from rung.cli.output import STATE_STYLE, VERDICT_STYLE, Output
from rung.core import git, ledger
from rung.core.config import CONFIG_NAME, ConfigError, find_root


def _print_result(result: runner.Result, out: Output) -> None:
    o = result.outcome
    out.line(f"{out.badge(o.verdict, VERDICT_STYLE, 6)}{result.check.id} ({result.check.kind}): {o.summary}")
    for line in o.lines:
        out.detail(line)


def cmd_check(args, out: Output) -> int:
    cfg = load_config(args)
    base: str | None = resolve_base(cfg.root, args.base) if args.changed or args.base else None
    changed = git.changed_files(cfg.root, base) if args.changed and base else None
    selected = runner.select(cfg, args.ids, changed)
    scenarios = tuple(args.scenario or ())
    partial = args.seed is not None or bool(scenarios)
    if partial and (len(args.ids) != 1 or args.changed or args.ci):
        raise ConfigError("--seed and --scenario reproduce one check: name exactly one, without --changed or --ci")
    if args.ci:
        # Reuse a verdict only if it is fresh and was made against the same comparison base.
        view = ledger.View(cfg.root)
        selected = [c for c in selected if not runner.status(cfg, c, base=base or "", view=view).trusted]
    if not cfg.checks:
        out.error(f"no checks declared in {CONFIG_NAME}; see `rung plan` or `rung add`")
        return GATE_FAILED
    results = [runner.run_check(cfg, c, base=base, seed=args.seed, scenarios=scenarios) for c in selected]
    out.emit({
        "base": base,
        "recorded": not partial,
        "results": [{"id": r.check.id, "kind": r.check.kind, "verdict": r.verdict, "summary": r.outcome.summary,
                     "lines": r.outcome.lines, "findings": r.outcome.findings, **r.outcome.details}
                    for r in results],
    })  # fmt: skip
    if not results:
        out.line("Nothing to run: every selected check is fresh or untouched by this change.")
    for r in results:
        _print_result(r, out)
    if partial:
        out.line(f"Reproduction run: not recorded in the ledger. `rung check {args.ids[0]}` records a verdict.")
    return GATE_FAILED if any(r.verdict != "pass" for r in results) else OK


def cmd_status(args, out: Output) -> int:
    cfg = load_config(args)
    rows = runner.statuses(cfg)
    problems = ledger.problems(cfg.root)
    out.emit({
        "checks": [{"id": s.check_id, "state": s.state, "at": (s.entry or {}).get("at"), "changed": s.changed}
                   for s in rows],
        "ledger_problems": problems,
    })  # fmt: skip
    for s in rows:
        when = (s.entry or {}).get("at", "")
        out.line(f"{out.badge(s.state, STATE_STYLE, 10)} {s.check_id:28} {out.style(when, 'dim')}")
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
        print(report.markdown(data))
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
    result = prove.prove(root, args.test_command, base, tests=args.tests or None, expect=args.expect, cwd=args.cwd)
    out.emit({
        "verdict": result.verdict, "summary": result.summary, "tests": result.tests,
        "base": result.base.__dict__ if result.base else None, "head": result.head.__dict__ if result.head else None,
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
        for line in result.base.tail[-12:]:
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
