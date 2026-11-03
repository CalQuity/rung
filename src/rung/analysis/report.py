"""One structured report over checks, laws, new findings and proofs.

The same data renders as JSON (for agents and tools) or Markdown (for a pull
request comment). The Markdown starts with a marker so CI can update one
comment in place instead of posting a new one on every push.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rung.analysis import formal
from rung.checks import runner
from rung.checks.command import read_result, reproduce_command
from rung.core import laws, ledger
from rung.core.config import Check, Config, ConfigError
from rung.engines import evidence_page as page

COMMENT_MARKER = "<!-- rung-report -->"
STATE_ICON = {"fresh": "✅", "stale": "🟡", "failing": "❌", "never": "⚪", "drifted": "🟠"}
LAW_ICON = {"held": "✅", "unproven": "🟡", "broken": "❌", "proposed": "📝", "unenforced": "⚪"}


def _runtime(check: Check, details: dict[str, Any]) -> dict[str, Any] | None:
    """What an evidence check's latest verdict says about its runs: seeds, outcomes, how to reproduce."""
    runs = details.get("runs")
    if check.kind != "evidence" or not isinstance(runs, list) or not runs:
        return None
    na: dict[str, str] = {}
    for r in runs:
        na.update(r.get("not_applicable") or {})
    return {
        "runs": [{"seed": r.get("seed"), "verdict": r.get("verdict"), "summary": r.get("summary", ""),
                  "scenarios": r.get("scenarios", []), "failed": r.get("failed", [])} for r in runs],
        "failing_seed": details.get("failing_seed"),
        "failing_scenario": details.get("failing_scenario", ""),
        "reproduce": details.get("reproduce", ""),
        "report": details.get("report", ""),
        "not_applicable": na,
    }  # fmt: skip


def build(config: Config, *, evidence_url: str = "") -> dict[str, Any]:
    statuses = {s.check_id: s for s in runner.statuses(config)}
    verdicts = ledger.read_all(config.root)

    rows = []
    new_findings = []
    below_threshold = 0
    for check in config.checks:
        s = statuses[check.id]
        latest = s.entry or {}
        rows.append({
            "id": check.id,
            "kind": check.kind,
            "state": s.state,
            "summary": latest.get("summary", ""),
            "at": latest.get("at", ""),
            "changed": s.changed,
            "runtime": _runtime(check, latest.get("details") or {}),
        })  # fmt: skip
        for f in (latest.get("details") or {}).get("findings", []):
            if f.get("status") == "new":
                new_findings.append({"check": check.id, **f})
            elif f.get("status") == "new-below-threshold":
                below_threshold += 1

    law_rows = []
    for law in laws.load(config.root):
        state, why = laws.state(law, statuses)
        law_rows.append({"id": law.id, "text": law.text, "harm": law.harm, "state": state, "why": why})

    proofs = []
    for p in (v for v in verdicts.values() if v.get("kind") == "proof"):
        # A proof is about specific bytes: once its tests or the fixed code change, it no longer holds.
        files = p.get("details", {}).get("files", [])
        current = ledger.inputs_digest(ledger.snapshot(config.root, files))
        stale = p["verdict"] == "pass" and current != p.get("inputs", {}).get("digest")
        proofs.append({
            "id": p["check"], "verdict": "stale" if stale else p["verdict"],
            "summary": "the tests or the fixed code changed since this proof" if stale else p["summary"],
            "tests": p.get("details", {}).get("tests", []),
        })  # fmt: skip

    trusted = sum(1 for r in rows if r["state"] == "fresh")
    held = sum(1 for law in law_rows if law["state"] == "held")
    approved = sum(1 for law in law_rows if law["state"] != "proposed")
    return {
        "project": config.name,
        "evidence_url": evidence_url,
        "checks": rows,
        "laws": law_rows,
        "new_findings": new_findings,
        "proofs": proofs,
        "formal": formal.rows(config, statuses),
        "ledger_intact": not any("invalid" in v for v in verdicts.values()),
        "summary": {
            "checks_fresh": trusted,
            "checks_total": len(rows),
            "laws_held": held,
            "laws_approved": approved,
            "new_findings": len(new_findings),
            "new_below_threshold": below_threshold,
        },
    }


