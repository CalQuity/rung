"""`rung draft`: model-drafted TLA+ specs, Lean models and runtime drive scripts."""

from __future__ import annotations

import re
import tomllib
from dataclasses import replace
from pathlib import Path

from rung import agents
from rung.analysis import survey
from rung.cli.commands.common import GATE_FAILED, OK, add_check, load_config
from rung.cli.commands.formal import accept_draft
from rung.cli.output import Output
from rung.core import laws
from rung.core.config import Check, Config, ConfigError
from rung.drafting import lean as lean_draft
from rung.drafting import property as property_draft
from rung.drafting import providers, runtime, sql_context
from rung.drafting import tla as tla_draft


def drafted_by(cfg: Config, provider: providers.Provider | None = None) -> str:
    """Which provider and model drafted an artifact, recorded on the check it creates.

    After a draft, `provider` reports the models that actually answered (Claude Code's own report, or
    Codex's configured default), so `default` is only left when the tool does not say.
    """
    model = provider.models() if provider else cfg.llm.model or "your CLI's default model"
    return f"{cfg.llm.provider}:{model}"


def _provider(cfg: Config, args) -> providers.Provider:
    """The configured provider, with `--model` applied to this draft only (rung.toml is not changed)."""
    return providers.get(replace(cfg.llm, model=args.model) if args.model else cfg.llm)


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
    provider = _provider(cfg, args)
    info = survey.survey(cfg.root)
    check_id = args.id or "runtime"
    out.line(
        f"Drafting a runtime verification script with {drafted_by(cfg, provider)}; Rung runs it until it proves itself."
    )
    result = runtime.draft_runtime(
        provider,
        cfg.root,
        info,
        focus=args.files,
        check_id=check_id,
        intent=args.intent or "",
        require=tuple(args.require or ()),
    )
    out.emit({"ok": result.ok, "message": result.message, "rounds": result.rounds, "covers": result.covers,
              "files": [str(f.relative_to(cfg.root)) for f in result.files], "evidence": result.evidence})  # fmt: skip
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    out.detail(f"drafted by {drafted_by(cfg, provider)}", indent=2)
    if result.notes:
        out.line("Notes from the model:")
        out.detail(result.notes, indent=2)
    for f in result.files:
        out.detail(f"wrote {f.relative_to(cfg.root)}", indent=2)
    for e in result.evidence:
        out.detail(f"evidence {e}", indent=2)
    if not result.ok:
        out.line("The last script is kept for you to finish by hand; it is not registered as a check.")
        return GATE_FAILED
    add_check(
        cfg,
        Check(
            check_id,
            "evidence",
            result.covers,
            {
                "run": result.run,
                "timeout": 900,
                "require": list(args.require or []),
                "drafted_by": drafted_by(cfg, provider),
            },
        ),
        out,
    )
    skill = agents.install_verify_skill(cfg.root, cfg.name, check_id)
    out.line(f"wrote {skill.relative_to(cfg.root)} so agents verify user-facing changes with `rung check {check_id}`.")
    return OK


def _draft_property(args, cfg: Config, out: Output, sources: list[Path], rel: list[str]) -> int:
    root = cfg.root
    check_id = args.id or f"{_slug(rel)}-properties"
    provider = _provider(cfg, args)
    out.line(
        f"Drafting property tests for {', '.join(rel)} with {drafted_by(cfg, provider)}; Rung runs them, then breaks "
        "the code on purpose to see whether they notice ..."
    )
    result, target = property_draft.draft(
        provider, root, sources, _slug(rel), intent=args.intent or "", out=args.out or ""
    )
    files = [f.relative_to(root).as_posix() for f in result.files]
    out.emit({"ok": result.ok, "message": result.message, "rounds": result.rounds, "trace": result.trace,
              "files": files, "warnings": result.warnings, "run": target.command})  # fmt: skip
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    out.detail(f"drafted by {drafted_by(cfg, provider)}", indent=2)
    if result.notes:
        out.line("Notes from the model:")
        out.detail(result.notes, indent=2)
    for f in files:
        out.detail(f"wrote {f}", indent=2)
    for w in result.warnings:
        out.detail(w, indent=2)
    if result.trace:
        out.line("\nFailing example:")
        out.detail(result.trace, indent=2)
        out.line(
            "\nIf this is a real bug, fix the code and run `rung check`. If the law is wrong, edit the test yourself."
        )
        return GATE_FAILED
    if not result.ok:
        return GATE_FAILED
    # The verdict depends on the code under test, whatever it imports, and the runner's configuration:
    # cover the whole project directory, and the test file itself.
    covers = ["**/*"] if target.project == "." else [f"{target.project}/**"]
    options = {"run": target.command, "drafted_by": drafted_by(cfg, provider), "specs": [target.test_file]}
    if target.project != ".":
        options["cwd"] = target.project
    add_check(cfg, Check(check_id, "command", covers, options), out)
    if added := laws.propose(root, result.laws, [check_id]):
        out.line(f"Proposed {len(added)} law(s) in {laws.LAWS_FILE}: {', '.join(added)}.")
        out.line("Read each one; approve with `rung laws --approve <id>` only if it says what you mean.")
    return OK


