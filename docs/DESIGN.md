# Design

## The problem

Coding agents now write more code than people can read. Two habits fail at that
scale. Reading every line does not keep up, and "the build passed" or "the tests
are green" says little about whether the change does what was meant. Tests written by
the same agent that wrote the code tend to restate the implementation, so they pass by
construction.

Rung's position: **trust should come from evidence a machine can check, tied to the
exact code it covers, and it should expire on its own.**

## Principles

1. **The model proposes; a checker decides.** A language model drafts specs, models,
   proofs and explanations. Only TLC, the Lean kernel, a differential test or a test
   runner can accept them. Rung never takes a model's word that something holds.
2. **Findings go to people; errors go back to the model.** A parse error or a failed
   build is the model's to fix. A TLC counterexample is not: it is a real bug or a wrong
   spec, and only a person can tell which. Letting the model edit a spec until the
   checker is quiet is *specification hacking*; Rung refuses to do it.
3. **A verdict covers exact bytes.** Each ledger entry stores a content hash of every
   covered source and spec file. Change one byte and the verdict is stale. Nobody has to
   remember to re-run anything.
4. **Make the cheap rungs universal and the expensive rungs targeted.** Lint rules and
   runtime evidence apply everywhere. TLA+ and Lean go where a mistake is expensive and
   the behaviour can be stated precisely: queues, leases, retries, money, permissions.
5. **Close the shortcuts.** An agent's easiest path to green is to weaken the check, so
   Rung rejects `sorry` and friends, audits theorem axioms, fails vacuous specs, voids
   verdicts whose files changed mid-run, and distrusts edited verdict files.
6. **Plain files are the interface.** `rung.toml`, `LAWS.md`, `.rung/verdicts/` and
   the line protocol are the contract. Any tool, agent or future reimplementation can
   read and write them.

## Configured-check health reports

`rung score-my-code` executes every declared check through the existing runner and writes a
standalone HTML page to `.rung/cache/score/index.html`. `--no-run` reads existing verdicts instead;
it still evaluates freshness. Optional `--base` supplies the scanner comparison and required
verdict context. A missing or mismatched base context cannot earn score credit.

The score is the nearest whole number to `100 * fresh passing / configured`, with every check
weighted equally. Zero checks yields no score, not 100. Stale, drifted, unrun, failed and errored
checks earn no credit. Corrupt ledger records are untrusted. Missing check categories do not enter
the denominator and must be disclosed separately; the score is not a measure of code quality,
test strength, line coverage or bug absence. The check-set fingerprint hashes sorted IDs and
existing definition hashes so consumers can identify changes to the scoring denominator.

The version-1 JSON payload extends the ordinary report with `score`, `stats`, `categories`,
`check_set_digest`, `generated_at`, `ran_checks`, `elapsed_seconds`, `comparison_base`, `passed`
and `report_path`. Check rows additionally carry `category`, original `verdict`, a plain-language
`explanation`, `diagnostics`, `next_step` and invocation-only `elapsed_seconds`. New findings carry
`current` so old findings can be distinguished from current evidence. No test-case counts are
inferred from arbitrary text output. Category assignment reuses the kit's check classification;
it does not establish behavioral coverage.

The renderer escapes all source/checker text, applies best-effort redaction to payload strings,
and includes no scripts, external assets or active links. The official CalQuity wordmark is packaged
with Rung and embedded as a data image; its CSP permits data images only. Slate blue, light surfaces
and native editable HTML carry the brand. The headline is the check count; the numerical score is secondary.
Report writing is atomic, rejects
symbolic links and unrelated existing files, and is restricted to the score cache or a location
outside the project. The destination is checked before and after executing repository commands.
The HTML file is not itself a verdict. This command adds no numeric merge threshold; exit 1
means no checks are configured, a configured check is not fresh and passing, or the ledger is corrupt.

Additive score fields `assessments` and `test_results` describe dedicated declared scanners, runtime
and static tooling, and current structured case counts. `stats.review_required` counts failed shortcut
checks that need a human decision; it does not change exit codes or exclude those checks from the score.
Check rows include an `assessment` and optional `test_results`. Missing dedicated scanner presets are
disclosed even when a dependency-existence check is configured; custom checks may need manual review.
Whole-suite test strength and line coverage remain unassessed by this report. `test_results` aggregates
only current pytest JUnit counts and lists reporting suites; it is null when no such data exists.
Historical case counts remain attached to the stale check, with a previous-results label.

