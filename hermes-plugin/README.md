# Rung for Hermes Agent

Coding agents say "done" when the code looks done. [Rung](https://github.com/CalQuity/rung) asks the
project's own tools instead. This plugin connects the two: before Hermes ends a turn in which it edited
code, Rung checks whether the tests, linters, type checkers, security scans and formal checks that cover
those files have a fresh passing verdict for the exact code on disk. If they do not, Hermes is sent back
to run them, fix what fails, and say plainly what is still unverified.

Every verdict comes from a tool (pytest, ruff, tsc, gitleaks, osv-scanner, TLC, the Lean kernel, a
script that drives the running app), never from a model. A verdict is tied to the code it checked and
expires when that code changes.

## Install

```bash
hermes plugins install rung          # from the plugin catalog
pip install 'rung-cli>=0.4,<0.5'     # into Hermes's environment, if the install did not add it
```

Then, in a repository, ask Hermes to "set up rung" (the bundled `rung:rung-setup` skill), or run
`rung init` yourself. The plugin does nothing in a folder without `rung.toml`.

## What it adds

| Surface | What it does |
|---|---|
| `pre_verify` hook | When a coding turn that edited files is about to end, asks Rung's ledger (without running anything) whether the checks covering the edits have fresh passing verdicts, and whether any changed path has no check at all. If not, Hermes continues with the check ids to run. At most two continuations per turn; Hermes's own `agent.max_verify_nudges` caps it as well. |
| `rung_check` tool | Runs `rung check --json` (all checks, the given ids, or `--changed`) and returns each verdict with its output. |
| `rung_plan` tool | Runs `rung plan --changed --json`: which checks cover the changed files, and which paths nothing covers. |
| `rung_kit` tool | Runs `rung kit --json`: every part of the verification kit, what is stale or missing, and how to add it. |
| `rung_map` tool | Drafts (or redrafts) and shows the feature map: the app's user-visible features, which Rung's app-driving scripts exercise once a person approves them. The model cannot approve features. |
| `/rung [kit\|plan\|check\|map]` | The same views from chat, as plain text. |
| `/rung approve\|not-built\|drop <ids>` | Your decisions on the feature map. They exist only as slash commands, so only a person makes them. |
| `rung:rung-setup` skill | Instructions for setting Rung up in a repository. |

## What it runs, and what leaves your machine

- **Shell commands.** The tools run the Rung CLI in the session's working folder. `rung_check` runs the
  commands recorded in that project's `rung.toml`, which are the project's own test, lint and build
  commands and any scanners you installed. Review `rung.toml` as you would a Makefile. The hook only
  reads Rung's local ledger.
- **Model calls.** `rung_map` drafts the feature map with the model CLI chosen in `rung.toml` (`claude`,
  `codex`, or an OpenAI-compatible endpoint you configure), which reads the project's source. With the
  provider set to `none`, nothing is drafted.
- **Network.** The plugin makes no network calls itself. The checks may: osv-scanner queries the OSV database,
  the `deps` check looks up new dependencies on PyPI or npm, and your test suite does whatever it does.
- **Files.** Rung writes verdicts and evidence under `.rung/` in the project. The plugin stores nothing.
- **Credentials and telemetry.** None. The plugin reads no credentials and sends no usage data.

## Requirements

Hermes Agent 0.20 or newer, Python 3.11 or newer, and `rung-cli` 0.4. Scanners (gitleaks, osv-scanner,
zizmor), Java with TLC, and Lean are optional; `rung doctor` lists what is missing and how to install it.

## License

Apache 2.0, the same as Rung.
