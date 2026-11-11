"""Commands that set a project up: init, kit, plan, doctor, agents, hook."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rung import agents
from rung.agents.install import prepare_install
from rung.analysis import kit, plan, survey
from rung.checks.command import cargo_test, isolates_evidence, package_script_commands
from rung.checks.presets import PRESETS
from rung.cli.commands.common import GATE_FAILED, OK, ask, resolve_base
from rung.cli.output import STATE_STYLE, Output
from rung.core import git, launch, laws, sandbox
from rung.core.config import (
    CONFIG_NAME,
    Check,
    Config,
    ConfigError,
    LLMConfig,
    append_checks,
    find_root,
    load,
    managed_path,
    save,
    write_atomic,
)
from rung.core.errors import ToolMissing
from rung.core.proc import run as run_process
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


def _starter_checks(root: Path, info: survey.Inspection) -> list[Check]:
    checks: list[Check] = []
    files = git.list_files(root)
    # Wire up the security scanners that are installed and relevant to this repo.
    for check_id, tool in (
        ("secrets", "gitleaks"),
        ("vulnerable-deps", "osv-scanner"),
        ("workflows", "zizmor"),
    ):
        if tool == "osv-scanner":
            paths = [f for f in files if Path(f).name not in {"pyproject.toml", "package.json", "go.mod", "pom.xml"}]
        else:
            paths = files
        relevant = tool == "gitleaks" or any(
            git.glob_match(pattern, path) for pattern in PRESETS[tool].covers for path in paths
        )
        if shutil.which(tool) and relevant:
            checks.append(Check(check_id, "scan", [], {"tool": tool}))
    checks.append(Check("deps", "deps", [], {"min_age_days": 30}))
    checks.extend(check for _, check in plan.detected_checks(info))
    if info.languages:
        checks.append(Check("shortcuts", "shortcuts", [], {}))
    return checks


def _local_ignores(path: Path) -> str | None:
    """Keep raw evidence and cache receipts local by default."""
    text = path.read_text() if path.is_file() else ""
    entries = {line.strip().lstrip("/") for line in text.splitlines()}
    missing = [entry for entry in (".rung/cache/", ".rung/evidence/", ".rung/runs/") if entry not in entries]
    if not missing:
        return None
    prefix = text + ("\n" if text and not text.endswith("\n") else "")
    return prefix + "\n# Rung local state\n" + "\n".join("/" + entry for entry in missing) + "\n"


def cmd_init(args, out: Output) -> int:
    root = Path(args.dir).resolve()
    existing = (root / CONFIG_NAME).exists()
    cfg = load(root) if existing else Config(root=root, name=root.name, llm=LLMConfig(), checks=[])
    if not root.is_dir():
        raise ConfigError(f"project directory does not exist: {root}")
    info = survey.survey(root)

    interactive = sys.stdin.isatty() and not out.json_mode
    # `--model sonnet` already says which CLI to use, so only an ambiguous model name still asks.
    requested = args.provider or (providers.provider_for_model(args.model) if args.model and not existing else None)
    if requested:
        cfg.llm = LLMConfig(provider=requested) if requested != cfg.llm.provider else cfg.llm
    elif not existing and not args.no_drafts and not args.dry_run and interactive:
        cfg.llm = _choose_provider(None)
    if args.model is not None:
        cfg.llm.model = args.model
    current = {check.id: check for check in cfg.checks}
    detected = _starter_checks(root, info) if not existing or args.refresh else []
    # A command already recorded under another id (same run and cwd) is the same check; do not add it twice.
    recorded = {(c.get("run"), c.get("cwd", ".")) for c in cfg.checks if c.kind == "command"}
    new = [
        check
        for check in detected
        if check.id not in current
        and not (check.kind == "command" and (check.get("run"), check.get("cwd", ".")) in recorded)
    ]
    changed_commands = [
        {"id": check.id, "configured": current[check.id].get("run"), "detected": check.get("run")}
        for check in detected
        if check.kind == "command" and check.id in current and current[check.id].get("run") != check.get("run")
    ]
    cfg.checks.extend(new)
    added = [check.id for check in new]
    missing_cwds = {
        check.id: check.get("cwd")
        for check in cfg.checks
        if check.get("cwd") and not (root / check.get("cwd")).is_dir()
    }
    ignores = managed_path(root, ".gitignore")
    ignore_update = _local_ignores(ignores)
    prompt = "Install the agent skills and Claude Code Stop hook? (y/n)"
    install_agents = not args.no_agents and (
        args.dry_run or not interactive or ask(prompt, "y").lower().startswith("y")
    )
    if install_agents:
        prepare_install(root, hooks=True)

    if args.dry_run:
        files = [CONFIG_NAME] if new or not existing or args.provider or args.model is not None else []
        if not (root / laws.LAWS_FILE).exists():
            files.append(laws.LAWS_FILE)
        if not (root / ".rung").exists():
            files.append(".rung/")
        if ignore_update is not None:
            files.append(".gitignore")
        if install_agents:
            files += [".claude/skills/", ".agents/skills/", "AGENTS.md", ".claude/settings.json"]
        checks = [check.__dict__ for check in cfg.checks]
        out.emit({"schema_version": 1, "root": str(root), "dry_run": True, "checks": checks, "added_checks": added,
                  "files": files, "provider": cfg.llm.provider, "survey": info.to_dict(),
                  "missing_working_directories": missing_cwds, "changed_detected_commands": changed_commands,
                  })  # fmt: skip
        out.heading("Setup preview", f"{_shown(root)} · {len(cfg.checks)} checks · no writes")
        for check in cfg.checks:
            description = check.get("run") or check.get("tool") or check.kind
            out.detail(check.id, indent=0)
            out.detail(f"{description} · cwd {check.get('cwd', '.')}", indent=2)
        for check_id, cwd in missing_cwds.items():
            out.detail(f"{check_id} uses missing directory {cwd}; review {CONFIG_NAME}.", indent=2)
        for change in changed_commands:
            out.detail(f"{change['id']} now detects {change['detected']!r}; review {CONFIG_NAME}.", indent=2)
        out.line("Files to set up: " + (", ".join(files) or "none"))
        argv = ["rung", "init", "--no-drafts"]
        if args.no_agents:
            argv.append("--no-agents")
        if args.refresh:
            argv.append("--refresh")
        if args.provider:
            argv += ["--provider", args.provider]
        if args.model is not None:
            argv += ["--model", args.model]
        out.line("No checks or model calls ran. To apply:")
        out.command(shlex.join(argv))
        return OK

    managed_path(root, CONFIG_NAME)
    laws_path = managed_path(root, laws.LAWS_FILE)
    state_dir = managed_path(root, ".rung")

    state = providers.LoginState(False, "drafting disabled")
    drafting = not args.no_drafts and cfg.llm.provider != "none" and (args.yes or interactive)
    if drafting:
        provider = providers.get(cfg.llm)
        state = provider.login_state()
        if (
            not state.ok
            and interactive
            and cfg.llm.provider in ("claude-cli", "codex-cli")
            and ask("Not logged in. Log in now? (y/n)", "n").lower().startswith("y")
        ):
            provider.login()
            state = provider.login_state()

    if not existing or args.provider or args.model is not None:
        save(cfg)
    else:
        append_checks(cfg, new)
    state_dir.mkdir(exist_ok=True)
    if not laws_path.exists():
        write_atomic(laws_path, laws.TEMPLATE)
    if ignore_update is not None:
        write_atomic(ignores, ignore_update)

    langs = ", ".join(f"{k} ({v})" for k, v in list(info.languages.items())[:4]) or "none found"
    out.line(f"Surveyed {_shown(root)}")
    out.line(f"  languages: {langs}")
    out.line(f"  surfaces:  {', '.join(info.surfaces) or 'none found'}")
    out.line(f"  tests:     {', '.join(info.test_commands) or 'none found'}")
    out.line(f"Model provider: {cfg.llm.provider} ({state.detail})")
    if added:
        out.line(f"Added checks: {', '.join(added)}")
    for check_id, cwd in missing_cwds.items():
        out.detail(f"{check_id} uses missing directory {cwd}; review {CONFIG_NAME}.", indent=2)
    for change in changed_commands:
        out.detail(f"{change['id']} now detects {change['detected']!r}; review {CONFIG_NAME}.", indent=2)
    out.line(f"Kit configuration: {CONFIG_NAME}, {laws.LAWS_FILE} and .rung/.")

    installed: list[str] = []
    if install_agents:
        written = agents.install(root, hooks=True, lean_version=lean.toolchain().split(":")[-1])
        installed = [str(p.relative_to(root)) for p in written]
        out.line("Installed Claude Code and Codex skills, AGENTS.md rules, and the Claude Code Stop hook.")

    drafted = _draft_kit(args, root, info, cfg, state.ok, out)
    cfg = load(root)
    built = kit.build(cfg, info)
    recs = plan.plan(info, {c.id for c in cfg.checks})
    out.emit({"root": str(root), "provider": cfg.llm.provider, "login": state.detail, "added_checks": added,
              "agents": installed, "drafted": drafted, "kit": built.to_dict(), "survey": info.to_dict(),
              "missing_working_directories": missing_cwds, "changed_detected_commands": changed_commands,
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
        surface = next((x for x in ("web UI", "desktop app") if x in info.surfaces), info.surfaces[0])
        offers.append(_Offer("runtime", f"Map the {surface}'s features for you to approve, then draft a "
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
    from rung.engines import map_page

    cfg = load(root)
    out.line("\n$ rung map")
    if not featuremap.has_map(root) and not featuremap.draft_map(cfg, out):
        return "feature map: no draft met the contract (above)"
    out.line("")
    featuremap.print_map(cfg, out, brief=True)
    page = map_page.write(root, check_ids=featuremap.evidence_check_ids(cfg))
    out.line(f"\nEach feature in full: {(feature_map.MAP_DIR / 'README.md').as_posix()} and {page.relative_to(root)}")
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
                out.command(part.suggest, indent=11)
    if built.laws:
        summary = ", ".join(f"{n} {state}" for state, n in sorted(built.laws.items()))
        out.line(f"\n{out.style('Laws', 'bold')} (LAWS.md): {summary}")
    if built.next_steps:
        cmd, why = built.next_steps[0]
        out.line()
        out.command(cmd, indent=0, prefix="Next: ")
        out.detail(why, indent=2)
    elif any(part.state in ("missing", "attention") for part in built.parts):
        out.line("\nRegister the missing parts above with the commands they show.")
    else:
        out.line(f"\n{out.style('Every part of the kit is in place and fresh.', 'green')}")


def cmd_kit(args, out: Output) -> int:
    cfg = load(find_root(Path(args.dir)))
    built = kit.build(cfg, survey.survey(cfg.root))
    out.emit(built.to_dict())
    _print_kit(built, out)
    return OK


def _shown(root: Path) -> str:
    """The project as a person would name it: its folder name when run from inside it, else `~/...` or the path."""
    if root == Path.cwd().resolve():
        return root.name
    home = Path.home().resolve()
    return f"~/{root.relative_to(home)}" if root.is_relative_to(home) else str(root)


def _print_plan(recs: list[plan.Recommendation], out: Output) -> None:
    out.line("\nRecommended next steps:")
    for i, rec in enumerate(recs, 1):
        out.line(f"\n {i}. {out.style(rec.title, 'bold')}  [{rec.rung}, effort {rec.effort}]")
        out.detail(rec.why, indent=4)
        if rec.command:
            out.command(rec.command, indent=4)


def cmd_plan(args, out: Output) -> int:
    root = Path(args.dir).resolve()
    try:
        root = find_root(root)
    except ConfigError:
        checks = []
    else:
        checks = load(root).checks
    info = survey.survey(root)
    payload: dict = {"schema_version": 1, "survey": info.to_dict()}
    paths = None
    if args.base and not args.changed:
        raise ConfigError("use --base with --changed")
    if args.changed or args.path:
        base = resolve_base(root, args.base) if args.changed else None
        paths = git.changed_files(root, base) if base else set()
        outside: list[str] = []
        for value in args.path or []:
            for path in _plan_paths(root, value):
                paths.add(path)
                if not any(Path(path).is_relative_to(p.path) for p in info.projects):
                    outside.append(path)
        paths = {path for path in paths if not git.is_rung_output(path)}
        scope = plan.change_plan(info, checks, paths, base=base)
        payload["change"] = {"base": base, **scope, "outside_projects": sorted(set(outside))}
        out.line(f"Verification plan for {len(paths)} path(s)")
        for path in sorted(set(outside)):
            out.detail(f"{path} is outside every detected project; no test suite is recommended for it", indent=2)
        if scope["focused_run"]:
            out.detail("Focused checks for declared targets", indent=2)
            out.command(scope["focused_run"], indent=4)
        if scope["run"]:
            if scope["focused_run"]:
                out.detail("All checks that may be affected", indent=2)
            out.command(scope["run"])
        for path in scope["without_execution_check"]:
            out.detail(f"No command, runtime or formal check scope includes {path}", indent=2)
        for step in scope["follow_up"]:
            out.detail(f"{step['label']} ({step['reason']})", indent=2)
            out.command(step["command"], indent=4)
    recs = plan.plan(info, {check.id for check in checks}, paths=paths)
    payload["plan"] = [r.__dict__ for r in recs]
    out.emit(payload)
    _print_plan(recs, out)
    return OK


def _plan_paths(root: Path, value: str) -> list[str]:
    """Project-relative paths for one `--path`: relative to the working directory when run inside the
    project (else to the project root), and a directory stands for the tracked files under it."""
    cwd = Path.cwd().resolve()
    base = cwd if cwd.is_relative_to(root) else root
    resolved = (base / value).resolve()
    if not resolved.is_relative_to(root):
        raise ConfigError(f"--path must name a path inside the project: {value!r}")
    rel = resolved.relative_to(root).as_posix()
    if not resolved.is_dir():
        return [rel]
    prefix = "" if rel == "." else rel + "/"
    return [name for name in git.list_files(root) if name.startswith(prefix)] or [rel]


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
        command = name.removesuffix("-cli")
        found = shutil.which(command)
        rows.append((name, bool(found), found or f"not installed: {command}"))
    try:
        guard = sandbox.probe_write_guard(Path(args.dir))
    except ToolMissing as exc:
        rows.append(("draft write guard", False, str(exc)))
    else:
        rows.append(("draft write guard", True, guard))
    try:
        root = find_root(Path(args.dir))
    except ConfigError:
        if args.strict:
            raise
        required = []
    else:
        required = _configured_tools(load(root), rows)
    rows.extend(required)
    # `strict_failures` names the configured tools `--strict` fails on, whether or not it was passed.
    failures = [label for label, ok, _ in required if not ok]
    out.emit({"tools": [{"tool": label, "ok": ok, "detail": detail} for label, ok, detail in rows],
              "strict_failures": failures})  # fmt: skip
    for label, ok, detail in rows:
        mark = out.style("ok ", "green") if ok else out.style("-- ", "dim")
        out.line(f"{mark} {label:14} {detail}")
    return GATE_FAILED if args.strict and failures else OK


def _configured_tools(cfg: Config, probes: list[tuple[str, bool, str]]) -> list[tuple[str, bool, str]]:
    available = {name: (ok, detail) for name, ok, detail in probes}
    rows = []
    for check in cfg.checks:
        if isolates_evidence(check):
            ok, detail = available["draft write guard"]
            rows.append((f"{check.id}: write guard", ok, detail))
        keys = ("model", "impl") if check.kind == "drt" else ("run",)
        for key in keys:
            if not (command := check.get(key)):
                continue
            argv = shlex.split(command)
            executable = argv[0]
            cwd_key = "cwd" if key == "run" else f"{key}_cwd"
            cwd = cfg.root / check.get(cwd_key, ".")
            label = f"{check.id}: {key} ({executable})"
            if not cwd.is_dir():
                rows.append((label, False, f"working directory {cwd} is missing; review {CONFIG_NAME}"))
                continue
            if key == "model" and executable == "lake":
                ready, found = available["lake (Lean)"]
                rows.append((f"{check.id}: {key} (lake)", ready, found))
                continue
            found = launch.find_executable(executable, cwd)
            rows.append((label, bool(found), found or f"not executable from {cwd}; install {executable}"))
            if found and (inner := launch.delegated(argv, cwd)) and inner.host_path:
                name = inner.argv[0]
                inner_tool = launch.find_executable(name, inner.cwd)
                detail = inner_tool or f"not executable from {inner.cwd}. Install {name} for the delegated command"
                rows.append((f"{check.id}: delegated runner ({name})", bool(inner_tool), detail))
            if found and cargo_test(argv) and "--locked" in argv:
                lock = next((p / "Cargo.lock" for p in (cwd, *cwd.parents) if (p / "Cargo.lock").is_file()), None)
                detail = str(lock) if lock else f"run cargo generate-lockfile in {cwd}, then review the lockfile"
                rows.append((f"{check.id}: Cargo.lock", bool(lock), detail))
            target = launch.python_entrypoint(argv)
            if found and target and target[0] == "module":
                module = target[1]
                relative = cwd.joinpath(*module.split("."))
                if relative.with_suffix(".py").is_file() or (relative / "__main__.py").is_file():
                    ok, detail = True, f"project module in {cwd}"
                else:
                    ok, detail = _python_module(found, module.split(".")[0])
                rows.append((f"{check.id}: Python module {module}", ok, detail))
            for script in package_script_commands(cwd, argv) if found else []:
                if not script or script[0] not in {
                    "vitest",
                    "jest",
                    "mocha",
                    "ava",
                    "playwright",
                    "eslint",
                    "tsc",
                    "next",
                }:
                    continue
                tool = script[0]
                binary = tool + (".cmd" if os.name == "nt" else "")
                ready = bool(shutil.which(tool)) or any(
                    (Path(executable).name == "yarn" and (directory / ".pnp.cjs").is_file())
                    or os.access(directory / "node_modules" / ".bin" / binary, os.X_OK)
                    for directory in (cwd, *cwd.parents)
                )
                if ready:
                    detail = "available to the package script"
                elif Path(executable).name == "npm" and (cwd / "package-lock.json").is_file():
                    detail = f"run npm ci in {cwd}"
                else:
                    detail = f"install dependencies for {cwd} using the project's package manager"
                rows.append((f"{check.id}: Node runner {tool}", ready, detail))
        tools = {
            "scan": (check.get("tool"),),
            "lean": ("lake (Lean)",),
            "tla": ("java (TLC)", "tla2tools.jar"),
        }.get(check.kind, ())
        for tool in tools:
            if tool and not (check.kind == "scan" and check.get("run")):
                ok, detail = available.get(tool, (False, f"unknown tool: {tool}"))
                rows.append((f"{check.id}: {tool}", ok, detail))
    return rows


def _python_module(executable: str, module: str) -> tuple[bool, str]:
    """Probe the PATH interpreter in isolation, without importing the module or any project code."""
    probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
    try:
        result = run_process([str(Path(executable).absolute()), "-I", "-c", probe, module],
                             cwd=Path(sys.base_prefix), timeout=5)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False, f"could not inspect {executable}"
    if result.returncode:
        return False, f"install {module} into the environment used by {executable}"
    return True, f"available to {executable}"


def cmd_agents(args, out: Output) -> int:
    root = find_root(Path(args.dir))
    written = agents.install(
        root, hooks=args.hooks, codex_hooks=args.codex_hooks, lean_version=lean.toolchain().split(":")[-1]
    )
    out.emit({"written": [str(p.relative_to(root)) for p in written]})
    for path in written:
        out.line(f"wrote {path.relative_to(root)}")
    if args.hooks:
        out.line("Claude Code will now run `rung hook stop` before an agent finishes.")
    if args.codex_hooks:
        out.line("Review and trust the project Stop hook in Codex with /hooks.")
    return OK


def cmd_hook(args, out: Output) -> int:
    return agents.main_stop_hook(agent=args.agent)
