"""Render an offline score report. All project and checker content is escaped text."""

from __future__ import annotations

import base64
from html import escape
from importlib.resources import files
from typing import Any

MARKER = "<!-- rung-score-report-v1 -->"
CSS = """
:root{color-scheme:light;--ink:#0A0A0B;--muted:#5A5A5E;--line:#D9D9DB;--accent:#3A5C78}
*{box-sizing:border-box}body{margin:0;background:#FEFDFB;color:var(--ink);
font:16px/1.65 'Hanken Grotesk',Arial,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 24px 64px}h1{font-size:clamp(28px,5vw,44px);line-height:1.2;margin:8px 0}
h2{font-size:23px;margin:32px 0 12px}h3{font-size:18px;margin:0 0 8px}p{margin:8px 0 16px}
.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:12px;font-weight:750;color:var(--accent)}
.muted,small{color:var(--muted)}
.hero,.card,details,.notice{background:white;border:1px solid var(--line);border-radius:14px}
.brand{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);
padding:0 0 22px;margin-bottom:32px;gap:24px}.calquity-logo{width:170px;height:auto;display:block}
.brand span{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.hero{padding:28px;display:grid;grid-template-columns:2fr 1fr;gap:32px;margin:24px 0}
.hero h2{margin:0 0 12px;font-size:28px;line-height:1.3}.score-box{background:#F3F2EE;padding:20px;border-radius:10px}
.score{font-size:48px;font-weight:750;line-height:1.2;color:var(--accent)}.score small{font-size:20px;font-weight:500}
.scope{padding-top:16px;border-top:1px solid var(--line)}.scope li{margin:5px 0}
h1,summary,td{overflow-wrap:anywhere}.scope ul{padding-left:20px}.scope h3{font-size:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}.card{padding:18px}
.metric{font-size:30px;font-weight:750}.notice{padding:16px 20px;border-left:5px solid #bd7800}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;background:#e9eff3;font-size:13px;font-weight:700}
.fresh{background:#e6f1eb;color:#2E7D5B}.failing{background:#f9e9e5;color:#B4493C}
.review{background:#fff0d4;color:#765000}
.stale,.drifted{background:#fff0d4;color:#765000}code,pre{font:13px/1.6 ui-monospace,monospace}
code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:14px;border-radius:8px}
details{padding:16px 20px;margin:10px 0}summary{cursor:pointer;font-weight:650}summary .badge{margin-right:10px}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px 10px;
text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{color:var(--muted)}
.time-bar{height:6px;background:#F3F2EE;border-radius:4px;margin-top:7px;min-width:100px}
.time-bar span{display:block;height:100%;background:var(--accent);border-radius:4px}
footer{margin-top:36px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
@media(max-width:640px){main{padding:24px 14px}
.hero{grid-template-columns:1fr;padding:20px;gap:16px}.score{font-size:60px}}
@media print{body{background:white}main{max-width:none}.hero,.card,details{break-inside:avoid}summary{cursor:default}}
"""


