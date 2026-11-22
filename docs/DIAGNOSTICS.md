# Reports, diagnostics, and failure-path workflows

Rung includes offline HTML health reports and opt-in pytest diagnostics that keep separate evidence without altering ordinary verdicts or merge gates.

## Plain-language HTML health report

```bash
rung score-my-code                    # run configured checks and write the report
rung score-my-code --no-run           # read saved results without executing checks
rung score-my-code --base origin/main # set the scanner comparison base
```

Open `.rung/cache/score/index.html`. The self-contained offline page (no external assets, escaped HTML, strict CSP) shows:

- Current passing checks, failures, execution errors, stale/drifted evidence, and next steps
- Review-required shortcut findings (separate from confirmed application defects)
- Checker execution timings, wall time, slowest checks, and structured pytest pass/skip metrics
- Verification areas that are **not configured** or **not assessed**

The Rung score is `100 * fresh passing checks / configured checks`, rounded. It measures configured-check health—not overall code quality, test strength, or coverage. Empty configurations are not scored.

Export outside the project with `--out /path/to/report.html` (in-project writes are restricted to `.rung/cache/score/`; symlinks and unrelated existing files are rejected). Use `--json` for structured output including `report_path`.

See [docs/DESIGN.md](DESIGN.md#configured-check-health-reports) for the full contract.

## Changed-line coverage and flaky-test diagnosis

These explicit diagnostics target an existing pytest `command` check ID from `rung.toml` (not a directory path) and store evidence under `.rung/evidence/diagnostics/`:

```bash
rung coverage tests --source src --base origin/main
rung coverage tests --source src --base origin/main --fail-under 90
rung flaky tests                         # 5 fresh processes, 600s budget
rung flaky tests --attempts 10 --budget 300 --fail-on-flaky
rung score-my-code --no-run              # view saved diagnostics in the HTML report
```

- **`rung coverage`** measures changed executable Python lines against the merge base (requires `pytest-cov` in that check's environment). Deleted lines, comments, and blanks are excluded; zero executable changes reports `N/A`, not 100%. Advisory by default; `--fail-under N` exits `1` below the threshold.
- **`rung flaky`** reruns the pytest command in fresh processes, retaining per-case and parameterized identities, lifecycle outcomes, skips/xfails, and grouped subtests. Consistent failures exit `1`; intermittent failures are advisory unless `--fail-on-flaky` is passed. Incomplete attempts, missing tools, or incompatible plugins (retries, random-order, cache selection, active xdist) exit `2`.

See [docs/DESIGN.md](DESIGN.md#explicit-pytest-diagnostics) for field definitions.

## Failure-path and issue-reporting agent skills

`rung agents` installs two additional skills for Claude Code and Codex alongside the core `rung` skill:

- **`rung-failure-path`:** guides agents to test failed writes, timeouts, retries, and post-failure state consistency with isolated fixtures, passing controls, and `rung prove` regression evidence. See [`examples/failure-path/`](../examples/failure-path/store.py) (`python -m unittest discover -s examples/failure-path -p 'test_*.py' -v`).
- **`rung-issue`:** guides agents to reproduce a confirmed finding against current code, check for duplicate GitHub issues, redact sensitive values, and draft or publish an issue only within the user's authorization.
