"""Commands that set a project up: init, kit, plan, doctor, agents, hook."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rung import agents
from rung.analysis import kit, plan, survey
from rung.checks.presets import PRESETS
from rung.cli.commands.common import OK, ask
from rung.cli.output import STATE_STYLE, Output
from rung.core import laws
from rung.core.config import CONFIG_NAME, Check, Config, ConfigError, LLMConfig, find_root, load, save
from rung.core.errors import ToolMissing
from rung.drafting import providers
from rung.engines import lean, tla


def _choose_provider(requested: str | None) -> LLMConfig:
    if requested:
        return LLMConfig(provider=requested)
    options = providers.detect()
    # Prompts go to stderr so `--json` output on stdout stays one clean document.
    print("\nWhich model should draft specs, proofs and explanations?", file=sys.stderr)
    for i, name in enumerate(options, 1):
        print(f"  {i}. {name:18} {providers.PROVIDERS[name]}", file=sys.stderr)
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
    if args.model is not None:
        cfg.llm.model = args.model
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
        # Record each detected test suite, covering its own project directory.
        for project in info.projects:
            if not project.test_command:
                continue
            check_id = plan.static_check_id(project.path, "tests")
            options = {"run": project.test_command} | ({} if project.path == "." else {"cwd": project.path})
            covers = [] if project.path == "." else [f"{project.path}/**"]
            cfg.checks.append(Check(check_id, "command", covers, options))
            added.append(check_id)
        # The type checkers and linters the project already configures, and the shortcuts that silence them.
        for project in info.projects:
            for kind, command in project.static:
                check_id = plan.static_check_id(project.path, kind)
                if any(c.id == check_id for c in cfg.checks):
                    continue
                options = {"run": command} | ({} if project.path == "." else {"cwd": project.path})
                covers = [] if project.path == "." else [f"{project.path}/**"]
                cfg.checks.append(Check(check_id, "command", covers, options))
                added.append(check_id)
        if info.languages:
            cfg.checks.append(Check("shortcuts", "shortcuts", [], {}))
            added.append("shortcuts")

    save(cfg)
    (root / ".rung").mkdir(exist_ok=True)
    if not (root / laws.LAWS_FILE).exists():
        (root / laws.LAWS_FILE).write_text(laws.TEMPLATE)

    langs = ", ".join(f"{k} ({v})" for k, v in list(info.languages.items())[:4]) or "none found"
    out.line(f"Surveyed {root}")
    out.line(f"  languages: {langs}")
    out.line(f"  surfaces:  {', '.join(info.surfaces) or 'none found'}")
    out.line(f"  tests:     {', '.join(info.test_commands) or 'none found'}")
    out.line(f"Model provider: {cfg.llm.provider} ({state.detail})")
    if added:
        out.line(f"Added checks: {', '.join(added)}")
    out.line(f"Wrote {CONFIG_NAME}, {laws.LAWS_FILE} and .rung/.")

    installed: list[str] = []
    if not args.no_agents and ask("\nInstall the agent skills and a Stop hook that blocks unverified work? (y/n)",
                                  "y").lower().startswith("y"):  # fmt: skip
        written = agents.install(root, hooks=True, lean_version=lean.toolchain().split(":")[-1])
        installed = [str(p.relative_to(root)) for p in written]
        out.line("Installed the agent skills, AGENTS.md rules and the Claude Code Stop hook.")

    drafted = _draft_kit(args, root, info, cfg, state.ok, out)
    cfg = load(root)
    built = kit.build(cfg, info)
    recs = plan.plan(info, {c.id for c in cfg.checks})
    out.emit({"root": str(root), "provider": cfg.llm.provider, "login": state.detail, "added_checks": added,
              "agents": installed, "drafted": drafted, "kit": built.to_dict(), "survey": info.to_dict(),
              "plan": [r.__dict__ for r in recs]})  # fmt: skip
    out.line("")
    _print_kit(built, out)
    return OK


@dataclass
class _Offer:
    kind: str  # runtime | property | tla | lean
    question: str
    choices: list[str]  # candidate files; empty for runtime


def _offers(cfg: Config, info: survey.Inspection) -> list[_Offer]:
    """The parts of the kit a model can draft for this repo, which the kit does not have yet."""
    have = {kit.part_of(c) for c in cfg.checks}
    kinds = {c.kind for c in cfg.checks}
    offers: list[_Offer] = []
    if "app" not in have and info.surfaces and info.surfaces != ["library"]:
        offers.append(_Offer("runtime", f"Map the {info.surfaces[0]}'s features for you to approve, then draft a "
                             "script that drives the approved ones through scenarios and faults?", []))  # fmt: skip
    model_files = [c.path for c in info.model_candidates[:3]]
    if "properties" not in have and model_files:
        offers.append(
            _Offer("property", "Draft property tests (kept only if they catch planted bugs) for", model_files)
        )
    protocol_files = [c.path for c in info.protocol_candidates[:3]]
    if "tla" not in kinds and protocol_files and _has(tla.find_java) and _has(tla.find_jar):
        offers.append(_Offer("tla", "Model stateful code in TLA+ and check every interleaving of", protocol_files))
    if "lean" not in kinds and model_files and _has(lean.find_lake):
        offers.append(_Offer("lean", "Model a pure core in Lean and test the code against it:", model_files))
    return offers


def _has(probe) -> bool:
    try:
        probe()
    except ToolMissing:
        return False
    return True


def _pick(offer: _Offer, yes: bool) -> str | None:
    """The file the person chose for this offer ("" for runtime), or None to skip it."""
    if yes:
        return offer.choices[0] if offer.choices else ""
    if not offer.choices:
        return "" if ask(f"\n{offer.question} (y/n)", "n").lower().startswith("y") else None
    print(f"\n{offer.question}", file=sys.stderr)
    for i, path in enumerate(offer.choices, 1):
        print(f"  {i}. {path}", file=sys.stderr)
    picked = ask("Choose a number, or n to skip", "n")
    if picked.isdigit() and 0 < int(picked) <= len(offer.choices):
        return offer.choices[int(picked) - 1]
    return None


def _draft_kit(args, root: Path, info: survey.Inspection, cfg: Config, logged_in: bool, out: Output) -> list[dict]:
    """Offer to draft the parts of the kit a model writes: app scenarios, property tests, formal models.

    Each is offered once, needs the person's yes (or `--yes`), and is decided by a checker, not the model.
    """
    if args.no_drafts or cfg.llm.provider == "none":
        return []
    offers = _offers(cfg, info)
    if not offers:
        return []
    if not logged_in:
        out.line(
            f"\nSkipping the drafted parts of the kit: {cfg.llm.provider} is not logged in. `rung kit` lists them."
        )
        return []
    if not args.yes and not sys.stdin.isatty():
        return []  # nobody to ask: `rung kit` lists what can be drafted
    if out.json_mode and not args.yes:
        return []
    out.line("\nA model can draft the rest of the kit; a checker keeps a draft only if it proves itself.")
    results: list[dict] = []
    for offer in offers:
        target = _pick(offer, args.yes)
        if target is None:
            continue
        ns = argparse.Namespace(dir=str(root), kind=offer.kind, files=[target] if target else [], intent=None,
                                require=[".png"] if offer.kind == "runtime" else None, id=None, out=None,
                                model=None)  # fmt: skip
        if offer.kind != "runtime":  # the app part starts with the map, which prints its own steps
            out.line(f"\n$ rung draft {offer.kind} {target}".rstrip())
        from rung.cli.commands.draft import cmd_draft  # drafting pulls in the providers; only load it when used

        before = {c.id for c in load(root).checks}
        note = ""
        try:
            if offer.kind == "runtime" and (note := _map_first(args, root, out)):
                code = 0
            else:
                code = cmd_draft(ns, _Silent() if out.json_mode else out)
        except (ConfigError, ToolMissing) as exc:
            out.line(f"  skipped: {exc}")
            code = 2
        added = sorted({c.id for c in load(root).checks} - before)
        results.append({"kind": offer.kind, "target": target, "exit": code, "added": added, "note": note})
    if results:
        out.line("\nDrafted parts:")
        for r in results:
            what = f"{r['kind']} {r['target']}".strip()
            if r.get("note"):
                verdict = r["note"]
            elif r["added"] and r["exit"] == 0:
                verdict = f"added {', '.join(r['added'])}"
            elif r["added"]:
                verdict = f"added {', '.join(r['added'])}, with a finding to review (above)"
            elif r["exit"] == 1:
                verdict = "not added: a finding or a failed draft to review (above)"
            else:
                verdict = "skipped"
            out.line(f"  {what:40} {verdict}")
    return results


def _map_first(args, root: Path, out: Output) -> str:
    """Draft the feature map and ask for approval before any drive script is written.

    Returns why no script was drafted ("" when the approved features should be drafted now).
    """
    from rung.cli.commands import featuremap
    from rung.drafting import feature_map

    cfg = load(root)
    out.line("\n$ rung map")
    if not featuremap.has_map(root) and not featuremap.draft_map(cfg, out):
        return "feature map: no draft met the contract (above)"
    out.line("")
    featuremap.print_map(cfg, out)
    if not args.yes:
        featuremap.review(cfg, out)
    state = feature_map.load_state(root)
    if state is None or not state.with_status("approved"):
        return "feature map drafted; approve features with `rung map`, then `rung draft runtime`"
    out.line("\n$ rung draft runtime --require .png")
    return ""


class _Silent(Output):
    """Swallows a nested command's own JSON document so `rung init --json` prints exactly one."""

    def __init__(self) -> None:
        super().__init__(json_mode=True, color=False)

    def emit(self, payload) -> None:
        return None


