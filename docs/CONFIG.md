# Configuration reference

## Files

| Path | Commit it? | What |
|---|---|---|
| `rung.toml` | yes | Declared checks and the model provider |
| `LAWS.md` | yes | Laws and the checks that enforce them |
| `.rung/verdicts/<check>.json` | yes | Each check's latest verdict, bound to a digest of its inputs |
| `.rung/cache/` | no | Per-file digests and the local run history |
| `.rung/baselines/<check>.json` | yes | Accepted existing findings for a scan check |
| `.rung/waivers.toml` | yes | Accepted individual findings, with reasons and expiry dates |
| `.rung/verify/` | yes | Generated drive script and feature map (`rung draft runtime`) |
| `.rung/formal/<check>.json` | yes | For a `tla` or `lean` check: the code its model was last confirmed to describe, and what the model says (scope, labels, fail-ability) |
| `.claude/skills/verify-<check>/SKILL.md` | yes | How an agent runs, reads and maintains one `tla` or `lean` check |
| `.rung/evidence/` | no | Evidence artifacts; the ledger verifies their hashes before treating a verdict as fresh. CI upload is opt-in. |

## `rung.toml`

```toml
[project]
name = "my-app"

[llm]
provider = "claude-cli"   # claude-cli | codex-cli | openai-compatible | none
model = ""                # optional model override
base_url = ""             # openai-compatible only, e.g. http://localhost:11434/v1
api_key_env = "RUNG_API_KEY"

[[check]]
id = "tests"
kind = "command"
run = "pytest -q"
covers = ["src/**", "tests/**"]
```

### Models

