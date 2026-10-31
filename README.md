# Rung

**An opinionated verification ladder for software written with coding agents.**

Agents now write more code than people can read. Reading every line does not
scale, and "the build passed" is not evidence that the code does what you meant.
Rung gives a repository a ladder of checks, from cheap to strong, and an
evidence ledger that records which checks passed against *exactly which code*.
A verdict expires on its own the moment the code or spec it covered changes.

The model only proposes. A checker decides: the TLC model checker, the Lean
kernel, a differential test, or your test suite.

## The ladder

| Rung | What it adds | Who decides |
|---|---|---|
| 1. Architecture | Import boundaries and lint rules that make the wrong shortcut impossible | Linter, in CI |
| 2. Runtime evidence | Drive the real app like a user and keep the transcript, screenshot or video | The running app |
| 3. Properties | Invariants as property-based tests | The test runner |
| 4. Protocol | TLA+ models of stateful code: retries, leases, queues, reconciliation | TLC, over every interleaving |
| 5. Model | A small executable Lean model with proven laws, and a differential test that holds the real code to it | The Lean kernel, then random inputs |
| 6. Ledger | Every verdict bound to content hashes of its code and spec, hash-chained | `rung status` |

Rungs 4 and 5 are the heart of Rung. Most bugs that survive review in agent-written
code need a specific interleaving or a specific input; tests rarely reach them,
and model checkers and proofs reach them by construction.

## Laws

`LAWS.md` lists the rules your software must never break, one plain sentence each,
and names the checks that enforce them. A law an agent proposes stays `proposed`
until a person approves it. `rung laws` shows which laws currently **hold**: approved,
enforced, and every enforcing verdict fresh.

## Quick start

```bash
uv tool install rung          # or: pip install rung
cd your-repo
rung init                     # survey the repo, choose a model provider, log in
rung skill                    # teach Claude Code / Codex / Cursor the rules
rung draft tla src/queue.py --intent "a job result always comes from the lease holder"
rung check                    # run every check, record verdicts
rung add e2e --kind evidence --run './scripts/drive.sh'   # must write to $RUNG_EVIDENCE_DIR
rung status --ci              # in CI: fail unless every verdict is fresh
```

`rung init` asks which model drafts specs and proofs. It reuses a login you already
have (`claude auth login`, `codex login`) or any OpenAI-compatible endpoint, including
local models. Rung never stores credentials.

Rung needs Java 11+ and [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases)
for TLA+, and [Lean 4 via elan](https://lean-lang.org/install/) for Lean. `rung doctor`
reports what is missing.

## Try the example

[`examples/lease-queue`](examples/lease-queue) is a job queue with leases and a
real bug. All three formal checks catch it in different ways:

```bash
cd examples/lease-queue
rung check
```

- TLC finds the interleaving: worker 1's lease expires, worker 2 claims the job,
  then worker 1 completes it anyway.
- The Lean model proves that only the lease holder can complete a job.
- The differential test shrinks a random failure to three actions: a worker
  completes the job on its own *expired* lease. The TLA+ invariant cannot see
  that case, which is why the ladder has more than one rung.

Fix the guard in `leasequeue/queue.py` and all three pass. Edit the file again and
`rung status` marks the verdicts stale.

## How Rung keeps agents honest

The cheapest way for an agent to make a check pass is to weaken the check. Rung
closes the common shortcuts:

- A TLC counterexample stops a draft. It goes to a person, who decides whether it
  is a bug or a wrong spec; the model never edits a spec until it passes.
- Lean checks pass only when the build succeeds, the sources contain no `sorry`,
  `admit`, `axiom`, `native_decide`, `unsafe`, `implemented_by` or `extern`, and
  `#print axioms` shows every named theorem rests only on Lean's standard axioms.
- A TLA+ spec that explores at most one state fails as vacuous.
- A file that changes while a check runs voids the verdict.
- Ledger entries are hash-chained; rewriting history breaks `rung ledger`.

## Status

Engineering preview (0.1). Working: the CLI, ledger and laws; the TLC, Lean,
differential-test, command and evidence runners; drafting TLA+ and Lean with a
model. Next: counterexamples turned into regression tests and videos, trace
validation, and the portal. See [docs/ROADMAP.md](docs/ROADMAP.md), and
[docs/DESIGN.md](docs/DESIGN.md) for how and why it works.

## Credits

Rung stands on other people's ideas, and says so:

- **Verification skills and the feature map**, and the rule that a generated
  verification skill must run once before it counts, are Lauren Tan's
  ([pstack](https://github.com/cursor/plugins/tree/main/pstack)). Rung's runtime rung follows
  her approach; no pstack code is used.
- **Verification-guided development**, an executable Lean model differentially tested
  against production code, is how AWS built [Cedar](https://lean-lang.org/use-cases/cedar/).
- **Laws enforced by proof** come from [Bend 2](https://github.com/bendlang/bend)'s `LAWS.bend`.
- **Counterexample-driven repair** of TLA+ specs follows TraceFix and related work.

Rung is not affiliated with or endorsed by any of these projects.

## License

[Apache License 2.0](LICENSE).
