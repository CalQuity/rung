---
name: setup
description: >-
  Set up Rung in a repository and start using it: install the CLI, record the project's tests and
  security scans as checks, gate pull requests in CI, and teach coding agents the rules. Use when
  asked to "set up rung", "add rung to this repo", "verify this repo with rung", or when a repository
  needs a verification ledger for agent-written code.
---

# Set up Rung in a repository

Rung records what has been verified in a codebase and against which exact code; a result expires
when that code changes. Every verdict comes from a tool (tests, scanners, TLC, the Lean kernel), never
from a model. Your job here is to wire those tools up, show the developer what they found, and leave
the decisions that are theirs to them.

Run every command non-interactively (`--provider`, explicit flags): you cannot answer prompts.

## 1. Install and check the environment

```bash
rung --version || uv tool install rung-cli     # or: pipx install rung-cli
rung doctor                                    # which checkers, scanners and model CLIs exist
```

If `uv` is missing, tell the developer and stop; do not install system packages yourself. Scanners
(gitleaks, osv-scanner, zizmor) and Java/Lean are optional; note which are missing and how to install
them (`rung doctor` prints the commands), but continue without them.

## 2. Initialise

```bash
rung init --provider none
```

Use `--provider claude-cli` or `--provider codex-cli` instead only if the developer wants Rung to draft
TLA+ specs, Lean proofs or app-driving scripts with that tool (it reuses their existing login). `init`
records each detected test suite, the installed scanners and new-dependency vetting in `rung.toml`.

Open `rung.toml` and check it against the repository:
- Each test check's `run` command works from its `cwd`. Fix it if the project runs tests differently
  (read the README, Makefile, package.json scripts, CI config).
- `covers` lists the files the check depends on. Leaving it out covers the whole project (safe, but
  stales on any change); narrowing it is fine if the narrower set is honestly all the check depends on.

## 3. First run

```bash
rung check
rung status
```

Report the results to the developer. Then:
- **Failing tests:** report them. Do not change tests or code to make Rung pass unless asked.
- **Scanner findings on existing code:** these are real findings. Show the developer the summary
  (`rung report`). Only if they agree to accept today's findings as a starting point, run
  `rung baseline <check-id>` and commit `.rung/baselines/`. Never accept findings on your own.
- Never add waivers (`rung waive`); accepting a specific risk is the developer's call.

## 4. Gate pull requests

Add `.github/workflows/rung.yml` (adjust the branch and Python setup to the project):

```yaml
name: rung
on: pull_request
permissions:
  contents: read
  pull-requests: write   # only for the report comment
jobs:
  rung:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: CalQuity/rung@v0.1.4
```

Install whatever the checks need (language runtimes, scanners) in steps before `CalQuity/rung`.
Locally the equivalent is `rung check --changed`, which runs only what a branch touches and blocks only
what it introduces.

## 5. Teach the repository's agents

```bash
rung agents --hooks
```

This writes Claude Code skills (`rung`, `rung-tla`, `rung-lean`), an AGENTS.md section for Codex,
Cursor and others, and a Claude Code Stop hook that keeps an agent from finishing while checks covering
its edits are not freshly passing. The hook needs `rung` on the PATH Claude Code sees.

## 6. Optional: evidence and formal checks

Only where they pay off, and only with a model provider configured:
- **A user-facing app:** `rung draft runtime --intent "<one feature a user cares about>"`. Rung registers
  the generated drive script as a check only after it has run and left evidence. Add `--require .webm` for a video.
  Where a script cannot reach the feature (a login flow, a native app), an agent with a browser or
  computer-use tool (Claude in Chrome, Codex, computer use) can drive and record it instead: `rung add
  evidence <id> --covers <files> --require .gif` without `--run`, then `rung attest <id> <files> --by
  <tool> --note "<what was seen>"`. Attested evidence is not re-run, so it is weaker than a script.
- **Concurrency, queues, retries, leases:** `rung draft tla <file>`. A counterexample is a finding:
  show it to the developer as a possible bug. Never edit the spec or invariant to make it go away.
- **Small pure logic (pricing, permissions, parsers):** `rung draft lean <file>`. It writes a proven
  model and a differential test against the real code; a divergence is a finding for the developer.

Drafts propose laws in `LAWS.md` as `proposed`. Never approve a law yourself
(`rung laws --approve <id>` is for the developer, after reading it).

## 7. Commit

Commit `rung.toml`, `LAWS.md`, `.rung/verdicts/`, `.rung/baselines/` (if any), `.rung/verify/` (if
any), the CI workflow and the agent files. `.rung/evidence/` and `.rung/cache/` are local; add them to
`.gitignore` if they are not.

## Using Rung day to day

- Before saying a change is done: `rung check --changed`, and read the result.
- When fixing a bug: `rung prove '<test command>' --expect '<assertion text>'` shows the new test fails
  without the fix and passes with it.
- `rung status` (what is fresh, stale or failing), `rung laws` (which laws hold), `rung report`
  (everything; `--json` for machine use).
- Exit codes: 0 ok, 1 a check failed, 2 configuration error, 3 missing tool, 4 git error.
