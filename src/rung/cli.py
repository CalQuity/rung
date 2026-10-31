"""`rung` command line."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

from rung import __version__, checks, draft, laws, ledger, plan, providers, skills, survey
from rung.config import CONFIG_NAME, Check, Config, ConfigError, LLMConfig, find_root, load, save
from rung.errors import ToolMissing
from rung.runners import lean as lean_runner
from rung.runners import tla as tla_runner

MARK = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}
STATE_MARK = {"fresh": "fresh", "stale": "STALE", "failing": "FAILING", "never": "never run"}


def _config(args) -> Config:
    return load(find_root(Path(args.dir)))


def _ask(question: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


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
        raise SystemExit(f"unknown provider {name!r}")
    llm = LLMConfig(provider=name)
    if name == "openai-compatible":
        llm.base_url = _ask("Base URL (e.g. http://localhost:11434/v1)", "http://localhost:11434/v1")
        llm.model = _ask("Model name", "qwen2.5-coder")
    return llm


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    existing = (root / CONFIG_NAME).exists()
    config = load(root) if existing else Config(root=root, name=root.name, llm=LLMConfig(), checks=[])
    print(f"Surveying {root} ...")
    info = survey.survey(root)
    langs = ", ".join(f"{k} ({v})" for k, v in list(info.languages.items())[:4]) or "none found"
    print(f"  languages: {langs}")
    print(f"  surfaces:  {', '.join(info.surfaces) or 'none found'}")
    print(f"  tests:     {', '.join(info.test_commands) or 'none found'}")
    if info.formal:
        print(f"  formal:    {', '.join(f'{k} ({len(v)})' for k, v in info.formal.items())}")

    if not existing or args.provider:
        config.llm = _choose_provider(args.provider)
    provider = providers.get(config.llm)
    state = provider.login_state()
    print(f"\nModel provider: {config.llm.provider} — {state.detail}")
    needs_login = not state.ok and config.llm.provider in ("claude-cli", "codex-cli")
    if needs_login and _ask("Not logged in. Log in now? (y/n)", "y").lower().startswith("y"):
        provider.login()
        print(f"  {provider.login_state().detail}")

    save(config)
    (root / ".rung").mkdir(exist_ok=True)
    if not (root / laws.LAWS_FILE).exists():
        (root / laws.LAWS_FILE).write_text(laws.TEMPLATE)
    print(f"\nWrote {CONFIG_NAME}, {laws.LAWS_FILE} and .rung/.")
    _print_plan(plan.plan(info))
    print("Next: `rung skill` to teach your agents the rules, then `rung check`.")
    return 0


def _print_plan(recs: list[plan.Recommendation]) -> None:
    print("\nRecommended rungs (cheapest first):")
    for i, rec in enumerate(recs, 1):
        print(f"\n {i}. [{rec.rung}] {rec.title}  (effort: {rec.effort})")
        print(f"    {rec.why}")
        if rec.command:
            print(f"    -> {rec.command}")


def cmd_plan(args) -> int:
    info = survey.survey(Path(args.dir))
    recs = plan.plan(info)
    if args.json:
        print(
            json.dumps({"survey": json.loads(info.to_json()), "plan": [r.__dict__ for r in recs]}, indent=2)
        )
    else:
        _print_plan(recs)
    return 0


def _print_outcome(outcome: checks.Outcome) -> None:
    print(f"{MARK[outcome.verdict]:5} {outcome.check.id} ({outcome.check.kind}): {outcome.summary}")
    for line in outcome.lines:
        for part in line.splitlines():
            print(f"      {part}")


def cmd_check(args) -> int:
    config = _config(args)
    selected = [config.check(i) for i in args.ids] if args.ids else config.checks
    if not selected:
        print(f"No checks declared in {CONFIG_NAME}. See `rung plan`.")
        return 1
    if args.ci:
        current = {s.check_id: s for s in checks.statuses(config)}
        selected = [c for c in selected if not current[c.id].trusted]
        if not selected:
            print("All verdicts are fresh.")
            return 0
    failed = 0
    for check in selected:
        outcome = checks.run_check(config, check)
        _print_outcome(outcome)
        failed += outcome.verdict != "pass"
    return 1 if failed else 0


def cmd_status(args) -> int:
    config = _config(args)
    rows = checks.statuses(config)
    for s in rows:
        when = s.entry["at"] if s.entry else ""
        print(f"{STATE_MARK[s.state]:10} {s.check_id:28} {when}")
        for path in s.changed:
            print(f"           changed: {path}")
    problems = ledger.verify_chain(ledger.read(config.root))
    for p in problems:
        print(f"LEDGER     {p}")
    if args.ci:
        return 0 if rows and all(s.trusted for s in rows) and not problems else 1
    return 0


def cmd_laws(args) -> int:
    config = _config(args)
    if args.approve:
        laws.set_status(config.root, args.approve, "approved")
        print(f"Approved {args.approve}.")
    statuses = {s.check_id: s for s in checks.statuses(config)}
    all_laws = laws.load(config.root)
    if not all_laws:
        print(f"No laws in {laws.LAWS_FILE} yet.")
        return 0
    for law in all_laws:
        state, why = laws.state(law, statuses)
        print(f"{state.upper():10} {law.id}\n           {law.text}\n           {why}")
    return 0


def cmd_ledger(args) -> int:
    config = _config(args)
    entries = ledger.read(config.root)
    problems = ledger.verify_chain(entries)
    print(f"{len(entries)} entries; chain {'intact' if not problems else 'BROKEN'}")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


def _add_check(config: Config, check: Check) -> None:
    config.checks = [c for c in config.checks if c.id != check.id] + [check]
    save(config)
    print(f"Added check {check.id!r} to {CONFIG_NAME}.")


def _slug(paths: list[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(paths[0]).stem.lower()).strip("-") or "spec"


def cmd_draft(args) -> int:
    config = _config(args)
    provider = providers.get(config.llm)
    root = config.root
    sources = [(root / p).resolve() for p in args.files]
    for src in sources:
        if not src.is_file():
            raise SystemExit(f"{src} not found")
    rel_sources = [s.relative_to(root).as_posix() for s in sources]
    check_id = args.id or f"{_slug(rel_sources)}-{args.kind}"
    out = root / (args.out or f"spec/{args.kind}/{_slug(rel_sources)}")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Drafting {args.kind} for {', '.join(rel_sources)} with {config.llm.provider} ...")

    if args.kind == "tla":
        result = draft.draft_tla(provider, root, sources, out, intent=args.intent or "")
    else:
        result = draft.draft_lean(provider, root, sources, out, intent=args.intent or "")
    print(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    for f in result.files:
        print(f"  wrote {f.relative_to(root)}")
    if result.trace:
        print("\nCounterexample:\n" + result.trace)
        print(
            "\nIf this is a real bug, fix the code and run `rung check`. "
            "If the spec is wrong, edit it yourself."
        )
    if not result.ok:
        return 1

    if args.kind == "tla":
        spec = next(f for f in result.files if f.suffix == ".tla")
        cfg = next(f for f in result.files if f.suffix == ".cfg")
        _add_check(
            config,
            Check(
                check_id,
                "tla",
                rel_sources,
                {
                    "spec": spec.relative_to(root).as_posix(),
                    "config": cfg.relative_to(root).as_posix(),
                },
            ),
        )
    else:
        project = out.relative_to(root).as_posix()
        _add_check(
            config,
            Check(check_id, "lean", rel_sources, {"project": project, "theorems": result.theorems}),
        )
        exe = _lean_exe(out)
        if exe and result.alphabet and (out / "drt_adapter.py").exists():
            _add_check(
                config,
                Check(
                    f"{check_id}-drt",
                    "drt",
                    rel_sources,
                    {
                        "model": f"lake exe {exe}",
                        "model_cwd": project,
                        "impl": f"python {project}/drt_adapter.py",
                        "alphabet": result.alphabet,
                        "cases": 2000,
                        "max_len": 8,
                        "specs": [f"{project}/**/*.lean", f"{project}/drt_adapter.py"],
                    },
                ),
            )
    print("Propose the guarantees as laws in LAWS.md (status: proposed) and approve them after reading.")
    return 0


def _lean_exe(project: Path) -> str:
    try:
        lakefile = tomllib.loads((project / "lakefile.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    exes = lakefile.get("lean_exe", [])
    return exes[0].get("name", "") if exes else ""


def cmd_add(args) -> int:
    config = _config(args)
    _add_check(config, Check(args.id, args.kind, args.covers or [], {"run": args.run}))
    return 0


def cmd_skill(args) -> int:
    root = find_root(Path(args.dir))
    for path in skills.install(root):
        print(f"wrote {path.relative_to(root)}")
    return 0


def cmd_doctor(args) -> int:
    rows = []
    for label, probe in (
        ("java (TLC)", tla_runner.find_java),
        ("tla2tools.jar", tla_runner.find_jar),
        ("lake (Lean)", lean_runner.find_lake),
    ):
        try:
            rows.append((label, True, str(probe())))
        except ToolMissing as exc:
            rows.append((label, False, str(exc)))
    rows.append(("bend", bool(shutil.which("bend")), shutil.which("bend") or "not installed (optional)"))
    for name in ("claude-cli", "codex-cli"):
        state = providers.get(LLMConfig(provider=name)).login_state()
        rows.append((name, state.ok, state.detail))
    for label, ok, detail in rows:
        print(f"{'ok ' if ok else '-- '} {label:14} {detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rung", description="An opinionated verification ladder for agent-written software."
    )
    p.add_argument("--version", action="version", version=f"rung {__version__}")
    p.add_argument("-C", "--dir", default=".", help="project directory (default: current)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="survey the repo, pick a model provider, write rung.toml")
    s.add_argument("--provider", choices=list(providers.PROVIDERS))
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("plan", help="recommend verification rungs for this repo")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("check", help="run checks and record verdicts")
    s.add_argument("ids", nargs="*")
    s.add_argument("--ci", action="store_true", help="re-run only non-fresh checks; fail unless all pass")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("status", help="show which verdicts are fresh, stale or failing")
    s.add_argument("--ci", action="store_true", help="exit 1 unless every verdict is fresh")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("laws", help="show LAWS.md and whether each law holds")
    s.add_argument("--approve", metavar="LAW_ID")
    s.set_defaults(func=cmd_laws)

    s = sub.add_parser("ledger", help="verify the evidence ledger's hash chain")
    s.set_defaults(func=cmd_ledger)

    s = sub.add_parser("draft", help="draft a TLA+ spec or Lean model from code with the configured model")
    s.add_argument("kind", choices=["tla", "lean"])
    s.add_argument("files", nargs="+")
    s.add_argument("--intent", help="what the code must guarantee, in plain words")
    s.add_argument("--id")
    s.add_argument("--out")
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("add", help="declare a command (test suite, evidence script) as a check")
    s.add_argument("id")
    s.add_argument(
        "--kind",
        choices=["command", "evidence"],
        default="command",
        help="`evidence`: the script must write artifacts to $RUNG_EVIDENCE_DIR",
    )
    s.add_argument("--run", required=True)
    s.add_argument("--covers", nargs="*")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("skill", help="install the Rung skill for Claude Code and an AGENTS.md section")
    s.set_defaults(func=cmd_skill)

    s = sub.add_parser("doctor", help="check that Java, TLC, Lean and model CLIs are available")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"rung: {exc}", file=sys.stderr)
        return 2
    except ToolMissing as exc:
        print(f"rung: {exc}", file=sys.stderr)
        return 3
