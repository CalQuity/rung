"""The feature map as one self-contained HTML page: what a person reviews and approves.

One card per feature: its status (proposed, approved, not built yet, dropped, changed since
approval), the behaviour in a paragraph, each sub-feature with its latest result from the drive
script (proven, failed, unreachable and why, or not driven yet) and its screenshots, every user entry
point, the gotchas and the source files it comes from. Like the evidence page, it has inline CSS and
relative links only: no script, no network.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from rung.drafting import feature_map
from rung.engines import evidence_page as ep

PAGE_NAME = "index.html"
CSS = (
    ep.CSS
    + """
.card{border:1px solid #d0d7de;border-radius:8px;padding:4px 18px 14px;margin:18px 0}
.proposed{background:#0969da}.approved{background:#1a7f37}.not-built{background:#59636e}.dropped{background:#59636e}
.changed{background:#9a6700}.sub{margin:10px 0 4px}.thumbs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 8px}
.thumbs img{height:220px;border:1px solid #d0d7de;border-radius:4px}
"""
)
LABELS = {"proposed": "PROPOSED", "approved": "APPROVED", "not-built": "NOT BUILT YET", "dropped": "DROPPED"}


def _inline(text: str) -> str:
    """Escaped text with the Markdown a map uses inline: `code` and **bold**."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)


def _block(text: str) -> str:
    """Paragraphs and `- ` bullet lists, each line rendered inline."""
    parts: list[str] = []
    bullets: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith(("- ", "* ")):
            bullets.append(f"<li>{_inline(line.lstrip()[2:])}</li>")
            continue
        if bullets:
            parts.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        if line.strip():
            parts.append(f"<p>{_inline(line.strip())}</p>")
    return "".join(parts)


def _latest_runs(root: Path, check_ids: list[str]) -> list[Path]:
    """Seed directories of the newest run of each drive-script check."""
    dirs: list[Path] = []
    for check_id in check_ids:
        base = root / ".rung" / "evidence" / check_id
        runs = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True) if base.is_dir() else []
        if runs:
            seeds = sorted(p for p in runs[0].iterdir() if p.is_dir() and p.name.startswith("seed-"))
            dirs += seeds or [runs[0]]
    return dirs


def results_by_id(run_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    """For each sub-feature ID: `state` (proven, failed, unreachable), a `note`, and its `images`."""
    out: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        result = ep._result(run_dir)
        files = sorted(p for p in run_dir.rglob("*") if p.is_file() and p.suffix.lower() in ep.IMAGES)
        for s in ep._dicts(result.get("scenarios")):
            ids = {str(s.get("name", ""))} | {str(c) for c in s.get("covers") or [] if isinstance(c, str)}
            assertions = ep._dicts(s.get("assertions"))
            passed = s.get("passed") if isinstance(s.get("passed"), bool) else all(
                a.get("passed") is True for a in assertions)  # fmt: skip
            failed = [ep._text(a.get("name", "")) for a in assertions if a.get("passed") is not True]
            for sub_id in ids - {""}:
                entry = out.setdefault(sub_id, {"state": "proven", "note": "", "images": []})
                if not passed:
                    entry["state"] = "failed"
                    entry["note"] = "; ".join(failed[:3])
                entry["images"] += ep._owned(s, run_dir, files)
        unreachable = result.get("unreachable")
        if isinstance(unreachable, dict):
            for sub_id, why in unreachable.items():
                out.setdefault(str(sub_id), {"state": "unreachable", "note": ep._text(why), "images": []})
    return out


def render(root: Path, page_dir: Path, *, check_ids: list[str]) -> str:
    state = feature_map.load_state(root) or feature_map.MapState()
    features = feature_map.load_features(root)
    drift = feature_map.drifted(root, state)
    results = results_by_id(_latest_runs(root, check_ids))
    counts: dict[str, int] = {}
    for slug in features:
        status = state.features.get(slug, {}).get("status", "proposed")
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join(f"{n} {LABELS.get(s, s).lower()}" for s, n in sorted(counts.items()))
    body = [
        "<h1>Feature map</h1>",
        f"<p>{len(features)} feature(s): {html.escape(summary or 'none')}. Approve, drop or mark a feature not built "
        "yet with <code>rung map</code>; only approved features are driven.</p>",
    ]
    if state.notes:
        body.append(f"<div class='repro'><b>Notes from the model</b>{_block(state.notes)}</div>")
    for slug, f in features.items():
        entry = state.features.get(slug, {})
        status = "changed" if slug in drift else entry.get("status", "proposed")
        label = "CHANGED SINCE APPROVAL" if slug in drift else LABELS.get(status, status.upper())
        body += [
            f"<div class='card' id='{html.escape(slug)}'><h2 style='border:0;margin-top:12px'>"
            f"{html.escape(f.title or slug)} "
            f"<span class='badge {html.escape(status)}'>{html.escape(label)}</span></h2>",
            _block(f.summary),
            "<h3>Sub-features</h3>",
        ]
        for sub_id, text in f.subfeatures:
            r = results.get(sub_id) or results.get(slug)  # `unreachable` may name the whole feature
            if r is None and entry.get("status") in ("not-built", "dropped"):
                r = {"state": entry["status"], "note": "", "images": []}
            badge = {"proven": "pass", "failed": "fail", "unreachable": "error"}.get(r["state"], "unknown") if r else \
                "unknown"  # fmt: skip
            word = LABELS.get(r["state"], r["state"].upper()) if r else "NOT DRIVEN YET"
            body.append(f"<div class='sub'><span class='badge {badge}'>{word}</span> <code>{html.escape(sub_id)}"
                        f"</code> {_inline(text)}</div>")  # fmt: skip
            if r and r["note"]:
                body.append(f"<div class='muted'>{html.escape(r['note'])}</div>")
            if r and r["images"]:
                body.append("<div class='thumbs'>" + "".join(
                    f"<a href='{ep._link(p, page_dir)}'><img src='{ep._link(p, page_dir)}' "
                    f"alt='{html.escape(ep.caption(p.name))}' title='{html.escape(ep.caption(p.name))}' loading='lazy'>"
                    "</a>" for p in r["images"][:6]) + "</div>")  # fmt: skip
        body += ["<h3>How a user gets to it</h3>", "<ul>"]
        body += [f"<li>{_inline(e)}</li>" for e in f.entry_points]
        body.append("</ul>")
        if f.gotchas:
            body += ["<h3>Gotchas</h3>", "<ul>", *[f"<li>{_inline(g)}</li>" for g in f.gotchas], "</ul>"]
        sources = entry.get("sources", [])
        if sources:
            body.append("<p class='muted'>Comes from: " + ", ".join(f"<code>{html.escape(s)}</code>" for s in sources)
                        + "</p>")  # fmt: skip
        if slug in drift:
            body.append("<p class='muted'>Changed since approval: " + html.escape(", ".join(drift[slug][:6])) + "</p>")
        body.append(f"<p class='muted'><a href='{html.escape(slug)}.md'>{html.escape(slug)}.md</a></p></div>")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Feature map</title><style>{CSS}</style></head><body>" + "\n".join(body) + "</body></html>\n"
    )


def write(root: Path, *, check_ids: list[str]) -> Path:
    page_dir = root / feature_map.MAP_DIR
    page_dir.mkdir(parents=True, exist_ok=True)
    path = page_dir / PAGE_NAME
    path.write_text(render(root, page_dir, check_ids=check_ids))
    return path
