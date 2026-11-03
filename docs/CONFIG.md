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
| `.rung/evidence/` | no | Evidence artifacts; the ledger keeps their hashes. Upload them from CI. |

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

### `command`

| Field | Default | |
|---|---|---|
| `run` | required | Command; exit status 0 passes |
| `cwd` | `.` | Working directory |

### `evidence`

| Field | Default | |
|---|---|---|
| `run` | | Script that drives the app; it gets `RUNG_EVIDENCE_DIR` and `RUNG_SEED`. Omit it for an attested check |
| `require` | `[]` | Artifact suffixes that must be present in every run, e.g. `[".webm", ".zip", ".har"]` |
| `runs` | `1` | How many times to run the script, once per seed |
| `seed` | derived from the check id | The first seed; runs use `seed`, `seed + 1`, ... |
| `faults` | the script's own list | Fault scenario names, passed as `RUNG_FAULTS` (comma-separated); `[]` drives none |
| `scenarios` | all | Scenario names to drive, passed as `RUNG_SCENARIOS` (comma-separated) |

With `run`, each run passes only if the script exits 0 **and** wrote at least one non-empty artifact
into its own directory (`.rung/evidence/<check>/<time>/seed-<N>/`), with every `require` suffix. If the
script writes `result.json`, Rung also fails the run when it records a failed scenario, reports a seed
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
- enforced by: check-a, check-b
- status: proposed | approved
```

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
