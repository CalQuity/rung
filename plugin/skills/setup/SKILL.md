---
name: setup
description: >-
  Set up Rung's verification kit in a repository: install the CLI, record the project's linters,
  tests and security scans as checks, gate pull requests in CI, and teach coding agents to verify
  their work. Use when asked to "set up rung", "add rung to this repo", "verify this repo with rung",
  or when a repository needs a way to prove agent-written code works.
---

# Set up Rung in a repository

Rung records what has been verified in a codebase and against which exact code; a result expires
when that code changes. Every verdict comes from a tool (tests, scanners, TLC, the Lean kernel), never
from a model. Your job here is to wire those tools up, show the developer what they found, and leave
the decisions that are theirs to them.

Run every command non-interactively (explicit flags such as `--provider`): you cannot answer prompts, so ask the developer first where a flag needs their choice.

## 1. Install and check the environment

```bash
rung --version || uv tool install rung-cli     # or: pipx install rung-cli
rung doctor                                    # which checkers, scanners and model CLIs exist
```

If `uv` is missing, tell the developer and stop; do not install system packages yourself. Scanners
(gitleaks, osv-scanner, zizmor) and Java/Lean are optional; note which are missing and how to install
them (`rung doctor` prints the commands), but continue without them.

## 2. Initialise

Ask the developer which model, if any, should draft TLA+ specs, Lean proofs, app-driving scripts and
explanations. `rung doctor` (step 1) shows which model CLIs are installed. The choices are
`claude-cli`, `codex-cli`, an `openai-compatible` endpoint they configure, or `none` for checks and
agent skills only. Do not pick one for them. Then run, with their answer:

```bash
rung init --provider <their choice>
```

Rung runs the model CLI they chose, and that CLI uses its own sign-in. For an `openai-compatible`
endpoint, Rung reads the key from the environment variable the developer names and sends it only to
that endpoint. Rung never stores a credential. `init` builds the project's verification kit: it records each detected test suite, linter
and type checker, the installed scanners and new-dependency vetting in `rung.toml`, and installs the
agent skills and the Stop hook (step 5; pass `--no-agents` if the developer does not want them). Run
without a terminal, it drafts nothing; `rung kit` then lists every part of the kit, what is missing,
and the command that adds it.

Open `rung.toml` and check it against the repository:
- Each test check's `run` command works from its `cwd`. Fix it if the project runs tests differently
  (read the README, Makefile, package.json scripts, CI config).
- `covers` lists the files the check depends on. Leaving it out covers the whole project (safe, but
  stales on any change); narrowing it is fine if the narrower set is honestly all the check depends on.

## 3. First run

```bash
rung check
rung kit
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
jobs:
  rung:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: CalQuity/rung@v0.3.0
```

Install whatever the checks need (language runtimes, scanners) in steps before `CalQuity/rung`.
Use `rung check --changed` for local iteration. With a build that provides `rung gate`, use
`rung gate --base origin/main` to make the same merge decision from the CLI. Protect the workflow
with repository-owner review. Keep PR jobs read-only and
free of secrets. Upload evidence only when the developer has chosen to share synthetic recordings.

## 5. Teach the repository's agents

`rung init` already did this unless it was run with `--no-agents`; otherwise:

```bash
rung agents --hooks
```

This installs shared `AGENTS.md` guidance and the Claude Code Stop hook. Run
`rung check --changed` and `rung report --format json` to drive and report verification.
Newer builds also install the Codex workflow and support `rung plan --changed --json`.
The hook needs `rung` on the PATH Claude Code sees.

## 6. Optional: evidence and formal checks

Only where they pay off, and only with a model provider configured. `rung kit` names the candidate
files for each part. With the developer's go-ahead, `rung init --provider claude-cli --yes` drafts every
part it found a candidate for in one go; or add them one at a time:
- **A user-facing app:** `rung draft runtime --intent "<one feature a user cares about>"`. Rung registers
  the generated drive script as a check only after it has run and left evidence. Add `--require .webm .zip` for a
  video and a Playwright trace, and set `runs = 3` on the check to run three seeds. Report any finding the draft
  prints (a scenario the app failed) to the developer; do not edit the script to make it pass.
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
