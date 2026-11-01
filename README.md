# Rung

**Prove your code works, not just that it builds.** Rung is a command-line tool for codebases
where coding agents write a lot of the code. It runs your tests, security scanners, runtime
checks, TLA+ model checks and Lean proofs. It records each result in a ledger tied to the
exact bytes it checked, and it blocks a pull request only on what that pull request breaks.

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
rung init                         # survey the repo, wire up installed scanners, choose a model
rung check                        # run everything and record verdicts
rung agents --hooks               # agents cannot finish while their changes are unverified
```

Then let the model write a verification script for your app, which Rung keeps only once it has
passed against the real app:

```bash
rung draft runtime --intent "create, list and delete a todo through the HTTP API"
```

## What it checks

| Concern | Check | What blocks |
|---|---|---|
| Tests | `command` | The suite fails |
| Bug fixes | `rung prove` | The new test passes *without* the fix, so it proves nothing |
| Real behaviour (UI, API, CLI) | `evidence` | The drive script fails or records no evidence; `require = [".webm"]` demands a video |
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

## Formal methods where they pay

TLA+ and Lean are not for every file. Rung recommends them where the survey finds code that
needs them, and the model drafts the first version.

- **`rung draft tla queue.py`** writes a TLA+ model whose actions copy the code's real guards,
  then runs TLC. A counterexample stops the draft and goes to you. The model never gets to
  edit the spec until the checker is quiet.
- **`rung draft lean pricing.py`** writes a small Lean model of the intended behaviour, proves
  its laws, and sets up a differential test that holds the real code to it on random inputs.
  Lean checks pass only when `#print axioms` shows no `sorry`, no `native_decide` and no
  invented axioms.

[`examples/lease-queue`](https://github.com/CalQuity/rung/tree/main/examples/lease-queue) shows both catching a real bug, plus a
three-step case only the differential test finds.

## In CI

```yaml
- uses: actions/checkout@v7
  with:
    fetch-depth: 0
- uses: CalQuity/rung@v0.1.2
```

The action runs the checks a pull request touches against its merge base. It keeps a single
report comment up to date and fails only on what the pull request introduced. Without the
action, use `rung check --changed` and `rung report`.

## With coding agents

`rung agents` writes a Claude Code skill and an `AGENTS.md` section, which Codex, Cursor and
others read. With `--hooks` it also installs a Claude Code `Stop` hook. An agent whose edits
touch a check that is not freshly passing is told which `rung check` to run, and cannot finish
until it has. The guidance can be ignored; the hook cannot.

Drafting uses a model you already have. `rung init` reuses your `claude` or `codex` login, or
any OpenAI-compatible endpoint, including local models. Rung never stores credentials.

## Commands

| Command | Does |
|---|---|
| `rung init` | Survey the repo, add relevant checks, choose a model |
| `rung plan` | Recommend checks, cheapest and most useful first |
| `rung check [ids] [--changed] [--base REF]` | Run checks and record verdicts |
| `rung status [--ci]` | Fresh, stale, failing; `--ci` exits 1 unless all fresh |
| `rung report [--format md\|json]` | Checks, laws, new findings and fix proofs |
| `rung prove 'pytest tests/test_x.py'` | Show a new test fails without the fix and passes with it |
| `rung laws [--approve ID]` | Which laws hold |
| `rung add <kind> <id> ...` | Declare a `command`, `evidence`, `scan` or `deps` check |
| `rung baseline <check>` | Accept a scan's existing findings |
| `rung waive <check> <fingerprint> --reason ...` | Accept one finding until an expiry date |
| `rung draft tla\|lean\|runtime` | Model-drafted spec, proof or drive script; a checker decides |
| `rung agents [--hooks]` | Agent skill, AGENTS.md rules, enforcement hook |
| `rung doctor` | Which tools and model CLIs are available |
| `rung ledger` | Verify the ledger's hash chain |

Every command takes `-C DIR` and `--json`. Exit codes: `0` ok, `1` a check or gate failed,
`2` configuration or usage error, `3` a required tool is missing, `4` git error.

Configuration lives in `rung.toml`; see [docs/CONFIG.md](https://github.com/CalQuity/rung/blob/main/docs/CONFIG.md).

## How Rung stays honest

- A checker never reports `pass` unless its tool confirmed the result. Missing tools and
  malformed output are errors, and so is a TLA+ spec that reaches only one state.
- A file that changes while a check runs voids the verdict.
- Gitleaks always runs with `--redact`; secret values never reach output, reports or the ledger.
- Waivers need a reason, an approver and an expiry date. Expired waivers stop applying.
- Ledger entries are hash-chained, and `rung ledger` detects a rewritten history.
- Rung collects no telemetry.

Rung's own repository is checked by Rung: see [`rung.toml`](https://github.com/CalQuity/rung/blob/main/rung.toml) and [`LAWS.md`](https://github.com/CalQuity/rung/blob/main/LAWS.md).

## Requirements

Python 3.11+. Each check needs its own tool: Java 11+ and
[`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases) for TLA+,
[Lean via elan](https://lean-lang.org/install/) for Lean, and the scanners you enable.
`rung doctor` says what is missing and how to install it.

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
