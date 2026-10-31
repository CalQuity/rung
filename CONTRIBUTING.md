# Contributing

```bash
uv sync
uv run pytest -q           # unit tests; the example integration test skips without TLC/Lean
uv run ruff format . && uv run ruff check .
```

To run the integration test locally, install Java 11+, put
[`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases) at
`~/.cache/rung/tla2tools.jar` (or set `RUNG_TLA2TOOLS`), and install Lean with
[elan](https://lean-lang.org/install/). `rung doctor` shows what is missing.

## Layout

| Path | What |
|---|---|
| `src/rung/cli/` | Argument parsing (`__init__.py`), commands (`commands.py`), terminal and JSON output |
| `src/rung/checks.py` | Selects checks (all, by id, touched by a diff), runs them, records verdicts |
| `src/rung/checkers/` | One class per check kind; `REGISTRY` maps kinds to checkers |
| `src/rung/engines/` | Tool wrappers with no knowledge of `rung.toml`: TLC, Lean, differential testing, SARIF |
| `src/rung/ledger.py` | Content-addressed, hash-chained verdicts and freshness |
| `src/rung/findings.py` | Baselines, waivers and triage of scan findings |
| `src/rung/gitutil.py` | Changed files, merge bases, worktrees, gitignore-aware globbing |
| `src/rung/prove.py` | Fail-first proofs for bug-fix tests |
| `src/rung/draft.py`, `runtime.py` | Model-drafted specs, models and drive scripts; a checker decides |
| `src/rung/providers.py` | Model providers (Claude Code CLI, Codex CLI, OpenAI-compatible) |
| `src/rung/agents.py` | Agent skill, AGENTS.md section, Stop hook |
| `src/rung/laws.py`, `report.py`, `survey.py`, `plan.py`, `presets.py` | Laws, reports, repo survey, recommendations, scanner presets |
| `examples/lease-queue` | The worked example, used by the integration test |

To add a check kind, subclass `rung.checkers.base.Checker`, register it in
`rung/checkers/__init__.py`, add the kind to `CHECK_KINDS` in `config.py`, and document its
fields in `docs/CONFIG.md`.

## Rules for changes

- Rung has no runtime dependencies. Keep it that way unless a dependency removes a
  real risk.
- A runner must never report `pass` when its tool did not actually confirm the result.
  When in doubt, report `error`.
- New file formats or fields are part of the public contract; document them in
  `docs/DESIGN.md` in the same change.
- Every runner change comes with a test that uses recorded tool output, so the suite
  runs without the tool installed.
