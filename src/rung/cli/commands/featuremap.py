"""`rung map`: draft the feature map, show it, and record a person's decision on each feature.

The map is the plan for the app-driving part of the kit. A model drafts it from the code; a person
approves, drops, or marks each feature "not built yet"; `rung draft runtime` then writes a drive
script for the approved features only, with one scenario per sub-feature.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from rung.analysis import survey
from rung.cli.commands.common import OK, ask, load_config
from rung.cli.output import Output
from rung.core import git
from rung.core.config import Config, ConfigError
from rung.drafting import feature_map, providers, runtime
from rung.engines import map_page

MAP_STYLE = {
    "proposed": ("PROPOSED", "blue"),
    "approved": ("approved", "green"),
    "not-built": ("not built", "dim"),
    "dropped": ("dropped", "dim"),
    "changed": ("CHANGED", "yellow"),
}
_CHOICES = {"a": "approved", "n": "not-built", "d": "dropped"}


def evidence_check_ids(cfg: Config) -> list[str]:
    return [c.id for c in cfg.checks if c.kind == "evidence" and c.get("run")]


def draft_map(cfg: Config, out: Output, *, model: str | None = None, intent: str = "") -> bool:
    """Draft or refresh the map from the code. True when a map that meets the contract was written."""
    provider = providers.get(replace(cfg.llm, model=model) if model else cfg.llm)
    info = survey.survey(cfg.root)
    refreshing = feature_map.load_state(cfg.root) is not None
    out.line(f"{'Refreshing' if refreshing else 'Drafting'} the feature map from the code with {cfg.llm.provider} ...")
    context = runtime.gather_context(cfg.root, info, runtime.app_focus(cfg.root, info))
    result = feature_map.draft(provider, cfg.root, info, context=context, intent=intent)
    out.line(f"{'OK' if result.ok else 'FAILED'} after {result.rounds} round(s): {result.message}")
    if result.notes:
        out.line("Notes from the model:")
        out.detail(result.notes, indent=2)
    return result.ok


def _first_sentence(text: str, limit: int = 110) -> str:
    sentence = " ".join(text.split()).split(". ")[0].rstrip(".")
    return sentence if len(sentence) <= limit else sentence[: limit - 3].rstrip() + "..."


def print_map(cfg: Config, out: Output, *, brief: bool = False) -> dict:
    """The map as a tree in the terminal; returns the same as data for `--json`.

    `brief` shows each feature in three lines (its status and title, what it does, its sub-feature IDs),
    enough to decide on it; the full map is in the feature files and on the map page.
    """
    state = feature_map.load_state(cfg.root) or feature_map.MapState()
    features = feature_map.load_features(cfg.root)
    drift = feature_map.drifted(cfg.root, state)
    rows = []
    out.line(out.style(f"Feature map ({feature_map.MAP_DIR.as_posix()}/)", "bold"))
    for slug, f in features.items():
        status = state.features.get(slug, {}).get("status", "proposed")
        shown = "changed" if slug in drift else status
        rows.append({"feature": slug, "title": f.title, "status": status, "changed": drift.get(slug, []),
                     "subfeatures": [i for i, _ in f.subfeatures], "entry_points": f.entry_points,
                     "problems": f.problems})  # fmt: skip
        out.line(f"\n{out.badge(shown, MAP_STYLE, 10)} {out.style(f.title or slug, 'bold')}  {out.style(slug, 'dim')}")
        if brief:
            out.detail(_first_sentence(f.summary), indent=11)
            out.detail(f"{len(f.subfeatures)} sub-features: " + ", ".join(i for i, _ in f.subfeatures), indent=11)
            continue
        out.detail(f.summary, indent=11)
        for sub_id, text in f.subfeatures:
            out.line(f"           - {out.style(sub_id, 'blue')} {text}")
        out.detail("reached by: " + "; ".join(f.entry_points), indent=11)
        for g in f.gotchas[:3]:
            out.detail(f"gotcha: {g}", indent=11)
        if slug in drift:
            out.detail("changed since approval: " + ", ".join(drift[slug][:4]), indent=11)
    for slug in state.with_status("not-built", "dropped"):
        if slug not in features:
            out.line(f"\n{out.badge(state.features[slug]['status'], MAP_STYLE, 10)} {slug}")
    return {"features": rows, "notes": state.notes}


def review(cfg: Config, out: Output) -> int:
    """Ask a person about each feature that needs a decision. Returns how many decisions were recorded."""
    state = feature_map.load_state(cfg.root)
    if state is None or not sys.stdin.isatty() or out.json_mode:
        return 0
    features = feature_map.load_features(cfg.root)
    drift = feature_map.drifted(cfg.root, state)
    pending = [s for s in features if state.features.get(s, {}).get("status", "proposed") == "proposed" or s in drift]
    if not pending:
        return 0
    who = git.user_name(cfg.root) or "a person"
    print("\nFor each feature: [a]pprove, [n]ot built yet, [d]rop, or Enter to decide later.", file=sys.stderr)
    decided = 0
    for slug in pending:
        title = features[slug].title or slug
        why = " (changed since approval)" if slug in drift else ""
        choice = ask(f"  {title}{why}", "").lower()[:1]
        if choice in _CHOICES:
            feature_map.set_status(cfg.root, state, slug, _CHOICES[choice], by=who)
            decided += 1
    state.save(cfg.root)
    return decided


def decide(cfg: Config, slugs: list[str], status: str, by: str) -> list[str]:
    state = feature_map.load_state(cfg.root)
    if state is None:
        raise ConfigError("there is no feature map yet; run `rung map` to draft one")
    features = feature_map.load_features(cfg.root)
    chosen = list(features) if slugs == ["all"] else slugs
    if unknown := [s for s in chosen if s not in features and s not in state.features]:
        raise ConfigError(f"no feature {', '.join(unknown)} in the map; features: {', '.join(features)}")
    for slug in chosen:
        if status == "approved" and features[slug].problems:
            raise ConfigError(f"{slug}.md does not meet the map contract: {features[slug].problems[0]}")
        feature_map.set_status(cfg.root, state, slug, status, by=by)
    state.save(cfg.root)
    return chosen


def next_step(cfg: Config) -> tuple[str, str]:
    state = feature_map.load_state(cfg.root)
    if state is None:
        return "rung map", "draft the feature map"
    if feature_map.drifted(cfg.root, state):
        return "rung map --redraft", "features changed since they were approved"
    if not state.with_status("approved"):
        return "rung map", "approve the features to drive"
    if not evidence_check_ids(cfg):
        return "rung draft runtime", "write the drive script for the approved features"
    return "rung check", "run the drive script"


def cmd_map(args, out: Output) -> int:
    cfg = load_config(args)
    by = args.by or git.user_name(cfg.root) or "a person"
    needs_draft = args.redraft or feature_map.load_state(cfg.root) is None
    if needs_draft and not draft_map(cfg, out, model=args.model, intent=args.intent or ""):
        out.emit({"ok": False})
        return 1
    for flag, status in ((args.approve, "approved"), (args.not_built, "not-built"), (args.drop, "dropped")):
        if flag:
            done = decide(cfg, flag, status, by)
            out.line(f"Marked {', '.join(done)} {status.replace('-', ' ')}.")
    out.line("")
    data = print_map(cfg, out, brief=args.brief)
    if review(cfg, out):
        out.line("")
        data = print_map(cfg, out, brief=True)
    page = map_page.write(cfg.root, check_ids=evidence_check_ids(cfg))
    cmd, why = next_step(cfg)
    out.emit({**data, "page": str(page.relative_to(cfg.root)), "next": {"command": cmd, "why": why}})
    out.line(f"\nMap page: {page.relative_to(cfg.root)}")
    out.line(f"{out.style('Next:', 'bold')} {out.style(cmd, 'blue')}  {out.style(why, 'dim')}")
    return OK


def has_map(root: Path) -> bool:
    return feature_map.load_state(root) is not None
