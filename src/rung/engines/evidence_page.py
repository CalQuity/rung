"""A self-contained HTML page for one evidence run, so a person can see what the drive script saw.

The page shows each seed's result and assertions (expected against observed), the fault scenarios
it drove, its screenshots as a gallery in step order, the video, and links to the Playwright trace,
the HAR and the logs. Everything is relative links to files next to it and inline CSS: no script,
no fonts, no network, so it opens the same from a laptop, a CI artifact download or a file share.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
VIDEOS = (".webm", ".mp4")
PAGE_NAME = "index.html"

CSS = """
body{font:15px/1.5 system-ui,-apple-system,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#1f2328}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 8px;padding-top:16px;border-top:1px solid #d0d7de}
h3{font-size:16px;margin:20px 0 6px}.muted{color:#59636e}code{background:#f6f8fa;padding:1px 5px;border-radius:4px}
.badge{display:inline-block;font-weight:600;font-size:13px;padding:1px 9px;border-radius:12px;color:#fff}
.pass{background:#1a7f37}.fail{background:#cf222e}.error{background:#9a6700}.unknown{background:#59636e}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:14px}
th,td{text-align:left;vertical-align:top;border-bottom:1px solid #d0d7de;padding:6px 8px}th{background:#f6f8fa}
td.ok{color:#1a7f37;font-weight:600}td.no{color:#cf222e;font-weight:600}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
figure{margin:0;border:1px solid #d0d7de;border-radius:6px;overflow:hidden}
figure img{display:block;width:100%;height:auto}figcaption{padding:6px 10px;font-size:13px;background:#f6f8fa}
video{max-width:100%;border:1px solid #d0d7de;border-radius:6px}ul.files{padding-left:20px}
.repro{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px;margin:12px 0}
"""


def caption(name: str) -> str:
    """`03-coupon-applied-total-32.png` -> `3. coupon applied total 32`."""
    stem = Path(name).stem
    match = re.match(r"(\d+)[-_ .]*(.*)", stem)
    words = re.sub(r"[-_]+", " ", match.group(2) if match else stem).strip()
    if match:
        return f"{int(match.group(1))}. {words}" if words else f"Step {int(match.group(1))}"
    return words or name


def _link(target: Path, page_dir: Path) -> str:
    return quote(Path(os.path.relpath(target, page_dir)).as_posix())


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _result(run_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((run_dir / "result.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _assertions_table(assertions: list[dict[str, Any]]) -> list[str]:
    if not assertions:
        return ["<p class='muted'>No assertions recorded.</p>"]
    out = ["<table><tr><th></th><th>Assertion</th><th>Expected</th><th>Observed</th></tr>"]
    for a in assertions:
        ok = a.get("passed") is True
        out.append(
            f"<tr><td class='{'ok' if ok else 'no'}'>{'pass' if ok else 'FAIL'}</td>"
            f"<td>{html.escape(_text(a.get('name', a.get('assertion', ''))))}</td>"
            f"<td>{html.escape(_text(a.get('expected', '')))}</td>"
            f"<td>{html.escape(_text(a.get('observed', '')))}</td></tr>"
        )
    return [*out, "</table>"]


def _gallery(images: list[Path], page_dir: Path) -> list[str]:
    out = ["<div class='gallery'>"]
    for p in images:
        label = html.escape(caption(p.name))
        out.append(f"<figure><a href='{_link(p, page_dir)}'><img src='{_link(p, page_dir)}' alt='{label}' "
                   f"loading='lazy'></a><figcaption>{label}</figcaption></figure>")  # fmt: skip
    return [*out, "</div>"]


def _videos(videos: list[Path], page_dir: Path) -> list[str]:
    return [f"<p class='muted'>{html.escape(p.name)}</p><video controls preload='metadata' "
            f"src='{_link(p, page_dir)}'></video>" for p in videos]  # fmt: skip


def _owned(s: dict[str, Any], run_dir: Path, files: list[Path]) -> list[Path]:
    """A scenario's media: files in a directory named after it, and any it lists under `screenshots`."""
    name = str(s.get("name") or "")
    listed = s.get("screenshots")
    names = {str(x) for x in listed} if isinstance(listed, list) else set()
    owned = []
    for p in files:
        parts = p.relative_to(run_dir).parts
        if (name and len(parts) > 1 and parts[0] == name) or "/".join(parts) in names or p.name in names:
            owned.append(p)
    return owned


def _run_section(check_id: str, run: dict[str, Any], run_dir: Path, page_dir: Path) -> list[str]:
    seed = run.get("seed", "?")
    verdict = str(run.get("verdict", "unknown"))
    result = _result(run_dir)
    out = [
        f"<h2 id='seed-{html.escape(str(seed))}'>Seed {html.escape(str(seed))} "
        f"<span class='badge {html.escape(verdict)}'>{html.escape(verdict.upper())}</span></h2>",
        f"<p>{html.escape(str(run.get('summary', '')))}</p>",
    ]
    if feature := result.get("feature"):
        out.append(f"<p><b>Feature:</b> {html.escape(_text(feature))}</p>")
    out.append(f"<p class='muted'>Reproduce: <code>{html.escape(f'rung check {check_id} --seed {seed}')}</code></p>")
    files = sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name != PAGE_NAME)
    media = [p for p in files if p.suffix.lower() in (*IMAGES, *VIDEOS)]
    shown: set[Path] = set()
    scenarios = _dicts(result.get("scenarios"))
    if not scenarios and "assertions" in result:
        scenarios = [{"name": "", "faults": [], "assertions": result.get("assertions")}]
    for s in scenarios:
        faults = [_text(f) for f in s.get("faults") or []] if isinstance(s.get("faults"), list) else []
        name = _text(s.get("name") or "default")
        assertions = _dicts(s.get("assertions"))
        passed = (
            s.get("passed") if isinstance(s.get("passed"), bool) else all(a.get("passed") is True for a in assertions)
        )
        state = "pass" if passed else "fail"
        category = f" <span class='muted'>({html.escape(_text(s['category']))})</span>" if s.get("category") else ""
        out.append(
            f"<h3>Scenario: {html.escape(name)}{category} <span class='badge {state}'>{state.upper()}</span></h3>"
        )
        out.append(
            "<p><b>Faults injected:</b> " + html.escape("; ".join(faults)) + "</p>" if faults
            else "<p class='muted'>No faults injected.</p>"
        )  # fmt: skip
        out += _assertions_table(assertions)
        mine = [p for p in _owned(s, run_dir, media) if p not in shown]
        shown.update(mine)
        if images := [p for p in mine if p.suffix.lower() in IMAGES]:
            out += _gallery(images, page_dir)
        out += _videos([p for p in mine if p.suffix.lower() in VIDEOS], page_dir)
    na = result.get("not_applicable")
    if isinstance(na, dict) and na:
        out += ["<h3>Not applicable to this feature</h3>", "<ul>"]
        out += [f"<li><b>{html.escape(_text(k))}</b>: {html.escape(_text(v))}</li>" for k, v in na.items()]
        out.append("</ul>")
    rest = [p for p in media if p not in shown]
    if images := [p for p in rest if p.suffix.lower() in IMAGES]:
        out += ["<h3>Screenshots</h3>" if not shown else "<h3>Other screenshots</h3>", *_gallery(images, page_dir)]
    if videos := [p for p in rest if p.suffix.lower() in VIDEOS]:
        out += ["<h3>Video</h3>", *_videos(videos, page_dir)]
    others = [p for p in files if p not in media]
    if others:
        out += ["<h3>Files</h3>", "<ul class='files'>"]
        for p in others:
            note = ""
            if p.suffix.lower() == ".zip" and "trace" in p.name.lower():
                where = html.escape(Path(os.path.relpath(p, page_dir)).as_posix())
                note = (f" &mdash; Playwright trace: run <code>npx playwright show-trace {where}</code> in this folder,"
                        " or drop the file on trace.playwright.dev")  # fmt: skip
            elif p.suffix.lower() == ".har":
                note = " &mdash; network log (HAR): open it in the browser dev tools' Network panel"
            out.append(
                f"<li><a href='{_link(p, page_dir)}'>{html.escape(p.relative_to(run_dir).as_posix())}</a>{note}</li>"
            )
        out.append("</ul>")
    return out


def render(check_id: str, runs: list[dict[str, Any]], run_dirs: dict[int, Path], page_dir: Path, *,
           summary: str = "", reproduce: str = "") -> str:  # fmt: skip
    """The page for `runs` (each run's ledger details), with files read from `run_dirs` (seed -> directory)."""
    verdicts = [str(r.get("verdict", "unknown")) for r in runs]
    overall = "pass" if verdicts and all(v == "pass" for v in verdicts) else ("fail" if "fail" in verdicts else "error")
    seeds = ", ".join(f"<a href='#seed-{r.get('seed')}'>{html.escape(str(r.get('seed')))}</a> "
                      f"({html.escape(str(r.get('verdict', '?')))})" for r in runs)  # fmt: skip
    body = [
        f"<h1>Rung evidence: <code>{html.escape(check_id)}</code> "
        f"<span class='badge {overall}'>{overall.upper()}</span></h1>",
        f"<p>{html.escape(summary)}</p>" if summary else "",
        f"<p><b>Runs:</b> {seeds}</p>",
    ]
    if reproduce:
        body.append(f"<div class='repro'>Reproduce the failing run: <code>{html.escape(reproduce)}</code></div>")
    for run in runs:
        seed = run.get("seed")
        run_dir = run_dirs.get(seed) if isinstance(seed, int) else None
        if run_dir is not None:
            body += _run_section(check_id, run, run_dir, page_dir)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Rung evidence: {html.escape(check_id)}</title><style>{CSS}</style></head><body>"
        + "\n".join(b for b in body if b)
        + "</body></html>\n"
    )


def write(check_id: str, runs: list[dict[str, Any]], run_dirs: dict[int, Path], page_dir: Path, *,
          summary: str = "", reproduce: str = "") -> Path:  # fmt: skip
    page_dir.mkdir(parents=True, exist_ok=True)
    path = page_dir / PAGE_NAME
    path.write_text(render(check_id, runs, run_dirs, page_dir, summary=summary, reproduce=reproduce))
    return path