def cmd_draft(args, out: Output) -> int:
    cfg = load_config(args)
    if args.kind == "runtime":
        return _draft_runtime(args, cfg, out)
    if not args.files:
        raise ConfigError(f"`rung draft {args.kind}` needs the source files to model")
    provider = _provider(cfg, args)
    root = cfg.root
    sources = [(root / p).resolve() for p in args.files]
    missing = [str(s) for s in sources if not s.is_file()]
    if missing:
        raise ConfigError(f"not found: {', '.join(missing)}")
    rel = [s.relative_to(root).as_posix() for s in sources]
    if args.kind == "property":
        return _draft_property(args, cfg, out, sources, rel)
    check_id = args.id or f"{_slug(rel)}-{args.kind}"
    target = root / (args.out or f"spec/{args.kind}/{_slug(rel)}")
    target.mkdir(parents=True, exist_ok=True)
    sql = sql_context.gather(root, sources)
    if sql.files:
        out.line(f"Added the SQL that defines {', '.join(sql.defined)} to the drafting context:")
        for f in sql.files:
            out.detail(f"added {f}", indent=2)
    for w in sql.warnings:
        out.line(out.style(f"WARNING {w}", "yellow"))
    out.line(f"Drafting {args.kind} for {', '.join(rel)} with {drafted_by(cfg, provider)} ...")

    drafter = tla_draft.draft if args.kind == "tla" else lean_draft.draft
    result = drafter(provider, root, sources, target, intent=args.intent or "", sql=sql)
    out.emit({"ok": result.ok, "message": result.message, "rounds": result.rounds, "trace": result.trace,
              "files": [str(f.relative_to(root)) for f in result.files], "context": result.context,
              "warnings": result.warnings})  # fmt: skip
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    out.detail(f"drafted by {drafted_by(cfg, provider)}", indent=2)
    if result.notes:
        out.line("Notes from the model:")
        out.detail(result.notes, indent=2)
    for f in result.files:
        out.detail(f"wrote {f.relative_to(root)}", indent=2)
    for w in result.warnings:
        if w not in sql.warnings:  # those were shown before drafting
            out.detail(w, indent=2)
    if sql.missing and result.ok:
        out.detail(f"this result assumes each call to {', '.join(sql.missing)} is atomic", indent=2)
    if result.trace and args.kind != "tla":  # a TLA+ counterexample is shown by the check below, in plain words
        out.line("\nCounterexample:")
        out.detail(result.trace, indent=2)
        out.line("\nIf this is a real bug, fix the code and run `rung check`. If the spec is wrong, edit it yourself.")
    if not result.ok:
        return GATE_FAILED

    covered = rel + [f for f in result.context if f not in rel]  # a changed SQL definition stales the verdict too
    if args.kind == "tla":
        spec = next(f for f in result.files if f.suffix == ".tla")
        cfg_file = next(f for f in result.files if f.suffix == ".cfg")
        options = {"spec": spec.relative_to(root).as_posix(), "config": cfg_file.relative_to(root).as_posix(),
                   "drafted_by": drafted_by(cfg, provider)}  # fmt: skip
        check = Check(check_id, "tla", covered, options)
        add_check(cfg, check, out)
    else:
        project = target.relative_to(root).as_posix()
        check = Check(
            check_id,
            "lean",
            [],
            {"project": project, "theorems": result.theorems, "drafted_by": drafted_by(cfg, provider)},
        )
        add_check(cfg, check, out)
        exe = _lean_exe(target)
        if exe and result.alphabet and (target / "drt_adapter.py").exists():
            options = {
                "model": f"lake exe {exe}", "model_cwd": project,
                "impl": f"python3 {project}/drt_adapter.py", "alphabet": result.alphabet,
                "cases": 2000, "max_len": 8, "specs": [f"{project}/**/*.lean", f"{project}/drt_adapter.py"],
            }  # fmt: skip
            add_check(cfg, Check(f"{check_id}-drt", "drt", covered, options), out)
    enforcing = [check_id] if args.kind == "tla" else [check_id, f"{check_id}-drt"]
    added = laws.propose(root, result.laws, enforcing)
    if added:
        out.line(f"Proposed {len(added)} law(s) in {laws.LAWS_FILE}: {', '.join(added)}.")
        out.line("Read each one; approve with `rung laws --approve <id>` only if it says what you mean.")
    return accept_draft(cfg, check, result, out, sources=covered, by=f"rung draft ({drafted_by(cfg, provider)})")
