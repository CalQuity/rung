"""One structured report over checks, laws, new findings and proofs.

The same data renders as JSON (for agents and tools) or Markdown (for a pull
request comment). The Markdown starts with a marker so CI can update one
comment in place instead of posting a new one on every push.
"""

from __future__ import annotations

from typing import Any

from rung import checks, laws, ledger
from rung.config import Config

COMMENT_MARKER = "<!-- rung-report -->"
STATE_ICON = {"fresh": "✅", "stale": "🟡", "failing": "❌", "never": "⚪"}
LAW_ICON = {"held": "✅", "unproven": "🟡", "broken": "❌", "proposed": "📝", "unenforced": "⚪"}


def build(config: Config) -> dict[str, Any]:
    statuses = {s.check_id: s for s in checks.statuses(config)}
    entries = ledger.read(config.root)

    rows = []
    new_findings = []
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
        })  # fmt: skip
        for f in (latest.get("details") or {}).get("findings", []):
            if f.get("status") == "new":
                new_findings.append({"check": check.id, **f})

    law_rows = []
    for law in laws.load(config.root):
        state, why = laws.state(law, statuses)
        law_rows.append({"id": law.id, "text": law.text, "state": state, "why": why})

    latest_proofs: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("kind") == "proof":
            latest_proofs[e["check"]] = e
    proofs = [
        {"id": p["check"], "verdict": p["verdict"], "summary": p["summary"],
         "tests": p.get("details", {}).get("tests", [])}
        for p in latest_proofs.values()
    ]  # fmt: skip

    trusted = sum(1 for r in rows if r["state"] == "fresh")
    held = sum(1 for law in law_rows if law["state"] == "held")
    approved = sum(1 for law in law_rows if law["state"] != "proposed")
    return {
        "project": config.name,
        "checks": rows,
        "laws": law_rows,
        "new_findings": new_findings,
        "proofs": proofs,
        "ledger_intact": not ledger.verify_chain(entries),
        "summary": {
            "checks_fresh": trusted,
            "checks_total": len(rows),
            "laws_held": held,
            "laws_approved": approved,
            "new_findings": len(new_findings),
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
        f"**{s['new_findings']}** new findings",
        "",
    ]
    if not data["ledger_intact"]:
        out += ["> ❗ The evidence ledger's hash chain is broken; verdicts cannot be trusted.", ""]

    if data["laws"]:
        out += ["| | Law | Status |", "|---|---|---|"]
        for law in data["laws"]:
            out.append(f"| {LAW_ICON[law['state']]} | {law['text']} | {law['state']}: {law['why']} |")
        out.append("")

    out += ["| | Check | Kind | Result |", "|---|---|---|---|"]
    for row in data["checks"]:
        detail = row["summary"] or "never run"
        if row["state"] == "stale" and row["changed"]:
            detail += f" (stale: {', '.join(row['changed'][:3])})"
        out.append(f"| {STATE_ICON[row['state']]} | `{row['id']}` | {row['kind']} | {detail} |")
    out.append("")

    if data["new_findings"]:
        out += ["<details><summary>New findings</summary>", ""]
        for f in data["new_findings"][:50]:
            where = f"{f['path']}:{f['line']}" if f.get("line") else f.get("path", "")
            out.append(f"- **{f['level']}** `{f['rule']}` in `{where}` ({f['check']}): {f.get('message', '')[:200]}")
        out += ["", "</details>", ""]

    if data["proofs"]:
        out += ["**Fix proofs**", ""]
        for p in data["proofs"]:
            icon = "✅" if p["verdict"] == "pass" else "❌"
            out.append(f"- {icon} `{', '.join(p['tests'])}`: {p['summary']}")
        out.append("")
    return "\n".join(out)