## The ladder

| Rung | Check kind | Evidence |
|---|---|---|
| Tests | `command`, plus `rung prove` for bug fixes | Exit status; a fix proof shows the new test fails on the merge base |
| Honest tests | `shortcuts` | No new type-checker silencers, lint disables, skipped, focused or deleted tests, lowered coverage thresholds or relaxed type-checker settings |
| Laws on real code | `command` from `rung draft property` | Hypothesis or fast-check properties that pass on the code and fail on deliberate one-token bugs in it |
| Security | `scan` (any SARIF analyzer), `deps` | New findings relative to the merge base or a baseline; registry facts for new packages |
| Architecture | `command` running the project's type checker, linter or import-boundary tool | Exit status |
| Runtime | `evidence` running a script that drives the real app through seeded scenarios and faults, or attested with `rung attest` | Hashed artifacts per seed: transcripts, request/response pairs, screenshots, video, Playwright traces, HARs; attested ones also record who recorded them |
| Protocol | `tla` | TLC result over every reachable state; counterexample trace |
| Model | `lean` + `drt` | Kernel-checked theorems with audited axioms; agreement with the code on random inputs |
| Ledger | always | One verdict file per check, bound to its inputs |

The ladder is applied by escalation, not climbed everywhere. Every repository gets the cheap
rungs. TLA+ is recommended only for code that holds state across steps, and Lean only for
small pure cores.

### Gating on what changed

A gate that fails on every existing problem gets switched off, so Rung gates on what a branch
introduces. For a `scan` check, `--changed` or `--base` runs the analyzer on the merge base in a
temporary git worktree as well as on the branch, and compares the two. Without a base, findings
are compared with a committed baseline (`rung baseline`), if one exists.

The comparison uses fingerprints that survive unrelated edits. A fingerprint is built from the
tool, the rule, the file, and the flagged code snippet, or the message with version numbers
removed. Line numbers are left out because they move with every edit. Tool line hashes are a
last resort, because several tools hash the whole file. Duplicate fingerprints are counted, so
a second copy of the same secret is still new.

A finding a team chooses to keep is waived with a reason, an approver and an expiry date.
Expired waivers stop applying and are reported. Agents are told never to add waivers.

### Fix proofs

A regression test that passes before the fix proves nothing, and agents write such tests often.
`rung prove` copies the branch's changed test files onto the merge base, runs them there where
they must fail, then runs them on the branch where they must pass. `--expect` names text the
failure must contain, so an import error does not count as the bug being caught.

One failing test can hide a weak one in the same file, so when the command runs pytest, both runs
also write a JUnit XML report and each test the branch adds or changes (found by comparing the test
functions' code with the merge base) is judged on its own. Any of them that passes or skips on the
merge base fails the proof, by name. `--json` lists each one as `cases`, with its outcome on both
sides. Other runners are judged by the whole run.

### Runtime evidence that proves itself

`rung draft runtime` has the model write a drive script and a feature map. Rung runs the script
as an `evidence` check and feeds failures back until it passes and records evidence; only then
is it registered. This is the rule from Lauren Tan's pstack, that a verification skill never
executed is only a draft, enforced by the checker rather than stated in a prompt.

A script that passes only on the happy path proves little, so the drafter also holds it to a
contract it can check from the script's `result.json`: named scenarios in each category (happy,
input, state, auth, fault, robustness), or a stated reason a category does not apply. The same rule
as for property tests decides what goes back to the model. A broken script (no evidence, a failing
happy path, a missing category) is the model's to fix. A happy path that passes while an edge case
or fault scenario fails is the app's problem, so it is shown to a person and the script is registered
as it is; the model is never asked to make it pass.

### Seeds, faults and replay

A runtime failure that cannot be reproduced cannot be fixed or trusted. Rung gives each run a seed
(`RUNG_SEED`), and the script derives every choice from it: which inputs it tries, which backend call
it delays and for how long, when it reloads or kills the server. A check can run several seeds, each
in its own directory; a failure names its seed and the command that runs it again. While drafting,
Rung runs the accepted seed a second time and rejects the script if any assertion's outcome changed,
because a flaky script turns every failure into noise.

