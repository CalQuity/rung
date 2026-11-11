---
name: rung-setup
description: >-
  Set up Rung's verification kit in a repository so Hermes can prove its code changes work: record the
  project's tests, linters and security scans as checks, run them, and report what they found. Use when
  asked to "set up rung", "verify this repo with rung", or when a repository has no rung.toml yet.
---

# Set up Rung in a repository

Rung records what has been verified in a codebase and against which exact code; a result expires when
that code changes. Every verdict comes from a tool (tests, scanners, TLC, the Lean kernel), never from a
model. Wire those tools up, show the developer what they found, and leave their decisions to them.

Commands cannot answer prompts here, so pass explicit flags and ask the developer first where a flag
needs their choice.

## 1. Install and check the environment

```bash
rung --version || uv tool install rung-cli     # or: pipx install rung-cli
rung doctor                                    # which checkers, scanners and model CLIs exist
```

If `uv` is missing, tell the developer and stop; do not install system packages yourself.

## 2. Initialise

Ask the developer which model, if any, should draft TLA+ specs, Lean proofs and app-driving scripts:
`claude-cli`, `codex-cli`, an `openai-compatible` endpoint they configure, or `none` for checks only.
Then run `rung init --provider <their choice> --no-agents` (the Rung plugin already gives Hermes the
verification step that `rung agents` installs for other coding agents).

Open `rung.toml` and check that each test check's `run` command works from its `cwd`; fix it if the
project runs its tests differently (read the README, Makefile, package.json scripts, CI config).

## 3. First run

Call the `rung_check` tool, then `rung_kit`, and report the results.

- **Failing tests:** report them. Do not change tests or code to make Rung pass unless asked.
- **Scanner findings on existing code:** show the developer the summary (`rung report`). Only if they
  agree to accept today's findings as a starting point, run `rung baseline <check-id>`.

## 4. Feature map (web apps)

For an app with a UI, call `rung_map` (pass `intent` to focus it) to draft the map of user-visible
features. Show the developer the map and ask them to decide each feature in chat with
`/rung approve <ids>` (or `all`), `/rung not-built <ids>` or `/rung drop <ids>`. Never decide features
yourself. Then `rung draft runtime` drafts an app-driving script for the approved features only, and
`rung_check` runs it with its scenarios and faults.

## 5. After that

When you finish a coding turn, the plugin asks Rung whether the checks covering your edits have fresh
passing verdicts. If not, it sends you back to run `rung_check`. Fix what fails; if a failure remains,
say so with the check ids instead of reporting the work as verified.
