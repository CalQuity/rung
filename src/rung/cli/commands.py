"""Command implementations. Each takes parsed args and an Output, and returns an exit code."""

from __future__ import annotations

import re
import shutil
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from rung import (
    agents,
    checkers,
    checks,
    draft,
    findings,
    gitutil,
    laws,
    ledger,
    plan,
    prove,
    providers,
    report,
    runtime,
    survey,
)
from rung.checkers.scan import ScanChecker
from rung.cli.output import STATE_STYLE, VERDICT_STYLE, Output
from rung.config import CONFIG_NAME, Check, Config, ConfigError, LLMConfig, find_root, load, save
from rung.engines import lean, tla
from rung.errors import RungError, ToolMissing
from rung.presets import PRESETS

OK, GATE_FAILED = 0, 1


def _config(args) -> Config:
    return load(find_root(Path(args.dir)))


def _ask(question: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    return input(f"{question}{suffix}: ").strip() or default


def _resolve_base(root: Path, base: str | None) -> str:
    """A merge base commit for `base` (or the default branch)."""
    return gitutil.merge_base(root, base or gitutil.default_base(root))


def _add_check(config: Config, check: Check, out: Output) -> None:
    checkers.get(check.kind)  # validate the kind before writing anything
    replaced = any(c.id == check.id for c in config.checks)
    config.checks = [c for c in config.checks if c.id != check.id] + [check]
    save(config)
    out.line(f"{'Updated' if replaced else 'Added'} check {check.id!r} ({check.kind}) in {CONFIG_NAME}.")


# --- setup --------------------------------------------------------------------------------------


def _choose_provider(requested: str | None) -> LLMConfig:
    if requested:
        return LLMConfig(provider=requested)
    options = providers.detect()
    print("\nWhich model should draft specs, proofs and explanations?")
    for i, name in enumerate(options, 1):
        print(f"  {i}. {name:18} {providers.PROVIDERS[name]}")
    picked = _ask("Choose", "1")
    name = options[int(picked) - 1] if picked.isdigit() and 0 < int(picked) <= len(options) else picked
    if name not in providers.PROVIDERS:
        raise ConfigError(f"unknown provider {name!r}")
    llm = LLMConfig(provider=name)
    if name == "openai-compatible":
        llm.base_url = _ask("Base URL", "http://localhost:11434/v1")
        llm.model = _ask("Model name", "qwen2.5-coder")
    return llm


def cmd_init(args, out: Output) -> int:
    root = Path(args.dir).resolve()
    existing = (root / CONFIG_NAME).exists()
    cfg = load(root) if existing else Config(root=root, name=root.name, llm=LLMConfig(), checks=[])
    info = survey.survey(root)

    if not existing or args.provider:
        cfg.llm = _choose_provider(args.provider)
    provider = providers.get(cfg.llm)
    state = provider.login_state()
    needs_login = not state.ok and cfg.llm.provider in ("claude-cli", "codex-cli")
    if needs_login and _ask("Not logged in. Log in now? (y/n)", "y").lower().startswith("y"):
        provider.login()
        state = provider.login_state()

    added = []
    if not existing:
        # Wire up the security scanners that are installed and relevant to this repo.
        for check_id, tool in (
            ("secrets", "gitleaks"),
            ("vulnerable-deps", "osv-scanner"),
            ("workflows", "zizmor"),
        ):
            relevant = tool != "zizmor" or (root / ".github" / "workflows").is_dir()
            if shutil.which(tool) and relevant:
                cfg.checks.append(Check(check_id, "scan", [], {"tool": tool}))
                added.append(check_id)
        cfg.checks.append(Check("deps", "deps", [], {"min_age_days": 30}))
        added.append("deps")

    save(cfg)
    (root / ".rung").mkdir(exist_ok=True)
    if not (root / laws.LAWS_FILE).exists():
        (root / laws.LAWS_FILE).write_text(laws.TEMPLATE)
    recs = plan.plan(info, {c.id for c in cfg.checks})
    out.emit({"root": str(root), "provider": cfg.llm.provider, "login": state.detail,
              "added_checks": added, "survey": info.to_dict(), "plan": [r.__dict__ for r in recs]})  # fmt: skip
    langs = ", ".join(f"{k} ({v})" for k, v in list(info.languages.items())[:4]) or "none found"
    out.line(f"Surveyed {root}")
    out.line(f"  languages: {langs}")
    out.line(f"  surfaces:  {', '.join(info.surfaces) or 'none found'}")
    out.line(f"  tests:     {', '.join(info.test_commands) or 'none found'}")
    out.line(f"Model provider: {cfg.llm.provider} ({state.detail})")
    if added:
        out.line(f"Added checks: {', '.join(added)}")
    out.line(f"Wrote {CONFIG_NAME}, {laws.LAWS_FILE} and .rung/.")
    _print_plan(recs, out)
    out.line("\nNext: `rung agents --hooks`, then `rung check`.")
    return OK


def _print_plan(recs: list[plan.Recommendation], out: Output) -> None:
    out.line("\nRecommended next steps:")
    for i, rec in enumerate(recs, 1):
        out.line(f"\n {i}. {out.style(rec.title, 'bold')}  [{rec.rung}, effort {rec.effort}]")
        out.detail(rec.why, indent=4)
        if rec.command:
            out.line(f"    {out.style('$ ' + rec.command, 'blue')}")


def cmd_plan(args, out: Output) -> int:
    root = Path(args.dir).resolve()
    try:
        existing = {c.id for c in load(find_root(root)).checks}
    except ConfigError:
        existing = set()
    info = survey.survey(root)
    recs = plan.plan(info, existing)
    out.emit({"survey": info.to_dict(), "plan": [r.__dict__ for r in recs]})
    _print_plan(recs, out)
    return OK


def cmd_doctor(args, out: Output) -> int:
    rows: list[tuple[str, bool, str]] = []
    for label, probe in (
        ("java (TLC)", tla.find_java),
        ("tla2tools.jar", tla.find_jar),
        ("lake (Lean)", lean.find_lake),
    ):
        try:
            rows.append((label, True, str(probe())))
        except ToolMissing as exc:
            rows.append((label, False, str(exc)))
    for name, preset in PRESETS.items():
        found = shutil.which(name)
        note = "" if preset.verified else " (preset not yet verified against real output)"
        rows.append((name, bool(found), (found or f"not installed: {preset.install}") + note))
    for name in ("claude-cli", "codex-cli"):
        state = providers.get(LLMConfig(provider=name)).login_state()
        rows.append((name, state.ok, state.detail))
    out.emit([{"tool": label, "ok": ok, "detail": detail} for label, ok, detail in rows])
    for label, ok, detail in rows:
        mark = out.style("ok ", "green") if ok else out.style("-- ", "dim")
        out.line(f"{mark} {label:14} {detail}")
    return OK


def cmd_agents(args, out: Output) -> int:
    root = find_root(Path(args.dir))
    written = agents.install(root, hooks=args.hooks)
    out.emit({"written": [str(p.relative_to(root)) for p in written]})
    for path in written:
        out.line(f"wrote {path.relative_to(root)}")
    if args.hooks:
        out.line("Claude Code will now run `rung hook stop` before an agent finishes.")
    return OK


def cmd_hook(args, out: Output) -> int:
    return agents.main_stop_hook()


# --- running ------------------------------------------------------------------------------------


def _print_result(result: checks.Result, out: Output) -> None:
    o = result.outcome
    out.line(f"{out.badge(o.verdict, VERDICT_STYLE, 6)}{result.check.id} ({result.check.kind}): {o.summary}")
    for line in o.lines:
        out.detail(line)


def cmd_check(args, out: Output) -> int:
    cfg = _config(args)
    base = None
    changed = None
    if args.changed or args.base:
        base = _resolve_base(cfg.root, args.base)
    if args.changed:
        changed = gitutil.changed_files(cfg.root, base)
    selected = checks.select(cfg, args.ids, changed)
    if args.ci:
        selected = [c for c in selected if not checks.status(cfg, c).trusted]
    if not cfg.checks:
        out.error(f"no checks declared in {CONFIG_NAME}; see `rung plan` or `rung add`")
        return GATE_FAILED
    results = [checks.run_check(cfg, c, base=base) for c in selected]
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
    cfg = _config(args)
    rows = checks.statuses(cfg)
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
    cfg = _config(args)
    data = report.build(cfg)
    if out.json_mode or args.format == "json":
        out.json_mode = True
        out.emit(data)
    else:
        print(report.markdown(data))
    return OK


def cmd_prove(args, out: Output) -> int:
    root = find_root(Path(args.dir))
    base = args.base or gitutil.default_base(root)
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
    cfg = _config(args)
    entries = ledger.read(cfg.root)
    problems = ledger.verify_chain(entries)
    out.emit({"entries": len(entries), "intact": not problems, "problems": problems})
    out.line(f"{len(entries)} entries; chain {'intact' if not problems else out.style('BROKEN', 'red')}")
    for p in problems:
        out.detail(p, indent=2)
    return GATE_FAILED if problems else OK


# --- managing checks, laws, baselines and waivers ------------------------------------------------


def cmd_laws(args, out: Output) -> int:
    cfg = _config(args)
    if args.approve:
        laws.set_status(cfg.root, args.approve, "approved")
        out.line(f"Approved {args.approve}.")
    statuses = {s.check_id: s for s in checks.statuses(cfg)}
    rows = [(law, *laws.state(law, statuses)) for law in laws.load(cfg.root)]
    out.emit([{"id": law.id, "text": law.text, "state": st, "why": why, "enforced_by": law.enforced_by}
              for law, st, why in rows])  # fmt: skip
    if not rows:
        out.line(f"No laws in {laws.LAWS_FILE} yet.")
    for law, st, why in rows:
        out.line(f"{out.badge(st, STATE_STYLE, 11)}{law.id}")
        out.detail(law.text, indent=11)
        out.detail(out.style(why, "dim"), indent=11)
    return OK


def cmd_add(args, out: Output) -> int:
    cfg = _config(args)
    options: dict[str, object] = {}
    if args.kind in ("command", "evidence"):
        if not args.run:
            raise ConfigError(f"`rung add {args.kind}` needs --run")
        options["run"] = args.run
        if args.cwd:
            options["cwd"] = args.cwd
        if args.require:
            options["require"] = args.require
    elif args.kind == "scan":
        if not (args.tool or args.run):
            raise ConfigError(f"`rung add scan` needs --tool (one of {', '.join(PRESETS)}) or --run")
        if args.tool:
            options["tool"] = args.tool
        if args.run:
            options["run"] = args.run
        if args.cwd:
            options["cwd"] = args.cwd
        if args.fail_on:
            options["fail_on"] = args.fail_on
    elif args.kind == "deps":
        options["min_age_days"] = args.min_age_days
    _add_check(cfg, Check(args.id, args.kind, args.covers or [], options), out)
    return OK


def cmd_baseline(args, out: Output) -> int:
    cfg = _config(args)
    check = cfg.check(args.check)
    if check.kind != "scan":
        raise ConfigError("baselines apply to `scan` checks")
    found = ScanChecker().collect(cfg.root, check, int(check.get("timeout", 900)))
    path = findings.save_baseline(cfg.root, check.id, found, gitutil.head(cfg.root))
    out.emit({"check": check.id, "findings": len(found), "path": str(path.relative_to(cfg.root))})
    out.line(f"Recorded {len(found)} existing finding(s) for {check.id!r} as accepted ({path.relative_to(cfg.root)}).")
    out.line("Commit this file; from now on only findings beyond it will block.")
    return OK


def cmd_waive(args, out: Output) -> int:
    cfg = _config(args)
    check = cfg.check(args.check)
    if not re.fullmatch(r"[0-9a-f]{16}", args.fingerprint):
        raise ConfigError("a fingerprint is the 16-character hex id shown in brackets by `rung check`")
    expires = date.fromisoformat(args.until) if args.until else date.today() + timedelta(days=args.days)
    approver = args.approved_by or gitutil.user_name(cfg.root)
    if not approver:
        raise ConfigError("cannot tell who approves this waiver; pass --approved-by")
    w = findings.Waiver(check.id, args.fingerprint, args.reason, approver, expires)
    path = findings.add_waiver(cfg.root, w)
    out.emit({"check": w.check, "fingerprint": w.fingerprint, "expires": str(w.expires), "approved_by": approver})
    out.line(f"Waived {w.fingerprint} on {w.check!r} until {w.expires} ({path.relative_to(cfg.root)}).")
    return OK


def _slug(paths: list[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(paths[0]).stem.lower()).strip("-") or "spec"


def _lean_exe(project: Path) -> str:
    try:
        lakefile = tomllib.loads((project / "lakefile.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    exes = lakefile.get("lean_exe", [])
    return exes[0].get("name", "") if exes else ""


def _draft_runtime(args, cfg: Config, out: Output) -> int:
    provider = providers.get(cfg.llm)
    info = survey.survey(cfg.root)
    check_id = args.id or "runtime"
    out.line(f"Drafting a runtime verification script with {cfg.llm.provider}; Rung runs it until it proves itself.")
    result = runtime.draft_runtime(
        provider, cfg.root, info, focus=args.files, check_id=check_id, intent=args.intent or ""
    )
    out.emit({"ok": result.ok, "message": result.message, "rounds": result.rounds, "covers": result.covers,
              "files": [str(f.relative_to(cfg.root)) for f in result.files], "evidence": result.evidence})  # fmt: skip
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    for f in result.files:
        out.detail(f"wrote {f.relative_to(cfg.root)}", indent=2)
    for e in result.evidence:
        out.detail(f"evidence {e}", indent=2)
    if not result.ok:
        out.line("The last script is kept for you to finish by hand; it is not registered as a check.")
        return GATE_FAILED
    run = f"python3 {(runtime.VERIFY_DIR / 'drive.py').as_posix()}"
    _add_check(cfg, Check(check_id, "evidence", result.covers, {"run": run, "timeout": 600}), out)
    skill = agents.install_verify_skill(cfg.root, cfg.name, check_id)
    out.line(f"wrote {skill.relative_to(cfg.root)} so agents verify user-facing changes with `rung check {check_id}`.")
    return OK


def cmd_draft(args, out: Output) -> int:
    cfg = _config(args)
    if args.kind == "runtime":
        return _draft_runtime(args, cfg, out)
    if not args.files:
        raise ConfigError(f"`rung draft {args.kind}` needs the source files to model")
    provider = providers.get(cfg.llm)
    root = cfg.root
    sources = [(root / p).resolve() for p in args.files]
    missing = [str(s) for s in sources if not s.is_file()]
    if missing:
        raise ConfigError(f"not found: {', '.join(missing)}")
    rel = [s.relative_to(root).as_posix() for s in sources]
    check_id = args.id or f"{_slug(rel)}-{args.kind}"
    target = root / (args.out or f"spec/{args.kind}/{_slug(rel)}")
    target.mkdir(parents=True, exist_ok=True)
    out.line(f"Drafting {args.kind} for {', '.join(rel)} with {cfg.llm.provider} ...")

    drafter = draft.draft_tla if args.kind == "tla" else draft.draft_lean
    result = drafter(provider, root, sources, target, intent=args.intent or "")
    out.emit({"ok": result.ok, "message": result.message, "rounds": result.rounds, "trace": result.trace,
              "files": [str(f.relative_to(root)) for f in result.files]})  # fmt: skip
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    for f in result.files:
        out.detail(f"wrote {f.relative_to(root)}", indent=2)
    if result.trace:
        out.line("\nCounterexample:")
        out.detail(result.trace, indent=2)
        out.line("\nIf this is a real bug, fix the code and run `rung check`. If the spec is wrong, edit it yourself.")
    if not result.ok:
        return GATE_FAILED

    if args.kind == "tla":
        spec = next(f for f in result.files if f.suffix == ".tla")
        cfg_file = next(f for f in result.files if f.suffix == ".cfg")
        options = {"spec": spec.relative_to(root).as_posix(), "config": cfg_file.relative_to(root).as_posix()}
        _add_check(cfg, Check(check_id, "tla", rel, options), out)
    else:
        project = target.relative_to(root).as_posix()
        _add_check(cfg, Check(check_id, "lean", [], {"project": project, "theorems": result.theorems}), out)
        exe = _lean_exe(target)
        if exe and result.alphabet and (target / "drt_adapter.py").exists():
            options = {
                "model": f"lake exe {exe}", "model_cwd": project,
                "impl": f"python3 {project}/drt_adapter.py", "alphabet": result.alphabet,
                "cases": 2000, "max_len": 8, "specs": [f"{project}/**/*.lean", f"{project}/drt_adapter.py"],
            }  # fmt: skip
            _add_check(cfg, Check(f"{check_id}-drt", "drt", rel, options), out)
    out.line("Propose the guarantees as laws in LAWS.md (status: proposed); approve them after reading.")
    return OK


__all__ = ["GATE_FAILED", "OK", "RungError"]