Fault scenarios (`RUNG_FAULTS`) are what the script can do from outside the app: delay or abort
network calls in the browser, go offline, submit twice, reload or go back mid-flow, kill and restart
the app's process on the same data, move the browser's clock, point a dependency at a dead port.
They cover the browser, the network calls the browser makes and the app's process, not anything
inside the app's runtime. The same seed reproduces the same injected faults and inputs; it does not
fix the order in which the app's own threads, timers or the operating system run, so a race inside
the app can still pass on one run of a seed and fail on another.

Each run's evidence is meant to be replayed by a person: a Playwright trace (DOM snapshots, network
and console at every action), a HAR, a video, and screenshots named after the step they prove. Rung
writes one `index.html` per run that ties them to the scenarios and assertions, with relative links
only. With uploads enabled, the GitHub Action links the artifact from the report.

### Agents

Guidance (a skill and an AGENTS.md section) says how to use Rung and which shortcuts are off
limits. Enforcement is a Claude Code `Stop` hook: when an agent tries to finish, `rung hook
stop` checks whether the checks covering everything changed since the branch left its base
(committed or not) are fresh. If not, the agent is
told what to run and cannot stop yet. The hook fails open on anything unexpected, and lets the
agent stop the second time in a turn, so it can never trap one.

### Why both TLA+ and Lean

They answer different questions, and the example shows it.

- **TLA+ mirrors the code.** Each action copies the guard the code actually checks, so
  TLC explores what the code *can* do, across every interleaving, and checks invariants
  that say what it *should* do. It finds concurrency and protocol bugs: lost updates,
  double dispatch, stale leases.
- **Lean models the intent.** A small executable model of the intended behaviour, with
  its laws proved by the kernel. A proof about a model says nothing about the code until
  the code is held to the model, which is what the differential test does. This pattern,
  *verification-guided development*, is how AWS built Cedar.

In the lease-queue example, TLC finds the two-worker interleaving. The differential
test finds a three-step case the TLA+ invariant cannot see, where a worker completes on
its own expired lease. Neither check alone covers both.

### Why a differential test and not just more proofs

Proving properties of production code directly is expensive and language-specific.
Proving them of a ten-times-smaller model, then demanding identical outputs from the
real code on thousands of random inputs, is cheap and works for any language that can
read stdin. Divergences are shrunk to a minimal input before they are reported.

### Models that cannot silently go stale

These checks are the formal-methods part of a project's verification kit, and a model is only
worth its verdict while it describes the code. When covered code changes, the verdict goes stale and
`rung check` reruns TLC, but on the *unchanged* spec, which usually passes again. That pass looks
like proof and is about code that no longer exists. So each `tla` and `lean` check keeps a small
committed record, `.rung/formal/<check>.json`: the commit and a digest of the covered sources when
the model was last confirmed to describe them, with who confirmed it and why.

- **Drift is an error, not a rerun.** When the covered sources' digest differs from the confirmed
  one, the check is an error naming the changed files, and the laws it enforces are UNPROVEN until
  someone acts. A draft that is accepted sets the record; so do the two ways out of drift.
- A standalone check can pass against a model before a source link is confirmed. Reports label that
  result model-only. An approved law that relies entirely on model checks requires at least one
  confirmed link to the source. Differential or other implementation checks can supply separate
  evidence for the law.
- **`rung maintain <check>`** gives the model the spec, the diff of the covered files (and any SQL
  definitions) since the confirmed commit, and the commit messages, and makes it classify each
  change: no effect, the model must change, the promise changed on purpose (with quoted evidence),
  or the promise may be broken. It updates the model from the code and never sees a checker
  result. Rung rejects a reply that removes or rewrites an invariant, property or theorem, or adds
  a state constraint, unless a change is classified as an intended promise change with evidence.
  The outcome is in sync, updated, bug (a counterexample on a model that describes the current
  code) or blocked; the confirmed commit moves only when the model now describes the code.
- **`rung confirm <check> --by NAME --reason TEXT`** records that a person compared the model with
  the code and it still holds, for changes no model needs to see.

### Able to fail