def render(data: dict[str, Any]) -> str:
    """Return one self-contained HTML document with no active or remote content."""
    score, stats = data["score"], data["stats"]
    value = "Not scored" if score["value"] is None else f"{score['value']}<small>/100</small>"
    rows = data["checks"]
    attention = [r for r in rows if r["state"] != "fresh"]
    out = [
        "<!doctype html>",
        MARKER,
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; '
        "style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">",
        f"<title>{_text(data['project'])} · Rung report</title><style>{CSS}</style></head><body><main>",
        f'<div class="brand"><img class="calquity-logo" src="{_logo_data()}" alt="CalQuity">'
        "<span>Engineering · Rung</span></div>",
        '<header><span class="eyebrow">Verification report</span>',
        f'<h1>{_text(data["project"])}</h1><p class="muted">What was checked, what needs attention, '
        "and what has not been assessed.</p></header>",
        '<section class="hero" aria-label="Rung verification health"><div>',
        f"<h2>{stats['fresh']} of {stats['total']} configured checks passed</h2>",
        _outcome(data),
        "<p>These passing results still match the files they cover. They describe the checks you configured, "
        "not everything that could be tested.</p>",
        _gaps(data["assessments"]),
        '</div><aside class="score-box"><h3>Verification health</h3>',
        f'<div class="score">{value}</div><p>Rung score · configured-check health</p>',
        f"<p>{_text(score['limitation'])}</p><p><strong>Formula:</strong> {_text(score['formula'])}</p>",
        "<p>Every configured check has equal weight. Missing verification areas are shown below; "
        "they do not enter this score.</p></aside></section>",
    ]
    if not rows:
        out.append(
            '<p class="notice">No checks configured. Use rung init --dry-run to preview setup, '
            "or rung add to register a verification check.</p>"
        )
    if not data["ledger_intact"]:
        out.append('<p class="notice">The ledger contains corrupt or edited verdicts. Rerun the affected checks.</p>')
    mode = (
        "Configured checks executed for this report."
        if data["ran_checks"]
        else ("Saved-results view: no checks were executed. Freshness was evaluated when this page was generated.")
    )
    out += [
        f'<p class="muted">{mode} Generated {_text(data["generated_at"])}.</p>',
        '<div class="grid" aria-label="Check statistics">',
    ]
    for label, value in (
        ("Fresh passing", stats["fresh"]),
        ("Checks failed", stats["failed"] - stats["review_required"]),
        ("Review required", stats["review_required"]),
        ("Execution errors", stats["errors"]),
        ("Stale or model drift", stats["stale"] + stats["drifted"]),
        ("Never run", stats["never"]),
    ):
        out.append(f'<div class="card"><div class="muted">{label}</div><div class="metric">{value}</div></div>')
    out.append("</div>")
    out += _timing(data["timing"])
    out += [
        "<h2>Issues and next steps</h2>",
        "<p>A failed check needs investigation; it is not a count of confirmed bugs. "
        "Review-required items may be intentional exceptions, but still need a documented decision.</p>",
    ]
    out += _test_results(data)
    out += [_check(row, expanded=True) for row in attention]
    if rows and not attention:
        out.append("<p>All configured checks currently have fresh passing results. Review unassessed areas below.</p>")
    out += _findings(data)
    out += [
        "<h2>Verification areas</h2><p>Groups are inferred from check kinds, names and commands. "
        'A configured check does not establish complete coverage of an area.</p><div class="grid">'
    ]
    for category in data["categories"]:
        status = (
            f"{category['fresh_passing']} / {category['configured']} checks fresh and passing"
            if category["configured"]
            else "No check configured · Not assessed"
        )
        out.append(
            f'<article class="card"><h3>{_text(category["title"])}</h3><p>{status}</p>'
            f"<small>Checks for {_text(category['description'])}.</small>"
            + (
                f"<p><small>Configured: {_text(', '.join(category['checks']))}</small></p>"
                if category["checks"]
                else ""
            )
            + "</article>"
        )
    out += [
        '</div><h2>Code quality and test strength</h2><p class="notice">Not assessed as an overall grade. '
        "Static checks report their own rules; a passing test suite does not establish strong tests. "
        "This report does not calculate line coverage, a suite-wide mutation score or maintainability.</p>",
        "<h2>All check details</h2>",
    ]
    out += [_check(row) for row in rows]
    if data["laws"]:
        out += ["<h2>Project guarantees</h2><p>These are the laws declared in LAWS.md.</p><ul>"]
        out += [
            f"<li><strong>{_text(law['state'])}</strong> · {_text(law['text'])} ({_text(law['why'])})</li>"
            for law in data["laws"]
        ]
        out.append("</ul>")
    out += [
        f"<footer>Commit: {_text(data['head'] or 'Not available')}<br>"
        f"Check-set fingerprint: {_text(data['check_set_digest'])}<br>"
        "This page is a snapshot, not a live dashboard. Regenerate it after edits. "
        "Rung did not request AI drafting for this report.</footer></main></body></html>"
    ]
    return "\n".join(out)


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _logo_data() -> str:
    encoded = base64.b64encode(files("rung").joinpath("assets/calquity-logo-full.png").read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _outcome(data: dict[str, Any]) -> str:
    stats = data["stats"]
    if data["passed"]:
        return '<p><span class="badge fresh">All configured checks passed</span></p>'
    if stats["failed"] > stats["review_required"] or stats["errors"]:
        return (
            '<p><span class="badge failing">Action needed</span> '
            "Open the failed checks below before accepting this run.</p>"
        )
    return (
        '<p><span class="badge review">Review or rerun required</span> Some checks lack a current passing result.</p>'
    )


def _gaps(assessments: dict[str, str]) -> str:
    gaps = []
    for key, label in (
        ("static_analysis", "No linter or type checker configured"),
        ("secret_scanning", "Secret scanning: no dedicated scanner declared"),
        ("vulnerability_scanning", "Vulnerability scanning: no dedicated scanner declared"),
        ("runtime", "No browser or runtime verification configured"),
    ):
        if assessments[key] == "not_configured":
            gaps.append(label)
    gaps += ["Test strength: not assessed across the whole suite", "Line coverage: not measured by this report"]
    return (
        '<div class="scope"><h3>What this result does not establish</h3><ul>'
        + "".join(f"<li>{_text(gap)}</li>" for gap in gaps)
        + "</ul>"
        "<small>Scanner labels use declared tool presets. Review custom commands separately. "
        "Configured does not mean passed or comprehensive.</small></div>"
    )


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "Not measured"
    if 0 < seconds < 0.01:
        return "<0.01s"
    seconds = round(seconds, 2)
    return f"{int(seconds // 60)}m {seconds % 60:.2f}s" if seconds >= 60 else f"{seconds:.2f}s"


def _timing(timing: dict[str, Any]) -> list[str]:
    title = "Execution timing" if timing["mode"] == "current" else "Recorded check timings"
    out = [f"<h2>{title}</h2>"]
    if not timing["measured_checks"]:
        return [
            *out,
            "<p>No current timings available. Run the checks to record execution durations. "
            "Older receipts, stale results and external captures do not supply current timing metrics.</p>",
        ]
    out.append(
        f"<p>{timing['measured_checks']} checks timed; {timing['unmeasured_checks']} without current timing. "
        "Checker time measures the verification tool call, including its setup and subprocesses. "
        "It excludes Rung's input snapshots, ledger writes and HTML generation.</p>"
    )
    if timing["mode"] == "recorded":
        out.append(
            '<p class="notice">No checks ran for this page. These durations may come from different runs; '
            "their sum is not a single run's wall-clock time. Only results matching their current inputs "
            "contribute. Previous timings remain in stale check details.</p>"
        )
    else:
        out.append(
            "<p>Wall-clock run time includes the sequential checks and Rung's surrounding bookkeeping, "
            "up to report assembly. It excludes HTML generation and writing.</p>"
        )
    slowest = timing["slowest_check"]
    out.append('<div class="grid" aria-label="Execution timing metrics">')
    for label, value in (
        ("Wall-clock run time", _duration(timing["wall_seconds"])),
        ("Combined checker time", _duration(timing["total_check_seconds"])),
        ("Average per timed check", _duration(timing["average_seconds"])),
        ("Median per timed check", _duration(timing["median_seconds"])),
        ("Slowest check", f"{slowest['id']} · {_duration(slowest['seconds'])}"),
        ("Time in failed/error checks", _duration(timing["failed_check_seconds"])),
    ):
        out.append(f'<div class="card"><div class="muted">{label}</div><div class="metric">{_text(value)}</div></div>')
    out.append(
        '</div><div class="table-wrap"><table><thead><tr><th>Check, slowest first</th>'
        "<th>Result</th><th>Checker time</th><th>Share of measured time</th><th>Recorded</th>"
        "</tr></thead><tbody>"
    )
    for row in timing["checks"]:
        share = row["share_percent"]
        out.append(
            f"<tr><td>{_text(row['id'])}</td><td>{_text(row['verdict'])}</td>"
            f"<td>{_text(_duration(row['seconds']))}</td><td>{share:.1f}%"
            f'<div class="time-bar" aria-hidden="true"><span style="width:{share}%"></span></div>'
            f"</td><td>{_text(row['at'])}</td></tr>"
        )
    return [*out, "</tbody></table></div>"]


def _test_results(data: dict[str, Any]) -> list[str]:
    results = data["test_results"]
    if results is None:
        return ["<p>Current test-case counts are unavailable. Rerun a supported test suite to record them.</p>"]
    counts = " · ".join(
        f"{results[k]} {label}"
        for k, label in (("passed", "passed"), ("failed", "failed"), ("errors", "errors"), ("skipped", "skipped"))
    )
    metrics = data["test_metrics"]
    rate = "Not available" if metrics["pass_rate_percent"] is None else f"{metrics['pass_rate_percent']:.1f}%"
    skip = "Not available" if metrics["skip_rate_percent"] is None else f"{metrics['skip_rate_percent']:.1f}%"
    return [
        '<div class="grid" aria-label="Recorded test metrics">'
        '<div class="card"><div class="muted">Reported cases</div>'
        f'<div class="metric">{metrics["total_cases"]}</div></div>'
        '<div class="card"><div class="muted">Executed/error cases</div>'
        f'<div class="metric">{metrics["executed_cases"]}</div></div>'
        f'<div class="card"><div class="muted">Test pass rate</div><div class="metric">{rate}</div>'
        "<small>Among reported executed cases, excluding skipped cases. "
        "Failures and errors remain in the denominator.</small></div>"
        f'<div class="card"><div class="muted">Skip rate</div><div class="metric">{skip}</div>'
        "<small>Share of all reported cases. Skips are not counted as passes.</small></div></div>",
        f'<div class="notice"><strong>Recorded test results:</strong> {counts}'
        f"<br><small>From {results['suites']} reporting suite(s). Counts cover current structured reports only; "
        "overlapping suites can count a test more than once. Other runners are excluded.</small></div>",
    ]


def _check(row: dict[str, Any], *, expanded: bool = False) -> str:
    state = row["state"]
    label = (
        "Review required"
        if row["assessment"] == "review_required"
        else ("Could not run" if state == "failing" and row["verdict"] == "error" else state)
    )
    style = "review" if row["assessment"] == "review_required" else state
    out = [
        f'<details{" open" if expanded else ""}><summary><span class="badge {_text(style)}">'
        f"{_text(label)}</span>{_text(row['id'])}</summary>",
        f"<p>{_text(row['explanation'])}</p>",
    ]
    if row["summary"]:
        prefix = "Previous result: " if state in {"stale", "drifted"} else "Result: "
        out.append(f"<p>{prefix}{_text(row['summary'])}</p>")
    if row["changed"]:
        out.append(f"<p>Why it needs attention: {_text('; '.join(row['changed']))}</p>")
    if row["source"] == "external_capture":
        out.append("<p>External capture: recorded by a person or tool; not rerun by Rung.</p>")
    if row.get("implementation_link"):
        out.append(f"<p>Formal implementation link: {_text(row['implementation_link'])}</p>")
    out.append(f"<p><strong>Next step:</strong> {_text(row['next_step'])}</p>")
    if row["at"]:
        out.append(f'<p class="muted">Recorded {_text(row["at"])} · kind: {_text(row["kind"])}</p>')
    if row["elapsed_seconds"] is not None:
        out.append(f"<p>Checker execution time this run: {_text(_duration(row['elapsed_seconds']))}</p>")
    elif row["recorded_elapsed_seconds"] is not None:
        label = "Previous execution time" if state in {"stale", "drifted"} else "Recorded execution time"
        out.append(f"<p>{label}: {_text(_duration(row['recorded_elapsed_seconds']))}</p>")
    if results := row["test_results"]:
        prefix = "Previous test results" if state in {"stale", "drifted"} else "Test results"
        counts = " · ".join(f"{results[k]} {k}" for k in ("passed", "failed", "errors", "skipped"))
        out.append(f"<p>{prefix}: {_text(counts)}</p>")
    if runtime := row.get("runtime"):
        out.append("<h3>Runtime scenarios</h3><ul>")
        for run in runtime["runs"]:
            for scenario in run["scenarios"]:
                result = "passed" if scenario.get("passed") else "failed"
                out.append(f"<li>Seed {_text(run['seed'])} · {_text(scenario.get('name', ''))}: {result}</li>")
            out.extend(f"<li>{_text(failure)}</li>" for failure in run.get("failed", []))
        out.append("</ul>")
        if runtime["reproduce"]:
            out.append(f"<pre>{_text(runtime['reproduce'])}</pre>")
    if row["diagnostics"]:
        out.append(f"<pre>{_text(chr(10).join(map(str, row['diagnostics'])))}</pre>")
    out.append("</details>")
    return "\n".join(out)


def _findings(data: dict[str, Any]) -> list[str]:
    findings = data["new_findings"]
    if not findings:
        return ["<p>No new blocking scanner findings recorded. This does not mean security was fully assessed.</p>"]
    out = [
        "<h3>Findings to review</h3><p>These findings block their configured check. "
        "They require source-context review, not an automatic conclusion that the application is broken.</p>"
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Check</th><th>Location</th><th>Finding</th><th>Evidence</th></tr></thead><tbody>"
    ]
    for finding in findings:
        location = f"{finding.get('path', '') or finding.get('file', '')}:{finding.get('line', '')}"
        out.append(
            f"<tr><td>{_text(finding['check'])}</td><td>{_text(location)}</td>"
            f"<td>{_text(finding.get('message', ''))}<br><small>{_text(finding['explanation'])}</small></td>"
            f"<td>{'Current' if finding['current'] else 'Previous result; rerun required'}</td></tr>"
        )
    return [*out, "</tbody></table></div>"]