def markdown(data: dict[str, Any]) -> str:
    s = data["summary"]
    out = [
        COMMENT_MARKER,
        f"### Rung · {data['project']}",
        "",
        f"**{s['checks_fresh']}/{s['checks_total']}** checks fresh · "
        f"**{s['laws_held']}/{s['laws_approved']}** approved laws held · "
        f"**{s['new_findings']}** new blocking findings"
        + (f" ({s['new_below_threshold']} more below the blocking level)" if s["new_below_threshold"] else ""),
        "",
    ]
    if not data["ledger_intact"]:
        out += ["> ❗ Some verdict files are corrupt or were edited by hand; those checks must be re-run.", ""]

    if data["laws"]:
        out += ["| | Law | Status |", "|---|---|---|"]
        for law in data["laws"]:
            harm = f"<br>_Harm: {law['harm']}_" if law.get("harm") else ""
            out.append(f"| {LAW_ICON[law['state']]} | {law['text']}{harm} | {law['state']}: {law['why']} |")
        out.append("")

    out += ["| | Check | Kind | Result |", "|---|---|---|---|"]
    for row in data["checks"]:
        detail = row["summary"] or "never run"
        if row["state"] == "stale" and row["changed"]:
            detail += f" (stale: {', '.join(row['changed'][:3])})"
        out.append(f"| {STATE_ICON[row['state']]} | `{row['id']}` | {row['kind']} | {detail} |")
    out.append("")

    runtime = [r for r in data["checks"] if r.get("runtime")]
    if runtime:
        out += ["**Runtime evidence**", ""]
        if data.get("evidence_url"):
            out += [f"[Download the recordings]({data['evidence_url']}) (screenshots, video, trace, HAR); "
                    "open each run's `index.html`.", ""]  # fmt: skip
        for row in runtime:
            rt = row["runtime"]
            seeds = " · ".join(f"{'✅' if r['verdict'] == 'pass' else '❌'} {r['seed']}" for r in rt["runs"])
            out.append(f"- `{row['id']}`: {len(rt['runs'])} run(s), seeds {seeds}")
            scenarios = rt["runs"][0]["scenarios"]
            if scenarios:
                listed = ", ".join(f"{'' if sc.get('passed') else '❌ '}{sc['name']} ({sc.get('category') or '?'})"
                                   for sc in scenarios)  # fmt: skip
                out.append(f"  - scenarios: {listed}")
            if rt["failing_seed"] is not None:
                where = f", scenario `{rt['failing_scenario']}`" if rt["failing_scenario"] else ""
                out.append(f"  - failing seed **{rt['failing_seed']}**{where}; reproduce with `{rt['reproduce']}`")
            for category, reason in rt["not_applicable"].items():
                out.append(f"  - {category}: not applicable ({reason})")
            if rt["report"]:
                out.append(f"  - evidence page: `{rt['report']}`")
        out.append("")

    if data["new_findings"]:
        out += ["<details><summary>New blocking findings</summary>", ""]
        for f in data["new_findings"][:50]:
            where = f"{f['path']}:{f['line']}" if f.get("line") else f.get("path", "")
            out.append(f"- **{f['level']}** `{f['rule']}` in `{where}` ({f['check']}): {f.get('message', '')[:200]}")
        out += ["", "</details>", ""]

    out += formal.markdown(data.get("formal", []))

    if data["proofs"]:
        out += ["**Fix proofs**", ""]
        for p in data["proofs"]:
            icon = {"pass": "✅", "stale": "🟡"}.get(p["verdict"], "❌")
            out.append(f"- {icon} `{', '.join(p['tests'])}`: {p['summary']}")
        out.append("")
    return "\n".join(out)


def evidence_page(config: Config, check: Check, *, seed: int | None = None, out: Path | None = None) -> Path:
    """Write the HTML page for `check`'s newest evidence run (or one seed of it) and return its path.

    Seeds and outcomes come from the verdict when it describes that run; a run that was not recorded
    (`rung check --seed`) is described from its own `result.json`.
    """
    root = config.root
    base = root / ".rung" / "evidence" / check.id
    # Names start with a timestamp to the second; the last write in a run (its page) breaks ties.
    runs_dirs = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: (d.name[:16], d.stat().st_mtime_ns)) \
        if base.is_dir() else []  # fmt: skip
    if not runs_dirs:
        raise ConfigError(f"no evidence recorded for {check.id!r} yet; run `rung check {check.id}`")
    newest = runs_dirs[-1]
    entry = ledger.read_all(root).get(check.id) or {}
    details = entry.get("details") or {}
    known = {r.get("seed"): r for r in details.get("runs", []) if isinstance(r, dict)}
    dirs: dict[int, Path] = {}
    for d in sorted(newest.glob("seed-*")):
        try:
            dirs[int(d.name.removeprefix("seed-"))] = d
        except ValueError:
            continue
    if seed is not None:
        if seed not in dirs:
            raise ConfigError(
                f"the newest run of {check.id!r} has no seed {seed} (it ran {', '.join(map(str, sorted(dirs)))}); "
                f"run `rung check {check.id} --seed {seed}`"
            )
        dirs = {seed: dirs[seed]}
    runs = []
    for s, d in dirs.items():
        recorded = known.get(s)
        if recorded and recorded.get("evidence_dir") == d.relative_to(root).as_posix():
            runs.append(recorded)
            continue
        result = read_result(d) or {}
        passed = result.get("passed")
        verdict = "pass" if passed is True else "fail" if passed is False else "unknown"
        runs.append({"seed": s, "verdict": verdict, "summary": "from the run's result.json (not recorded)"})
    failing = next((r for r in runs if r.get("verdict") == "fail"), None)
    reproduce = reproduce_command(check.id, failing["seed"]) if failing else ""
    target = out or (dirs[seed] if seed is not None else newest)
    return page.write(check.id, runs, dirs, target.resolve(), reproduce=reproduce)
