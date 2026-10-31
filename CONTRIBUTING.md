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
| `src/rung/cli.py` | Commands; thin, delegates everything |
| `src/rung/config.py` | `rung.toml` load and save |
| `src/rung/checks.py` | Runs a declared check, records the verdict |
| `src/rung/runners/` | One module per checker: `tla`, `lean`, `drt` |
| `src/rung/ledger.py` | Content-addressed, hash-chained verdicts and freshness |
| `src/rung/laws.py` | `LAWS.md` parsing and law state |
| `src/rung/draft.py` | Propose, check, repair loop for model-drafted specs |
| `src/rung/providers.py` | Model providers (Claude Code CLI, Codex CLI, OpenAI-compatible) |
| `src/rung/survey.py`, `plan.py` | Repository survey and rung recommendations |
| `src/rung/skills.py` | The agent skill and AGENTS.md section |
| `examples/lease-queue` | The worked example, used by the integration test |

## Rules for changes

- Rung has no runtime dependencies. Keep it that way unless a dependency removes a
  real risk.
- A runner must never report `pass` when its tool did not actually confirm the result.
  When in doubt, report `error`.
- New file formats or fields are part of the public contract; document them in
  `docs/DESIGN.md` in the same change.
- Every runner change comes with a test that uses recorded tool output, so the suite
  runs without the tool installed.
