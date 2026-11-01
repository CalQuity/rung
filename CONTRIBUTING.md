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

Each layer imports only from the layers below it.

| Path | What |
|---|---|
| `src/rung/cli/` | Argument parsing, terminal/JSON output, and `commands/` (`setup`, `run`, `manage`, `draft`) |
| `src/rung/agents/` | Agent skills and the AGENTS.md section (`install`), the Claude Code Stop hook (`hooks`) |
| `src/rung/drafting/` | Model providers with structured output, and the TLA+, Lean and runtime drafters |
| `src/rung/guides/` | Markdown guidance used both as drafting prompts and as agent skills |
| `src/rung/analysis/` | Repository survey, recommended plan, report |
| `src/rung/checks/` | Check kinds (`registry` maps kinds to checkers), the `runner`, scan presets, baselines and waivers, fix proofs |
| `src/rung/engines/` | Tool wrappers with no knowledge of `rung.toml`: TLC, Lean, differential testing, SARIF |
| `src/rung/core/` | Config, ledger, laws, git, errors |
| `examples/lease-queue` | The worked example, used by the integration test |

To add a check kind, subclass `rung.checks.base.Checker`, register it in `rung/checks/registry.py`,
add the kind to `CHECK_KINDS` in `rung/core/config.py`, and document its fields in `docs/CONFIG.md`.
To change what models or agents are told, edit the Markdown in `src/rung/guides/`.

## Rules for changes

- Rung has no runtime dependencies. Keep it that way unless a dependency removes a
  real risk.
- A runner must never report `pass` when its tool did not actually confirm the result.
  When in doubt, report `error`.
- New file formats or fields are part of the public contract; document them in
  `docs/DESIGN.md` in the same change.
- Every runner change comes with a test that uses recorded tool output, so the suite
  runs without the tool installed.
