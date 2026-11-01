# Roadmap

## Shipped (0.1)

- CLI: `init`, `plan`, `doctor`, `check` (`--changed`, `--base`), `status`, `report`, `prove`,
  `laws`, `add`, `attest`, `baseline`, `waive`, `draft tla|lean|runtime`, `agents`, `hook`, `ledger`;
  `--json` and documented exit codes everywhere
- Check kinds: `command`, `evidence`, `scan` (any SARIF tool; gitleaks, osv-scanner and zizmor
  verified), `deps`, `tla`, `lean`, `drt`
- Gating on what a branch introduces (merge-base worktree or committed baseline), stable
  fingerprints, waivers with expiry
- Fix proofs: a new test must fail on the merge base and pass on the branch
- Runtime verification scripts drafted by a model and kept only once they pass
- Attested evidence: recordings an agent makes with its own browser or computer-use tool
  (Claude in Chrome, Codex, computer use), bound to the code until it changes
- GitHub Action flags pull requests that edit policy files; a pre-commit hook
- Claude Code Stop hook that blocks finishing with unverified changes
- Per-check verdict files bound to their inputs; `LAWS.md`
- GitHub Action with a sticky report comment; Rung verifies its own repository

## Next

### Output for the tools developers already watch
`rung check --format sarif|github|junit`. SARIF uploads to GitHub code scanning, GitHub annotations
put findings inline on the pull request diff, and JUnit feeds any CI's test view. Rung reads SARIF
already; this writes it.

### Editor support for `rung.toml`
A JSON Schema, registered in SchemaStore, so editors and Taplo validate check kinds and options
while they are typed rather than at `rung check`.

### Shell completions and a docs site
`rung completions bash|zsh|fish`, and a small site with a CLI reference generated from the parser.

### Test quality
Mutation-testing adapters (mutmut, Stryker, cargo-mutants) that report which mutants the suite
fails to kill on changed lines, gated like scans: only surviving mutants a branch introduces block.

### More adapters and agents
JSON adapters for tools that do not emit SARIF (React Doctor, ESLint, Ruff), and enforcement
hooks for agents beyond Claude Code as their hook interfaces settle.

### Counterexample to regression test
Translate a TLC trace into a test in the repository's own language and add it to the
suite, so the bug stays fixed even where TLC does not run.

### Counterexample to video
Replay a TLC trace against the running app with the runtime-evidence script and record
it. A formal finding becomes a thirty-second video a reviewer can watch.

### Trace validation
Record event logs from real runs (tests, staging, the evidence script) and check each
one against the TLA+ spec. A log the spec cannot produce means the code and the spec
have drifted, the gap a mirror-style spec otherwise leaves.

### Spec mutation
Delete a guard or weaken an action in the spec and re-run TLC. If every invariant still
holds, the invariants are too weak to catch that class of bug. This extends the current
vacuity check.

### Portal
A local web view, `rung portal`, over the same files:

- **Trust map:** each module coloured by which rungs cover it and whether those
  verdicts are fresh.
- **Laws:** proposed laws awaiting approval, with the plain-language text, the formal
  statement and a counterexample when one exists.
- **Findings inbox:** counterexamples and divergences, each with its trace, a
  regression test and a video once those exist; "real bug" or "wrong spec" is recorded
  as a decision.
- **Evidence-gated coordination:** long-running work is split and delegated to agents
  (the coordinator pattern of Symphony, MaxOS and Cursor Projects). The difference is
  the merge gate: a change merges only when every law covering the files it touched is
  held, and autonomy per module follows its trust level.

### Cost accounting
Record tokens, wall time and CPU energy per verified change, comparing deterministic
checkers with LLM-only verification loops.

## Later

- `rung explain <kind|state|fingerprint>`: what a check kind does, what a verdict state means, and
  why a finding blocks, for people and agents alike.
- CI templates beyond GitHub: GitLab, CircleCI, Jenkins and Buildkite, running `rung check
  --changed` and `rung report`.
- A Docker image with Java, `tla2tools.jar`, elan and the scanners preinstalled, removing the
  slowest CI setup step.
- `-q` and `-v` flags, for quiet hooks and verbose TLC and Lean runs.
- Monorepos: several `rung.toml` files run and reported together.
- `rung migrate` to upgrade `rung.toml` and `.rung/` files across format changes.
- reviewdog's rdjson output, to reuse its GitLab, Gerrit and Bitbucket reporters.
- `rung self update` hints in `rung doctor` when a newer release exists.

- A Rust port of the CLI once the file formats settle, shipped as a single binary
  (Homebrew, a curl installer, npm wrapper). The current test suite and recorded
  TLC/Lean outputs become its conformance tests.
- More checkers: Quint and Apalache, Alloy, Dafny, Bend, Verus.
- Aeneas integration for proving properties of Rust code directly.

## Distribution

1. PyPI as `rung-cli` (`uv tool install rung-cli`), published from CI by `release.yml` via Trusted Publishing.
2. A Homebrew tap that also installs Java and elan.
3. Prebuilt binaries after the Rust port.
