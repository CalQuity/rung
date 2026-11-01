"""Commands that set a project up: init, plan, doctor, agents, hook."""

from __future__ import annotations

import shutil
from pathlib import Path

from rung import agents
from rung.analysis import plan, survey
from rung.checks.presets import PRESETS
from rung.cli.commands.common import OK, ask
from rung.cli.output import Output
from rung.core import laws
from rung.core.config import CONFIG_NAME, Check, Config, ConfigError, LLMConfig, find_root, load, save
from rung.core.errors import ToolMissing
from rung.drafting import providers
from rung.engines import lean, tla


def _choose_provider(requested: str | None) -> LLMConfig:
    if requested:
        return LLMConfig(provider=requested)
    options = providers.detect()
    print("\nWhich model should draft specs, proofs and explanations?")
    for i, name in enumerate(options, 1):
        print(f"  {i}. {name:18} {providers.PROVIDERS[name]}")
    picked = ask("Choose", "1")
    name = options[int(picked) - 1] if picked.isdigit() and 0 < int(picked) <= len(options) else picked
    if name not in providers.PROVIDERS:
        raise ConfigError(f"unknown provider {name!r}")
    llm = LLMConfig(provider=name)
    if name == "openai-compatible":
        llm.base_url = ask("Base URL", "http://localhost:11434/v1")
        llm.model = ask("Model name", "qwen2.5-coder")
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
    if needs_login and ask("Not logged in. Log in now? (y/n)", "y").lower().startswith("y"):
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
    written = agents.install(root, hooks=args.hooks, lean_version=lean.toolchain().split(":")[-1])
    out.emit({"written": [str(p.relative_to(root)) for p in written]})
    for path in written:
        out.line(f"wrote {path.relative_to(root)}")
    if args.hooks:
        out.line("Claude Code will now run `rung hook stop` before an agent finishes.")
    return OK


def cmd_hook(args, out: Output) -> int:
    return agents.main_stop_hook()
