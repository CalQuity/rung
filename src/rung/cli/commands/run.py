"""Commands that run and report checks: check, status, report, prove, ledger."""

from __future__ import annotations

from pathlib import Path

from rung.analysis import report
from rung.checks import prove, runner
from rung.cli.commands.common import GATE_FAILED, OK, load_config, resolve_base
from rung.cli.output import STATE_STYLE, VERDICT_STYLE, Output
from rung.core import git, ledger
from rung.core.config import CONFIG_NAME, find_root


def _print_result(result: runner.Result, out: Output) -> None:
    o = result.outcome
    out.line(f"{out.badge(o.verdict, VERDICT_STYLE, 6)}{result.check.id} ({result.check.kind}): {o.summary}")
    for line in o.lines:
        out.detail(line)


def cmd_check(args, out: Output) -> int:
    cfg = load_config(args)
    base = None
    changed = None
    if args.changed or args.base:
        base = resolve_base(cfg.root, args.base)
    if args.changed:
        changed = git.changed_files(cfg.root, base)
    selected = runner.select(cfg, args.ids, changed)
    if args.ci:
        # Reuse a verdict only if it is fresh and was made against the same comparison base.
        view = ledger.View(cfg.root)
        selected = [c for c in selected if not runner.status(cfg, c, base=base or "", view=view).trusted]
    if not cfg.checks:
        out.error(f"no checks declared in {CONFIG_NAME}; see `rung plan` or `rung add`")
        return GATE_FAILED
    results = [runner.run_check(cfg, c, base=base) for c in selected]
    out.emit({
        "base": base,
        "results": [{"id": r.check.id, "kind": r.check.kind, "verdict": r.verdict, "summary": r.outcome.summary,
                     "lines": r.outcome.lines, "findings": r.outcome.findings} for r in results],
    })  # fmt: skip
    if not results:
        out.line("Nothing to run: every selected check is fresh or untouched by this change.")
    for r in results:
        _print_result(r, out)
    return GATE_FAILED if any(r.verdict != "pass" for r in results) else OK


def cmd_status(args, out: Output) -> int:
    cfg = load_config(args)
    rows = runner.statuses(cfg)
    problems = ledger.verify_chain(ledger.read(cfg.root))
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
        out.line(out.style(f"LEDGER     {p}", "red"))
    if args.ci:
        return OK if rows and all(s.trusted for s in rows) and not problems else GATE_FAILED
    return OK


def cmd_report(args, out: Output) -> int:
    cfg = load_config(args)
    data = report.build(cfg)
    if out.json_mode or args.format == "json":
        out.json_mode = True
        out.emit(data)
    else:
        print(report.markdown(data))
    return OK


def cmd_prove(args, out: Output) -> int:
    root = find_root(Path(args.dir))
    base = args.base or git.default_base(root)
    result = prove.prove(root, args.test_command, base, tests=args.tests or None, expect=args.expect, cwd=args.cwd)
    out.emit({
        "verdict": result.verdict, "summary": result.summary, "tests": result.tests,
        "base": result.base.__dict__ if result.base else None, "head": result.head.__dict__ if result.head else None,
    })  # fmt: skip
    out.line(f"{out.badge(result.verdict, VERDICT_STYLE, 6)}{result.summary}")
    for t in result.tests:
        out.detail(f"test: {t}")
    if result.base and result.verdict != "pass":
        out.detail(f"merge base run (exit {result.base.returncode}):")
        for line in result.base.tail[-12:]:
            out.detail(line, indent=8)
    return OK if result.verdict == "pass" else GATE_FAILED


def cmd_ledger(args, out: Output) -> int:
    cfg = load_config(args)
    entries = ledger.read(cfg.root)
    problems = ledger.verify_chain(entries)
    out.emit({"entries": len(entries), "intact": not problems, "problems": problems})
    out.line(f"{len(entries)} entries; chain {'intact' if not problems else out.style('BROKEN', 'red')}")
    for p in problems:
        out.detail(p, indent=2)
    return GATE_FAILED if problems else OK
