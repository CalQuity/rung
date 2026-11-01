# Configuration reference

## Files

| Path | Commit it? | What |
|---|---|---|
| `rung.toml` | yes | Declared checks and the model provider |
| `LAWS.md` | yes | Laws and the checks that enforce them |
| `.rung/ledger.jsonl` | yes | Hash-chained verdicts |
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
`drafted_by = "<provider>:<model>"`. Replies are schema-constrained JSON (`--json-schema`,
`--output-schema`, or `response_format`).

### Checks

Every check has `id` (letters, digits, `.`, `_`, `-`), `kind` and optionally `covers`: glob
patterns (`*`, `**`, `?`, `[abc]`) for the files whose change makes its verdict stale. `command`
and `evidence` checks with no `covers` cover the whole project, so any change stales them; narrow
`covers` to keep them fresh longer. A scan's verdict also depends on its baseline and the waivers
file, and expires when a waiver it relied on expires. Rung validates `rung.toml` before running
anything and rejects wrong types instead of guessing. Rung's own files (`.rung/`, `rung.toml`, `LAWS.md`) are never part of coverage.
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
| `run` | required | Script that drives the app; it gets `RUNG_EVIDENCE_DIR` |
| `require` | `[]` | Artifact suffixes that must be present, e.g. `[".webm"]` |

Passes only if the script exits 0 **and** wrote at least one artifact.

### `scan`

| Field | Default | |
|---|---|---|
| `tool` | | Preset: `gitleaks`, `osv-scanner`, `zizmor`, `semgrep`, `opengrep`, `trivy` |
| `run` | preset's | Any command that writes SARIF; `{out}` is replaced with a file path, otherwise stdout is read |
| `fail_on` | `warning` | Lowest SARIF level that blocks: `none`, `note`, `warning`, `error` |
| `default_level` | preset's | Level for results the tool reports without one |
| `covers` | preset's | gitleaks/semgrep: everything; osv-scanner: manifests and lockfiles; zizmor: workflows |

On `--changed` or `--base`, the scanner also runs on the merge base, in a temporary worktree,
and only new findings count. Otherwise findings are compared with `.rung/baselines/<id>.json`
if present. Without either, every finding counts as new.

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
