# Roadmap

## Shipped (0.1)

- `rung init`, `plan`, `check`, `status`, `laws`, `ledger`, `draft`, `add`, `skill`, `doctor`
- Runners: TLC, Lean (build, escape-hatch scan, `#print axioms` audit), differential
  random testing with shrinking, commands, evidence scripts
- Content-addressed, hash-chained evidence ledger; `LAWS.md`
- Model providers: Claude Code CLI, Codex CLI, any OpenAI-compatible endpoint
- Lease-queue example; CI that runs it with the real TLC and Lean

## Next

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

### Runtime verification skill generator
Generate the project-local skill that launches the app, drives one feature and records
evidence, run it once before handing it over, and keep its feature map current.

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

- A Rust port of the CLI once the file formats settle, shipped as a single binary
  (Homebrew, a curl installer, npm wrapper). The current test suite and recorded
  TLC/Lean outputs become its conformance tests.
- More checkers: Quint and Apalache, Alloy, Dafny, Bend, Verus.
- Aeneas integration for proving properties of Rust code directly.

## Distribution

1. PyPI as `rung` (`uv tool install rung`, `pipx install rung`).
2. A Homebrew tap that also installs Java and elan.
3. Prebuilt binaries after the Rust port.
