# Rung

**A verification kit for your project, built in one command, and a record of what it has actually
verified.** Rung is a command-line tool for codebases where coding agents write a lot of the code.
`rung init` reads the repo and assembles a kit from the best of each kind of check: the linters and
type checkers you already have, your tests, property tests that must catch planted bugs, scripts
that drive the running app through scenarios and faults with screenshots and video, security gates,
and formal models (TLA+, Lean) for the parts where a missed interleaving is expensive. It installs
skills that make your coding agent use the kit, and records every result in a ledger tied to the
exact bytes it checked. A result expires the moment those bytes change, and a pull request is
blocked only on what it introduces.

![Rung on the Acme Store example: rung init builds the kit, rung map drafts a feature map you approve, and the drive script finds a negative total and a double charge](https://raw.githubusercontent.com/CalQuity/rung/main/docs/assets/first-run.gif)

<sub>A minute on [`examples/acme-store`](examples/acme-store), a FastAPI and Next.js shop: `rung init`
builds the kit, `rung map` drafts a feature map you approve, and the drive script it writes finds a
coupon that makes the total negative and a double-click that charges the card twice. The model's
drafting steps take a few minutes and are skipped in the recording; every output shown is real.</sub>

What a pass means depends on the check: a test suite proves what its tests assert, TLC proves an
invariant over every state of a model, a Lean proof holds for the model it is about. Rung makes
each of those explicit and keeps them current; it cannot make a weak test strong.

```console
$ rung check --changed
PASS  tests (command): `pytest -q` exited 0
FAIL  secrets (scan): 1 new finding(s) at or above `warning`
      NEW error   slack-bot-token  slack.py:1  [7a9fe0a79cbca79f]
FAIL  deps (deps): 1 missing, 0 younger than 30 days
      MISSING pypi:requests-toolbelt-turbo-pro: not on the registry (hallucinated or private?)
PASS  runtime (evidence): 3 artifact(s) recorded
FAIL  lease-protocol (tla): invariant ResultFromLeaseHolder is violated
      counterexample (5 states):
        2. Claim      owner = w1, valid = TRUE
        3. Expire     valid = FALSE
        4. Claim      owner = w2, valid = TRUE
        5. Complete   result = w1, done = TRUE
```

## Why

Agents write more code than people can read, and "the build passed" says little about whether
a change does what was meant. Rung's answer is three habits.

1. **Evidence over assertion.** A verdict is something a machine checked: a test run, a
   scanner, a recording of the real app, TLC, the Lean kernel. Rung never takes a model's word.
2. **Verdicts expire.** Each verdict stores content hashes of every file it covered. Change one
   byte and it is stale. Nobody has to remember to re-run anything.
3. **Gate on what changed.** On a branch, only problems the branch *introduces* block. Old
   findings are compared with the merge base, or accepted once with `rung baseline`.

## Quick start

```bash
uv tool install rung-cli          # or: pipx install rung-cli  (the command is `rung`)
cd your-repo
rung init                         # build the kit: checks, agent skills and hook, drafted scenarios and models
rung check                        # run everything and record verdicts
rung kit                          # every part of the kit, how it stands, and the next command
```

To try it on an app with a few bugs planted for it to find, run the same commands in
[`examples/acme-store`](examples/acme-store).

`rung init` asks before each part a model drafts and shows the candidate files it found, so you
choose what gets modelled. `rung init --yes` drafts every part it can without asking;
`--no-drafts` sets up the checks and agent skills only. Run it again later to fill the parts still
missing.

Or let your coding agent do the setup. Rung ships a skill that installs the CLI, records your tests
and scans, adds the CI gate and installs the agent rules, while leaving the decisions that are yours
(accepting existing findings, approving laws) to you:

```text
# Claude Code (2.1.275+), inside a session:
/plugin marketplace add CalQuity/rung
/plugin install rung@rung
/rung:setup

# Codex and other agents that read .agents/skills:
mkdir -p ~/.agents/skills/rung-setup && curl -fsSL \
  https://raw.githubusercontent.com/CalQuity/rung/main/plugin/skills/setup/SKILL.md \
  | sed 's/^name: setup$/name: rung-setup/' > ~/.agents/skills/rung-setup/SKILL.md
```

Each drafted part can also be added on its own, for example a script that drives one feature:

```bash
rung draft runtime --intent "create, list and delete a todo through the HTTP API"
```

## Your project's verification kit

Each part catches what the others miss, and `rung kit` shows them side by side:

| Part | What it catches | Built from |
|---|---|---|
| Linters and type checkers | Mistakes a tool sees without running anything | The ones the project already configures, plus `shortcuts`, which blocks new ways of silencing them |
| Tests | Regressions in the behaviour tests pin down | The project's own suites; `rung prove` shows a bug fix's test catches the bug |
| Property tests | Inputs nobody wrote a test for | Drafted by a model, kept only if they catch planted bugs |
| Security gates | Leaked secrets, vulnerable or invented packages, risky CI | gitleaks, osv-scanner, zizmor, semgrep, `deps` |
| Driving the app | What a user sees, under faults and odd inputs | A drafted script: seeded scenarios in six categories, screenshots, video, traces, an evidence page |
| Formal models | Interleavings and edge cases tests rarely reach | TLA+ for stateful code, Lean for a pure core; they report when the code drifts from the model |
| Agent skills and hook | An agent finishing work its checks do not back | Skills, AGENTS.md rules and a Claude Code Stop hook |

On a small demo shop (a checkout page with a coupon box), `rung init --yes` produced this kit. The drafted
property tests and the Lean model both caught the shop's real bug, a coupon larger than the cart
giving a negative total, and the property tests were left for a person to judge rather than
registered:

```console
$ rung kit
Verification kit

ATTENTION  Linters and type checkers  catches the mistakes a tool can see without running anything
           never run  shortcuts

ATTENTION  Tests  catches regressions in the behaviour the tests pin down
           never run  tests

missing    Property tests  catches inputs nobody thought to write a test for; each must catch planted bugs
           Property tests for shop/pricing.py, kept only if they catch planted bugs
           $ rung draft property shop/pricing.py

ATTENTION  Security gates  catches leaked secrets, vulnerable or invented packages, risky CI
           never run  secrets
           never run  vulnerable-deps
           never run  deps

missing    Driving the app  catches what a user sees, under faults and odd inputs, with the evidence to show it
           Drive the web UI through scenarios and faults, recording screenshots and video
           $ rung draft runtime

ATTENTION  Formal models  catches interleavings and edge cases tests rarely reach, checked exhaustively
           never run  pricing-lean
           never run  pricing-lean-drt

ok         Agent skills and hook  catches an agent finishing work its checks do not back
           installed  rung skills + AGENTS.md
           installed  verify-pricing-lean  project-specific verification skill
           installed  Stop hook  blocks an agent finishing with stale checks

Laws (LAWS.md): 4 proposed

Next: rung check  7 check(s) stale or never run: shortcuts, tests, secrets, vulnerable-deps ...
```

`rung kit` always ends with the one command to run next: a failing check first, then a formal model
whose code changed, then stale checks, then the missing parts and the laws waiting for approval.

## What it checks

| Concern | Check | What blocks |
|---|---|---|
| Tests | `command` | The suite fails |
| Bug fixes | `rung prove` | The new test passes *without* the fix, so it proves nothing |
| Shortcuts to green | `shortcuts` | A change adds `@ts-ignore`, `# type: ignore`, `as any`, a lint disable, a skipped, focused or deleted test, a lowered coverage threshold or a relaxed type-checker setting |
| Type checking and lint | `command` | The project's own `tsc`, pyright, mypy, ruff, ESLint, `go vet` or Clippy fails (`rung init` records the ones it finds configured) |
| Laws on real code | `rung draft property` | A Hypothesis or fast-check property fails, or the properties catch fewer than half the deliberate bugs Rung plants |
| Real behaviour (UI, API, CLI) | `evidence` | The drive script fails or records no evidence; `require = [".webm"]` demands a video. Without a script, an agent's browser or computer-use recording, attested with `rung attest`, holds until the code changes |
| Secrets | `scan` (gitleaks) | A new secret appears |
| Vulnerable dependencies | `scan` (osv-scanner) | A new known vulnerability appears |
| Invented or brand-new packages | `deps` | A new package is missing from PyPI/npm, or is under 30 days old |
| CI workflows | `scan` (zizmor) | A new insecure pattern appears in a workflow |
| Code patterns | `scan` (semgrep, opengrep, trivy, any SARIF tool) | A new finding at or above `fail_on` |
| Concurrency and protocols | `tla` | TLC finds an interleaving that breaks an invariant |
| Core logic | `lean` + `drt` | A proof fails or cheats, or the code disagrees with the proven model |

See [docs/RECIPES.md](https://github.com/CalQuity/rung/blob/main/docs/RECIPES.md) for APIs, web apps, libraries, job queues and monorepos.

## Laws

`LAWS.md` lists what must never break, one sentence each, and the checks that enforce it:

```markdown
## refund-bounded
A refund never exceeds the original charge, even under concurrent retries.
- enforced by: refund-protocol, refund-model, tests
- status: approved
```

`rung laws` shows whether each law currently **holds**: approved, enforced, and every enforcing
verdict fresh. Agents may propose laws; only people approve them.

## Driving the app

Driving the app starts with a **feature map** you approve. `rung map` has the model read the code
(routes, templates, commands, docs) and write `.rung/verify/features/`: an index with how to launch
the app for verification, the driving conventions and the proof rules, and one file per user-facing
feature with its sub-features (each with a short ID), every way a user reaches it, the driving
recipe (preconditions, then each action paired with its observable result) and the gotchas. Rung
checks every file against that shape and sends a map that breaks it back to the model.

You then decide feature by feature: approve it, mark it **not built yet** (a disabled button, a
stubbed route: never driven), or drop it. `rung map` shows the map in the terminal and writes
`.rung/verify/features/index.html`, one page with a card per feature; once a script has run, each
sub-feature on it shows its latest result (proven, failed, unreachable and why) and its screenshots.

```console
$ rung map --approve coupons --not-built pay
$ rung draft runtime --require .png
```

`rung draft runtime` then writes a script for the approved features only, registered as a check
once it has worked against the real app:

- **`.rung/verify/drive.py`**, a script that starts the app on a free port, drives the approved
  features the way a user does (Playwright for web UIs, HTTP or CLI calls otherwise) and asserts on
  what a user would see. Each scenario is named after the sub-feature ID it proves, and every approved
  sub-feature must have one, or be reported as unreachable with the precondition the run lacked. It
  never calls internal functions or test-only endpoints. The model is shown the routes the app really
  serves, found in its code (FastAPI, Starlette and Flask decorators, Express, Hono and Koa handlers,
  Next.js app and pages routers), and drives only those.
- **A map that cannot drift silently.** Approval pins a digest of each feature's file and the code it
  comes from. When either changes, `rung kit` and `rung draft runtime` say so, and `rung map
  --redraft` refreshes the map: unchanged features keep their approval, changed ones wait for yours
  again, and a "not built yet" or "drop" outlives the refresh.
- **A project verification skill** (`.claude/skills/verify-<project>/`) that tells the project's
  coding agents to drive the app after any user-facing change, refresh the feature map when they add
  a feature, and treat a failure as a product bug rather than weaken the script.
- **Scenarios, not just the happy path.** For each feature the script drives named scenarios in six
  categories: `happy`, `input` (empty, very long, unicode, case and spacing variants, boundary values,
  invalid formats, injection-looking strings), `state` (repeats, empty carts, stale state, refresh
  after success, deep links), `auth` (signed out, wrong user, bad token), `fault` and `robustness`
  (keyboard only, no console errors or uncaught exceptions, no 5xx). A category that does not apply
  needs a stated reason, and Rung rejects a script that drives only the happy path. A scenario the
  real app fails while drafting is a finding for you, never sent back to the model to make it pass.
- **Seeds that reproduce a failure exactly.** Rung passes `RUNG_SEED` and the script derives every
  choice from it: which inputs to try, which backend call to delay and for how long, when to reload.
  `runs = 3` runs seeds `seed`, `seed+1`, `seed+2`, each in its own directory; a failure names its
  seed and prints `rung check runtime --seed N`, which runs that seed again. While drafting, Rung runs
  the same seed twice and sends the script back if the outcomes differ.
- **Fault scenarios.** `RUNG_FAULTS` names the adverse conditions to drive: slow or failing backend
  calls and going offline (Playwright `context.route`, `set_offline`), double submits, reload or back
  mid-flow, killing and restarting the app on the same data, a fake clock for expiry, a dependency on
  a dead port. Under each, the script asserts that nothing happened twice, no state was lost, and the
  user saw an error message rather than a stack trace.
- **Recordings you can replay and share.** Every run writes its evidence to a fresh directory: a
  video (`--require .webm`), a Playwright trace (`--require .zip`; open it with
  `npx playwright show-trace trace.zip` or at trace.playwright.dev), a HAR network log (`.har`),
  screenshots named after the step they prove, a transcript and a structured `result.json`. Their
  hashes go into the verdict, which stays valid only while the code it covers is unchanged. Each run
  also gets an `index.html` evidence page (`rung evidence runtime` rewrites it): the result, each
  scenario's faults and assertions (expected against observed), its screenshots in step order, the
  video, links to the trace and HAR, and the command that reproduces a failure. It is one file with
  relative links and no network access, so it opens from a laptop or a CI artifact download alike.

Where a script cannot reach a feature (a login flow, a native app), an agent can drive it with its
own browser or computer-use tool and record it with `rung attest <check> <files> --by <tool>`. Such
evidence is recorded with who captured it and is never re-run, so it is weaker than a script.

## Property tests that are tested themselves

`rung draft property pricing.py --intent "a total is never negative and equals the discounted sum"`
has the model write Hypothesis (Python) or fast-check (TypeScript) properties for the laws, then:

- runs them on the real code. A property that fails is a finding for you, never sent back to the
  model to make it pass. So is any failure Rung cannot pin on the test's own code;
- breaks the code on purpose, one token at a time (`<` to `<=`, `+` to `-`, `and` to `or`), and
  runs them again. Only a property failure counts as catching a bug; one that breaks the build or
  times out is not counted. Properties that catch fewer than half go back to the model with the
  bugs they missed. If no bug can be planted at all, the result says INCONCLUSIVE.

All of this runs in a disposable copy of the project, without credential-looking environment
variables, so your files are never mutated. That limits accidents; it is not a security sandbox,
so read a drafted test before trusting it. The accepted test is written at the end (never over an
existing file); a failed draft is kept in `.rung/cache/drafts/`. The result is a `command` check
covering the project, and proposed laws in `LAWS.md`.

## Formal methods where they pay

TLA+ and Lean are not for every file. Rung recommends them where the survey finds code that
needs them, and the model drafts the first version.

- **`rung draft tla queue.py`** writes a TLA+ model whose actions copy the code's real guards,
  then runs TLC. A counterexample stops the draft and goes to you. The model never gets to
  edit the spec until the checker is quiet. When the code calls database functions
  (`supabase.rpc("claim_job")`, `select claim_job(...)` in SQL strings), Rung adds the `*.sql`
  files that define them to the prompt, oldest definition first, and covers them with the check;
  a call whose definition it cannot find is a warning, because the model then has to assume the
  call is atomic.
- **`rung draft lean pricing.py`** writes a small Lean model of the intended behaviour, proves
  its laws, and sets up a differential test that holds the real code to it on random inputs.
  Lean checks pass only when `#print axioms` shows no `sorry`, no `native_decide` and no
  invented axioms. It is shown the same SQL definitions.

These are the formal-methods part of the project's verification kit, and Rung keeps them honest
after the draft:

- **Models cannot silently go stale.** Each `tla` and `lean` check records the commit and a digest
  of the code its model was last confirmed to describe (`.rung/formal/<check>.json`). When that code
  changes, `rung check` does not rerun TLC on the old spec and call it a pass: it reports that the
  code changed since the model was confirmed, names the files, and the laws it enforces show as
  UNPROVEN. `rung maintain <check>` shows the model the diff and commit messages, has it classify
  each change (no effect, the model must change, the promise changed on purpose with cited
  evidence, or the promise may be broken), updates the model from the code, and rechecks it:
  **in sync**, **updated**, **bug** or **blocked**. An invariant or theorem is never weakened
  without cited evidence. `rung confirm <check> --by NAME --reason TEXT` records a person's review.
- **Shown able to fail.** After a TLA+ spec passes, Rung removes one action guard at a time and
  runs TLC on each mutant; at least one must break an invariant, or the invariants are too weak to
  catch anything and the spec goes back to the model. The score is shown with every check.
- **Readable.** A counterexample reads as numbered steps in plain words ("a customer clicks
  Pay"), with the invariant it breaks and the harm, next to the raw trace, and is drawn as a
  Mermaid state diagram. Each check shows its scope: the bounds, the assumptions and what it does
  not cover. Laws carry a `harm:` line.
- **A skill per model.** `.claude/skills/verify-<check>/SKILL.md` tells an agent what the model
  promises, the files and functions it covers, its bounds, how to replay a counterexample against
  the code, when to run `rung maintain`, and the confirmed commit.

```console
$ rung check payments-tla
ERROR payments-tla (tla): the code changed since the model was confirmed at 29b079f; the model may no longer describe it
      changed: shop/payments.py
      next: rung maintain payments-tla
$ rung maintain payments-tla
UPDATED payments-tla: the model was updated to describe the code (spec/tla/payments/Checkout.tla) and it checks
  [model-must-change] shop/payments.py:12-16 checkout now holds a per-order lock across the check, charge and mark paid
  fail-ability: 4/5 guard mutants caught by an invariant
```

Every drafted file (spec, adapter, drive script, property test) is sent back to the model if it
hard-codes a home or temporary directory path (`/Users/<name>/`, `/home/<name>/`, `C:\Users\`,
`/tmp`), because it would run on one machine only.

[`examples/lease-queue`](https://github.com/CalQuity/rung/tree/main/examples/lease-queue) shows both catching a real bug, plus a
three-step case only the differential test finds.

## In CI

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write # only for the report comment; without it the gate still works
jobs:
  rung:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: CalQuity/rung@v0.3.0
```

The action runs the checks a pull request touches against its merge base. It keeps a single
report comment up to date and fails only on what the pull request introduced. The pull request's
own `rung.toml`, `LAWS.md`, waivers, baselines and scanner configs decide what it is checked
against, so the action lists any edits to them in the report and as annotations (add other policy
files, such as a semgrep rules file, with `policy-paths`); set
`policy-changes: fail` to fail the gate on them until a reviewer accepts. It uploads `.rung/evidence/`
(screenshots, videos, traces, HARs and each run's `index.html`) as the `rung-evidence` workflow
artifact and links it from the report comment and the step summary, so a reviewer can watch what the
drive script did; set `upload-evidence: false` to skip it. Without the action, use
`rung check --changed` and `rung report`.

To run the same check before every push, add Rung to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/CalQuity/rung
  rev: v0.3.0
  hooks:
    - id: rung-check
```

## With coding agents

`rung agents` writes a Claude Code skill and an `AGENTS.md` section, which Codex, Cursor and
others read. With `--hooks` it also installs a Claude Code `Stop` hook. An agent whose edits
touch a check that is not freshly passing is told which `rung check` to run, and cannot finish
until it has. This is a best-effort local guard: it lets the agent stop on a second attempt (so it
can never trap one), allows the stop if Rung itself cannot run, and looks at everything changed since the branch
left its base, committed or not.
CI with `rung check --changed` is the enforcement boundary.

Drafting uses a model you already have. `rung init` reuses your `claude` or `codex` login, or
any OpenAI-compatible endpoint, including local models. Rung never stores credentials.

## Commands

| Command | Does |
|---|---|
| `rung init [--yes] [--no-drafts] [--no-agents]` | Build the verification kit: survey the repo, add checks, install agent skills and the Stop hook, draft app scenarios, property tests and formal models with your approval |
| `rung kit` | Every part of the kit, its checks and their state, and the next command to run |
| `rung map [--approve F] [--not-built F] [--drop F] [--redraft]` | Draft the feature map, show it (terminal and HTML page), record your decision on each feature |
| `rung plan` | Recommend checks, cheapest and most useful first |
| `rung check [ids] [--changed] [--base REF]` | Run checks and record verdicts |
| `rung check <id> --seed N [--scenario NAME]` | Run one seed (or scenario) of a drive script again, without recording it |
| `rung evidence <check> [--seed N] [--out DIR]` | Write the HTML evidence page of a drive script's newest run |
| `rung status [--ci]` | Fresh, stale, failing; `--ci` exits 1 unless all fresh |
| `rung report [--format md\|json]` | Checks, laws, new findings and fix proofs |
| `rung prove 'pytest tests/test_x.py'` | Show a new test fails without the fix and passes with it; under pytest, each new or changed test on its own |
| `rung laws [--approve ID]` | Which laws hold |
| `rung add <kind> <id> ...` | Declare a `command`, `evidence`, `scan` or `deps` check |
| `rung attest <check> <files> --by <tool>` | Record evidence an agent or person captured (Claude in Chrome, Codex, computer use) |
| `rung baseline <check>` | Accept a scan's existing findings |
| `rung waive <check> <fingerprint> --reason ...` | Accept one finding until an expiry date |
| `rung draft tla\|lean\|property\|runtime` | Model-drafted spec, proof, property tests or drive script; a checker decides |
| `rung maintain <check>` | Bring a TLA+ or Lean model back in line with code that changed since it was confirmed |
| `rung confirm <check> --by NAME --reason TEXT` | Record that a person checked a TLA+ or Lean model still describes the code |
| `rung agents [--hooks]` | Agent skill, AGENTS.md rules, enforcement hook |
| `rung doctor` | Which tools and model CLIs are available |
| `rung ledger [--prune]` | Check the verdict files for corruption |

Every command takes `-C DIR` and `--json`. Exit codes: `0` ok, `1` a check or gate failed,
`2` configuration or usage error, `3` a required tool is missing, `4` git error.

Configuration lives in `rung.toml`; see [docs/CONFIG.md](https://github.com/CalQuity/rung/blob/main/docs/CONFIG.md).

## How Rung stays honest

- A checker never reports `pass` unless its tool confirmed the result. Missing tools and
  malformed output are errors, and so is a TLA+ spec that reaches only one state, or one whose
  invariants notice no action guard being removed.
- A TLA+ or Lean model whose code changed since it was confirmed is an error, not a rerun.
- A file that changes while a check runs voids the verdict.
- The ways an agent can get to green without fixing anything (silencing the type checker or linter,
  skipping or deleting tests, lowering coverage thresholds) block in the `shortcuts` check.
- Gitleaks always runs with `--redact`, and secret-looking values (tokens, keys, credentials in
  URLs) are masked before command output is stored in the ledger. That masking is best effort:
  commands should not print secrets.
- Waivers need a reason, an approver and an expiry date. Expired waivers stop applying.
- Each verdict file carries a hash of its own contents, so a hand-edited, truncated or conflicted
  file reads as "re-run this check". That is an integrity aid, not a signature: whoever can write
  the file can rehash it, so CI re-runs what a change touches (`rung check --changed`) and changes
  to `.rung/` and `LAWS.md` deserve review like any other change (CODEOWNERS helps).
- Approving a law or adding a waiver is a file edit, not an authenticated signature.
- Rung collects no telemetry.

Rung's own repository is checked by Rung: see [`rung.toml`](https://github.com/CalQuity/rung/blob/main/rung.toml) and [`LAWS.md`](https://github.com/CalQuity/rung/blob/main/LAWS.md).

## Requirements

- **Python 3.11+** and **git**.
- **The tools for the checks you enable.** Java 11+ and
  [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases) for TLA+;
  [Lean via elan](https://lean-lang.org/install/) for Lean; gitleaks, osv-scanner, zizmor and so on for
  scans. `rung doctor` lists what is missing and how to install it.
- **A coding agent or model, only for `rung draft`.** Checks never use a model. To have Rung draft
  specs, proofs and drive scripts, install and log in to one of:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code): `npm install -g @anthropic-ai/claude-code`, then `claude auth login`
  - [Codex CLI](https://github.com/openai/codex): `npm install -g @openai/codex`, then `codex login`
  - any OpenAI-compatible endpoint (Ollama, vLLM, a hosted API): set `base_url`, `model` and the key
    variable under `[llm]` in `rung.toml`

  `rung init` lists the ones it finds, asks which to use and offers to log you in;
  `rung init --provider claude-cli` skips the question. Rung reuses the tool's own login and never
  stores credentials. Pick the model with `model = "..."` under `[llm]`; empty means the tool's default.
- **For agent enforcement (`rung agents --hooks`)**: `rung` must be on the `PATH` Claude Code sees,
  which `uv tool install rung-cli` provides.

## Credits

Rung builds on other people's ideas and says so.

- Verification skills, the feature map, and the rule that a generated verification skill must
  run before it counts come from Lauren Tan's
  [pstack](https://github.com/cursor/plugins/tree/main/pstack). Rung enforces that rule with the
  ledger; no pstack code is used.
- Verification-guided development, a Lean model differentially tested against production code,
  is how AWS built [Cedar](https://lean-lang.org/use-cases/cedar/).
- Laws enforced by proof come from [Bend 2](https://github.com/bendlang/bend)'s `LAWS.bend`.
- Diff-scoped reporting and agent hooks were informed by
  [React Doctor](https://github.com/millionco/react-doctor).
- Counterexample-driven repair of TLA+ specs follows TraceFix and related work.
- The TLA+ and Lean guides in `src/rung/guides/` are distilled from the TLA+ VS Code extension's
  knowledge base and [tlaplus/AgentSkills](https://github.com/tlaplus/AgentSkills) (MIT),
  [opum-ai/proof-skills](https://github.com/opum-ai/proof-skills) (MIT),
  [leanprover/skills](https://github.com/leanprover/skills) (Apache-2.0) and
  [alok/vericoding-skill](https://github.com/alok/vericoding-skill) (MIT).

Rung is not affiliated with or endorsed by any of these projects.

## License

[Apache License 2.0](https://github.com/CalQuity/rung/blob/main/LICENSE).
