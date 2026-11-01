# Contributing

```bash
uv sync
uv run pre-commit install    # ruff, ruff format and pyright on every commit, as in CI
uv run pytest -q --cov       # the example integration test skips without TLC/Lean
uv run pyright               # type check (editors read the same [tool.pyright] settings)
actionlint                   # workflows, if you change .github/ (CI also shellchecks action.yml)
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

## Releasing

1. Bump the version in `pyproject.toml`, `src/rung/__init__.py` and `.claude-plugin/plugin.json`, and the
   `CalQuity/rung@v…` references in `README.md` and `skills/setup/SKILL.md`.
   `tests/test_release_consistency.py` fails if any of them disagree.
2. Merge to `main` through a pull request.
3. A maintainer pushes the tag (`git tag -a vX.Y.Z -m "Rung X.Y.Z" && git push origin vX.Y.Z`); only
   admins can create `v*` tags. `release.yml` checks the tag matches the version, tests, and publishes
   `rung-cli` to PyPI through Trusted Publishing.

## The setup skill and plugin

`skills/setup/SKILL.md` is what developers give their coding agents to set Rung up; `.claude-plugin/`
publishes it as a Claude Code plugin (`/plugin install rung --marketplace CalQuity/rung`, then
`/rung:setup`). Validate changes with `claude plugin validate .`. The skills Rung installs *into* a
project (`rung agents`) live in `src/rung/guides/` instead.