A TLA+ pass proves invariants over every reachable state, but an invariant that holds whatever the
actions do proves nothing. After a TLA+ draft passes, and on every `rung maintain`, Rung removes one
guard at a time from the actions (a conjunct with no primed variable) and runs TLC on each mutant
with a short timeout. At least one mutant must break an invariant other than `TypeOK`; if none does,
the spec goes back to the model as too weak to catch anything. The score is kept with the record
and shown with each check. Swapping conjuncts is not tried: conjunction is commutative, so it would
change nothing. Lean has the differential test against the real code instead.

### Readable by people

The TLA+ draft also returns a plain-language label for each action, a one-sentence statement and
harm for each invariant, and a `scope`: the bounds, the assumptions and what is not covered (Lean
drafts return the scope too). A counterexample is printed as numbered steps in those words, with
only the variables each step changed, next to the raw trace, and drawn as a Mermaid
`stateDiagram-v2`; a passing spec under 200 states also gets a diagram of the explored graph from
TLC's DOT dump. Each accepted model gets a project skill, `.claude/skills/verify-<check>/SKILL.md`,
saying what it promises, covers and assumes, how to run it, how to tell a real bug from a modeling
error, and when to maintain it.

## Laws

`LAWS.md` holds the rules the software must never break, one sentence each, in words
a non-specialist can approve, with an optional `harm:` line saying what goes wrong for a person
if it breaks. Each law names the checks that enforce it. A law is **held** only when a person has
approved it and every enforcing check has a fresh pass; a law enforced by a model whose code has
drifted is unproven.

This generalises Bend 2's `LAWS.bend`. Bend proves laws about programs written in Bend;
Rung lets a law about a Python or TypeScript service be enforced by a TLA+ invariant, a
Lean theorem plus a differential test, and a test suite, all at once. Agents may
*propose* laws; only people approve them.

## Trust boundaries

- Rung never stores credentials. CLI providers reuse the tool's own login; HTTP
  providers read a key from an environment variable named in `rung.toml`.
- Drafting with Claude Code runs with all tools disabled (`--tools ""`) and no MCP servers
  (`--strict-mcp-config`), so the model sees only what Rung sends; Codex runs in a read-only sandbox and may read the repository.
- Runtime and property trials run in a disposable copy under an OS write guard. The trial
  environment removes credential-like variables and masks common credential paths. Model-assisted
  Lean builds run as project tooling. Review the generated files before relying on them.
- Spec and proof checking runs locally. Nothing is uploaded. The `deps` check sends package
  names to PyPI and npm; the scanners make whatever network calls they make themselves
  (zizmor runs with `--offline`).
- Gitleaks always runs with `--redact`.
- Drafted drives retain the write guard on replay. Rung runs each replay in a disposable copy
  and copies back the report and ordinary evidence files. Drives can require broader access
  through reviewed configuration. A HAR or
  a trace records request headers and bodies, cookies and tokens included. The GitHub Action
  uploads `.rung/evidence/` only when opted in. Drive with test accounts and synthetic data
  before sharing. Rung redacts known secret shapes in command excerpts.
- The evidence page escapes everything it shows and loads nothing from the network; it does not
  sandbox the files it links to.

## What Rung is not

- Not a proof assistant or a model checker. It drives TLC and Lean.
- Not a guarantee of correctness. It guarantees that the stated laws were checked
  against the exact code in the ledger. A law that says the wrong thing is checked just
  as faithfully, which is why laws need human approval.

## Merge decision and agent output

`rung gate` resolves the target's merge base, then checks protected paths against the working
tree. It treats checker-declared specs as policy, including formal model and configuration files.
A Node command also makes package script definitions part of policy. Coverage gaps are reported
on both the local check path and the merge path. Teams can require a check scope for selected globs.
A local focused check runs only execution checks with declared targets touched by the change and
reports omitted checks. Shared inputs still invalidate earlier verdicts. The merge gate applies its
full selection.
Its settings (policy mode, extra policy paths, required coverage, approval label) come from the
`[gate]` table on the merge base, so the change under review cannot loosen them; caller options
only fill unset keys. A matching approval label turns blocking policy changes into reported ones.
When given the pull request head, the gate refuses to judge any other commit. Each custom check
needs its own verifier declaration: a policy glob exempts only checks whose launch files it matches.
A gate runs every selected automated check again, ensures approved laws have their enforcing
checks in the selection, and checks final freshness. These controls catch accidental or agent-made
weakening. They do not give integrity against a hostile change: its code runs where the receipt is
produced, on the local runner and in a Daytona sandbox alike. The verdict ledger remains useful for
developer feedback and review. A hash in a contributor-controlled file establishes integrity
against accidental changes.