_PART_STYLE = {"ok": ("ok", "green"), "attention": ("ATTENTION", "yellow"), "missing": ("missing", "dim"),
               "n/a": ("n/a", "dim")}  # fmt: skip


def _print_kit(built: kit.Kit, out: Output) -> None:
    out.line(out.style("Verification kit", "bold"))
    for part in built.parts:
        out.line(f"\n{out.badge(part.state, _PART_STYLE, 10)} {out.style(part.title, 'bold')}  "
                 f"{out.style('catches ' + part.catches, 'dim')}")  # fmt: skip
        for e in part.entries:
            label = out.badge(
                e.state, STATE_STYLE | {"installed": ("installed", "green"), "approved": ("approved", "green")}, 10
            )
            out.line(f"           {label} {e.check_id}" + (f"  {out.style(e.detail, 'dim')}" if e.detail else ""))
        if not part.entries:
            out.detail(part.missing, indent=11)
            if part.suggest:
                out.line(f"           {out.style('$ ' + part.suggest, 'blue')}")
    if built.laws:
        summary = ", ".join(f"{n} {state}" for state, n in sorted(built.laws.items()))
        out.line(f"\n{out.style('Laws', 'bold')} (LAWS.md): {summary}")
    if built.next_steps:
        cmd, why = built.next_steps[0]
        out.line(f"\n{out.style('Next:', 'bold')} {out.style(cmd, 'blue')}  {out.style(why, 'dim')}")
    else:
        out.line(f"\n{out.style('Every part of the kit is in place and fresh.', 'green')}")


def cmd_kit(args, out: Output) -> int:
    cfg = load(find_root(Path(args.dir)))
    built = kit.build(cfg, survey.survey(cfg.root))
    out.emit(built.to_dict())
    _print_kit(built, out)
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