Checks never use a model: every verdict comes from a deterministic tool. A model is used only by
`rung draft`, to write a first version of a spec, proof or drive script that a checker then accepts
or rejects. You choose it: `provider` picks Claude Code, Codex or an OpenAI-compatible endpoint, and
`model` is passed through as that tool's `--model` / `-m`. Left empty, the tool's own default applies
(Claude Code's default model, or the model in `~/.codex/config.toml`). Each drafted check records
`drafted_by = "<provider>:<model>"`: the model Claude Code reports having used, or, for Codex,
which does not report it, the configured one (marked "as configured"). Replies are
schema-constrained JSON (`--json-schema`, `--output-schema`, or `response_format`); an HTTP server
that rejects `response_format` is asked for JSON in the prompt instead, and the reply is checked
against the schema.

### Checks

Every check has `id` (letters, digits, `.`, `_`, `-`), `kind` and optionally `covers`: glob
patterns (`*`, `**`, `?`, `[abc]`) for the files whose change makes its verdict stale. `command`
and `evidence` checks with no `covers` cover the whole project, so any change stales them; narrow
`covers` to keep them fresh longer. A scan's verdict also depends on its baseline and the waivers
file, and expires when a waiver it relied on expires. Rung validates `rung.toml` before running
anything and rejects wrong types instead of guessing. Rung's outputs (`.rung/verdicts/`, `.rung/evidence/`, `.rung/cache/`) are never part of coverage;
inputs such as `.rung/verify/` scripts, baselines and waivers can be.
Instead each verdict records a hash of its check's declaration, so editing a check stales only
that check. `timeout` (seconds) is accepted by every kind.

`verifies` optionally declares the paths the check is intended to verify. A gate with
`--require-coverage` requires a matching `verifies` or explicit `covers` scope on a selected
command, runtime, or formal check. The path must also be among the check's freshness inputs.
Use `verifies` when the input scope includes shared configuration or dependencies that the
check does not directly exercise.

For a custom check with declared verification targets, list the files that implement the
verifier under `specs`. Include indirect helpers that the command loads. `rung add --specs`
sets these paths at registration. The merge gate also accepts policy paths declared by the
trusted workflow. A custom check needs this declaration to claim changed targets or satisfy
required coverage.

### `command`

| Field | Default | |
|---|---|---|
| `run` | required | Command; exit status 0 passes |
| `cwd` | `.` | Working directory |

Recognized pytest test runs also require at least one passing case in a fresh JUnit report.
For collection-only commands, the merge gate still requires passing cases.
Python's `-m unittest`, Go tests, and direct Node tests with the default reporter
also require at least one passing case. This catches empty test discovery even when the
runner exits successfully.
Inferred Rust tests use `cargo test --locked`. For a new project, run
`cargo generate-lockfile` and review the lockfile before checking. Standard Cargo test runs
require at least one passing case.

### `evidence`

| Field | Default | |
|---|---|---|
| `run` | | Script that drives the app; it gets `RUNG_EVIDENCE_DIR` and `RUNG_SEED`. Omit it for an attested check |
| `require` | `[]` | Artifact suffixes that must be present in every run, e.g. `[".webm", ".zip", ".har"]` |
| `runs` | `1` | How many times to run the script, once per seed |
| `seed` | derived from the check id | The first seed; runs use `seed`, `seed + 1`, ... |
| `faults` | the script's own list | Fault scenario names, passed as `RUNG_FAULTS` (comma-separated); `[]` drives none |
| `scenarios` | all | Scenario names to drive, passed as `RUNG_SCENARIOS` (comma-separated) |
| `structured` | `false` | Require `result.json` with an integer seed, unique scenario names, and at least one boolean assertion outcome per scenario. Newly drafted drives enable this. |
| `isolate` | `true` for drafts, otherwise `false` | Run in a disposable guarded copy, including when replaying a seed. An unavailable guard reports an error. |

The generated evidence page is hashed alongside the artifacts. Run older receipts again to
bind their generated pages.

Add `--isolate` when registering a hand-written drive to enable the same behavior. The guard
limits writes and filters the environment. Drives with `uv run --with` can fetch dependencies.
Set `isolate = false` for a reviewed drive that needs host access, or when running it inside
an isolated worker that provides equivalent protection.

Required PNG, GIF, JPEG, WebM, ZIP and HAR artifacts also receive basic format checks.
The format checks establish that a file resembles the required capture. The scenario assertions
establish what the drive observed.

With `run`, each run passes only if the script exits 0 **and** wrote at least one non-empty artifact
into its own directory (`.rung/evidence/<check>/<time>/seed-<N>/`), with every `require` suffix. If the
script writes `result.json`, Rung also fails the run when the file is malformed, records a failure, reports a seed
other than the one Rung passed, or has no scenario for a name in `faults` or `scenarios`. The check
passes only if every run passes. The verdict's `details.runs` (also in `rung check --json`) lists each
seed with its verdict, scenarios and failed assertions; a failure adds `failing_seed`,
`failing_scenario` and `reproduce`, the command that runs that seed again:
`rung check <id> --seed N`, optionally with `--scenario NAME`. Such a run is not recorded, because it
does not stand for the check as declared.

Every run also writes `index.html` next to its seed directories (the verdict's `details.report`): a
self-contained page with each seed's result, its scenarios, faults and assertions, screenshots in step
order, the video, and links to `trace.zip` and the HAR. `rung evidence <id> [--seed N] [--out DIR]`
writes it again for the newest run.

`result.json`, as the drafting guide asks for it:

```json
{"feature": "apply a coupon", "seed": 4242, "passed": true,
 "not_applicable": {"auth": "the checkout page has no sign-in"},
 "scenarios": [{"name": "reload", "category": "fault", "faults": ["reload after applying WELCOME5"],
                "passed": true,
                "assertions": [{"name": "coupon applied once", "expected": "$32.00", "observed": "$32.00",
                                "passed": true}]}]}
```

Categories are `happy`, `input`, `state`, `auth`, `fault` and `robustness`. Files in a subdirectory
named after a scenario appear under that scenario on the evidence page.

Without `run`, the check is **attested**: an agent drives the feature with its own browser or
computer-use tool (Claude in Chrome, Codex, computer use) or a person does it by hand, and records the
files with `rung attest <id> <files> --by <tool> --note "<what was seen>"`. The verdict stores who
attested and each file's hash, and holds until a covered file changes; `rung check` cannot re-run it,
so a stale attested check stays an error until someone attests again. Prefer a script wherever one
can reach the feature.

### `scan`

| Field | Default | |
|---|---|---|
| `tool` | | Preset: `gitleaks`, `osv-scanner`, `zizmor`, `semgrep`, `opengrep`, `trivy` |
| `run` | preset's | Any command that writes SARIF; `{out}` is replaced with a file path, otherwise stdout is read |
| `finding_exit_codes` | preset's, or `[1]` for custom commands | Nonzero exit codes meaning a completed scan with findings. Other nonzero exits are errors, even when partial SARIF is present. |
| `fail_on` | `warning` | Lowest SARIF level that blocks: `none`, `note`, `warning`, `error` |
| `default_level` | preset's | Level for results the tool reports without one |
| `covers` | preset's | gitleaks/semgrep: everything; osv-scanner: manifests and lockfiles; zizmor: workflows |

The `osv-scanner` preset runs `osv-scanner scan source --no-resolve`: manifests and lockfiles are
scanned as written, without resolving transitive dependencies, because one package the registry does
not have (an invented or private name in `requirements.txt`) makes resolution fail the whole scan.
The `deps` check is what reports such a package. A project that wants transitive resolution can set
`run` to the command without `--no-resolve`.

On `--changed` or `--base`, the scanner also runs on the merge base, in a temporary worktree,
and only new findings count. Otherwise findings are compared with `.rung/baselines/<id>.json`
if present. Without either, every finding counts as new.

### `shortcuts`

| Field | Default | |
|---|---|---|
| `allow` | `[]` | Rule ids not to check, e.g. `["noqa", "lint-allow"]` |
| `fail_on` | `warning` | Lowest level that blocks; `tests-removed`, `coverage-lowered`, `focused-test` and `relaxed-config` are `error` |

Rules: `ts-ignore`, `ts-expect-error`, `any-cast`, `lint-disable` (TypeScript/JavaScript);
`type-ignore`, `noqa` (Python); `nolint` (Go); `lint-allow`, `unsafe` (Rust); `suppress-warnings`
(Java/Kotlin); `no-cover`, `skipped-test`, `focused-test` (test files); `relaxed-config` (tsconfig,
pyproject, mypy and pyright settings); and, against the base, `tests-removed` (fewer test definitions
than the base) and `coverage-lowered` (a lower `fail_under` or coverage threshold).

Only what a change adds blocks. With `--base` it compares with the merge base; otherwise with
`rung baseline <id>` if recorded, else with the merge base of the default branch. A genuinely needed
one is accepted with `rung waive`.

### `deps`

| Field | Default | |
|---|---|---|
| `min_age_days` | `30` | Youngest acceptable new package |
| `allow` | `[]` | Package names a person has reviewed |

Reads `requirements*.txt`, `pyproject.toml` and `package.json`. Packages new relative to the
merge base, or all declared packages without a base, are looked up on PyPI or npm.

### `tla`

| Field | Default | |
|---|---|---|
| `spec` | required | The `.tla` file |
| `config` | | The `.cfg` file |

Needs Java 11+ and `tla2tools.jar` (`RUNG_TLA2TOOLS` or `~/.cache/rung/tla2tools.jar`).

A counterexample is printed as plain-language steps (when the check has a model record) next to the
raw trace, and drawn as `.rung/evidence/<check>/<run>/counterexample.mmd`. A pass under 200 distinct
states also writes `state-graph.mmd` there. Both are Mermaid `stateDiagram-v2` files, hashed into
the verdict like any evidence.

### Model records (`tla` and `lean`)

A drafted `tla` or `lean` check has a record in `.rung/formal/<check>.json`, written by Rung:

| Field | |
|---|---|
| `sources` | The covered source files (globs) the model describes; a `tla` check's `covers` |
| `confirmed` | `commit`, `digest` and per-file digests of `sources` when the model was last confirmed, with `by`, `reason` and `at` |
| `scope` | `bounds`, `assumptions` and `not_covered`, in plain sentences |
| `functions` | The functions the model mirrors, as `path:line name` |
| `actions`, `invariants` | (`tla`) Each action's plain-language label; each invariant's statement and harm |
| `failability` | (`tla`) Guard mutants caught by an invariant, for one spec digest |
| `history` | The last 20 confirmations, each with its outcome |

When the digest of `sources` differs from `confirmed.digest`, the check is an error ("the code
changed since the model was confirmed at <commit>") and `rung status` shows it as `drifted`. Resolve
it with `rung maintain <check>` (the drafting model updates the model from the diff and Rung rechecks
it) or, after reviewing the model yourself, `rung confirm <check> --by NAME --reason TEXT`.
A check without a source link can still validate the model and reports a model-only result.
An approved law needs a confirmed source link or a check that exercises the implementation
before it can hold or pass the merge gate. `rung confirm` creates a record from the model's
`covers` (for `lean`, it can use its `<check>-drt` check's `covers`).

### `lean`

| Field | Default | |
|---|---|---|
| `project` | required | Directory with a lakefile |
| `theorems` | `[]` | Fully qualified theorem names whose axioms are audited |

### `drt`

| Field | Default | |
|---|---|---|
| `model` | required | Command for the model side (a leading `lake` is resolved from elan) |
| `impl` | required | Command for the implementation side |
| `alphabet` | required | Tokens inputs are built from |
| `cases`, `min_len`, `max_len`, `seed` | `1000`, `0`, `8`, `0` | Input generation |
| `model_cwd`, `impl_cwd` | `.` | Working directories |
| `specs` | `[]` | Files whose change should stale the verdict (the model, the adapter) |

Both sides read one input per line on stdin and print one output line per input.

## `LAWS.md`

```markdown
## law-id
One plain sentence a person can approve.
- harm: one sentence on what goes wrong for a person if it breaks (optional)
- enforced by: check-a, check-b
- status: proposed | approved
```

A law enforced by a check whose model has drifted is `unproven`, with the changed files as the reason.

## Waivers (`.rung/waivers.toml`)

Written by `rung waive`; edit by hand only to revoke.

```toml
[[waiver]]
check = "secrets"
fingerprint = "7a9fe0a79cbca79f"
reason = "test fixture with a fake token"
approved_by = "Dev"
expires = 2027-01-31
```