Object-shaped JSON responses carry `schema_version`, including setup, kit, and draft results.
The existing `doctor` and `laws` lists keep their array shape. Read the exit code first.
Check results expose artifact paths and hashes.
Gate output includes its merge base, HEAD, selected checks, blocked reasons, uncovered paths,
and results. A scope match describes the inputs that invalidate a verdict. Its assertions
determine the behavior it exercises. A seed or scenario replay returns results without replacing
the full verdict. Configuration errors encountered after parsing emit an error object in JSON
mode. Argument errors also emit an error object when `--json` is present.

Input snapshot version 2 includes executable permissions and symbolic link targets as well as
the file contents. Earlier passing verdicts refresh on their next check. External link targets
have no reproducible repository snapshot, so covered paths using them produce an error.

POSIX checks run in their own process group and use temporary files for subprocess output.
A deadline ends the process group and any still-discoverable descendants, then returns
without waiting for inherited output descriptors. A process that is already detached and
reparented may outlive this cleanup. Run untrusted commands on an isolated disposable worker.
Linux and macOS are exercised by CI.

### Draft trial controls

A runtime draft runs twice at one seed. Rung then changes application sources in the copy
and reruns the same drive. Python returns, scripts embedded in HTML, and supported JavaScript
or TypeScript expressions provide behavior controls. The app can still load, and a previously
passing named assertion must fail. Rung holds a drive that only passes a startup control or
misses its behavior control under `.rung/cache/drafts/`. Review the retained script and use
`rung add evidence` if a manual check fits the project. Review accepted assertions against the intended behavior.
An accepted draft replaces the configured script and publishes its evidence.

`rung check --reuse-local` can reuse an unchanged verdict only with a local environment receipt.
The receipt hashes the toolchain-relevant environment (PATH, locale, and interpreter, package
manager and compiler variables such as `PYTHON*`, `NODE*`, `UV_*`, `GO*`, `CARGO*`, `JAVA*`) and
resolved launcher identities, with no environment variables in the shared verdict. Session noise
such as terminal or CI bookkeeping variables does not prevent reuse. It includes symlinks along a launcher path and their targets. Rung reruns
checks when a package runner manages the delegated tool environment and its identity is unavailable
before execution. The merge gate always reruns its checks.

### Reporting confirmed findings

`rung agents` distributes the `rung-issue` skill from `guides/issue.md` to both supported
agent skill directories. Its workflow covers reproduction, destination selection, duplicate
search, a detailed issue draft and creation/readback within the user's publishing authorization.
The skill does not add network calls to checking or report rendering. Execution errors and stale
evidence require diagnosis before a defect claim. Sensitive findings follow the target
repository's security-reporting route.

### Execution metrics

The runner records monotonic `details.execution_seconds` for the checker call, including setup
and subprocesses but excluding input snapshots, ledger serialization and HTML generation.
Failures and execution errors retain durations. Reused external captures are not executions.
The score command's `elapsed_seconds` remains the sequential verification-loop wall time,
including its surrounding bookkeeping up to report assembly.

Score rows add `recorded_elapsed_seconds`; invocation-only `elapsed_seconds` retains its existing
meaning and is null in saved-results mode. The `timing` object includes the mode, wall time,
measured/unmeasured counts, sum, mean, median, slowest check, failed/error time and a sorted
breakdown with shares. Saved timing excludes stale, drifted, invalid and external results, and
discloses that receipts can come from different runs. Legacy or non-finite, negative or boolean
durations are unavailable. Stale check details retain labeled historical measurements.

`test_metrics` derives total and non-skipped cases from current structured reports. Pass rate
is passed / (passed + failed + errors); skip rate is skipped / all cases. Empty denominators
yield null, not a perfect percentage. Test percentages do not imply coverage or suite strength.
