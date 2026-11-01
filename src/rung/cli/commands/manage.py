"""Commands that change what is checked: laws, add, baseline, waive."""

from __future__ import annotations

import re
from datetime import date, timedelta

from rung.checks import findings, runner
from rung.checks.presets import PRESETS
from rung.checks.scan import ScanChecker
from rung.cli.commands.common import OK, add_check, load_config
from rung.cli.output import STATE_STYLE, Output
from rung.core import git, laws
from rung.core.config import Check, ConfigError


def cmd_laws(args, out: Output) -> int:
    cfg = load_config(args)
    if args.approve:
        try:
            laws.set_status(cfg.root, args.approve, "approved")
        except KeyError as exc:
            raise ConfigError(f"no law {args.approve!r} in {laws.LAWS_FILE}") from exc
        out.line(f"Approved {args.approve}.")
    statuses = {s.check_id: s for s in runner.statuses(cfg)}
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
    cfg = load_config(args)
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
    add_check(cfg, Check(args.id, args.kind, args.covers or [], options), out)
    return OK


def cmd_baseline(args, out: Output) -> int:
    cfg = load_config(args)
    check = cfg.check(args.check)
    if check.kind != "scan":
        raise ConfigError("baselines apply to `scan` checks")
    found = ScanChecker().collect(cfg.root, check, check.get_int("timeout", 900))
    path = findings.save_baseline(cfg.root, check.id, found, git.head(cfg.root))
    out.emit({"check": check.id, "findings": len(found), "path": str(path.relative_to(cfg.root))})
    out.line(f"Recorded {len(found)} existing finding(s) for {check.id!r} as accepted ({path.relative_to(cfg.root)}).")
    out.line("Commit this file; from now on only findings beyond it will block.")
    return OK


def cmd_waive(args, out: Output) -> int:
    cfg = load_config(args)
    check = cfg.check(args.check)
    if not re.fullmatch(r"[0-9a-f]{16}", args.fingerprint):
        raise ConfigError("a fingerprint is the 16-character hex id shown in brackets by `rung check`")
    try:
        expires = date.fromisoformat(args.until) if args.until else date.today() + timedelta(days=args.days)
    except ValueError as exc:
        raise ConfigError(f"--until must be a date like 2026-12-31, not {args.until!r}") from exc
    if expires < date.today():
        raise ConfigError("a waiver must expire in the future")
    approver = args.approved_by or git.user_name(cfg.root)
    if not approver:
        raise ConfigError("cannot tell who approves this waiver; pass --approved-by")
    w = findings.Waiver(check.id, args.fingerprint, args.reason, approver, expires)
    path = findings.add_waiver(cfg.root, w)
    out.emit({"check": w.check, "fingerprint": w.fingerprint, "expires": str(w.expires), "approved_by": approver})
    out.line(f"Waived {w.fingerprint} on {w.check!r} until {w.expires} ({path.relative_to(cfg.root)}).")
    return OK
