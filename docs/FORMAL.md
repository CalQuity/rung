# Property tests, formal models, and laws

TLA+, Lean, and mutation-checked property tests are opt-in rungs for the parts of a codebase where a subtle edge case or interleaving is expensive: billing, refunds, permissions, leases, retries, and state machines.

## Property tests that are tested themselves

```bash
rung draft property pricing.py --intent "a total is never negative and equals the discounted sum"
```

Rung drafts Hypothesis (Python) or fast-check (TypeScript) properties in a disposable, write-guarded copy of your repository and checks them two ways:

1. **Against the real code:** any failing property is reported to you as a finding—never sent back to the model to weaken until it passes.
2. **Against deliberate bugs:** Rung mutates the source one token at a time (`<` to `<=`, `+` to `-`, `and` to `or`) and requires the properties to catch at least half of viable mutants (timeouts and compile errors do not count). Weak drafts go back to the model with the surviving mutants.

## TLA+ for concurrency and protocols

Requires Java 11+ and [`tla2tools.jar`](https://github.com/tlaplus/tlaplus/releases) (`rung doctor` checks both).

```bash
rung draft tla queue.py
```

- **Mirrors real guards:** actions copy the guards your code actually checks; TLC explores every reachable interleaving. A counterexample stops the draft and goes to you—the model never edits the spec to silence TLC.
- **SQL context:** when your code calls database functions (`supabase.rpc("claim_job")`, `select claim_job(...)`), Rung attaches the defining `*.sql` migrations to the prompt and covers them in the verdict.
- **Shown able to fail:** after a spec passes, Rung removes one action guard at a time and runs TLC on each mutant. At least one mutant must break a real invariant (not just `TypeOK`), or the spec is rejected as vacuous.
- **Readable counterexamples:** violations print as plain-language numbered steps next to the raw trace and export a Mermaid `stateDiagram-v2` diagram (`counterexample.mmd`).

## Lean 4 + differential testing for core logic

Requires [Lean via elan](https://lean-lang.org/install/).

```bash
rung draft lean pricing.py
```

Following AWS Cedar's verification-guided development pattern:

1. **Executable Lean model + proofs:** Rung pins the Lean toolchain, builds the project, rejects escape hatches (`sorry`, `admit`, `native_decide`, `unsafe`, `implemented_by`, `@[extern]`, custom `axiom`), and audits `#print axioms` on every theorem to allow only `propext`, `Classical.choice`, and `Quot.sound`.
2. **Differential random testing (`drt`):** runs the compiled Lean executable and your real production code on the same random inputs and shrinks any divergence to a minimal failing input.

See [`examples/lease-queue`](../examples/lease-queue) for a worked example where TLC catches a two-worker interleaving and Lean + DRT catches a three-step bug TLC misses.

## Models that cannot silently go stale

Each `tla` and `lean` check stores a committed record in `.rung/formal/<check>.json` binding the model to the exact commit and source digest it was last confirmed to describe.

When covered code changes, `rung check` does **not** rerun TLC on the old spec and report green—it reports drift and marks enforced laws `UNPROVEN`:

```console
$ rung check payments-tla
ERROR payments-tla (tla): the code changed since the model was confirmed at 29b079f
      changed: shop/payments.py
      next: rung maintain payments-tla
```

Two ways out of drift:

- **`rung maintain <check>`** shows the model the diff and commit messages, classifies each change, updates the model without weakening invariants or theorems unless cited evidence justifies a promise change, and rechecks (`in sync`, `updated`, `bug`, or `blocked`).
- **`rung confirm <check> --by NAME --reason TEXT`** records a human review confirming the model still describes the code.

Accepted formal models also write `.claude/skills/verify-<check>/SKILL.md` so coding agents know the promises, bounds, and maintenance rules.

## `LAWS.md`

`LAWS.md` lists plain-language invariants that must never break and the checks enforcing them:

```markdown
## refund-bounded
A refund never exceeds the original charge, even under concurrent retries.
- enforced by: refund-protocol, refund-model, tests
- harm: customers can be refunded more than they paid
- status: approved
```

Run `rung laws` to see which laws currently **hold** (approved by a person, enforced, and every enforcing verdict fresh). Agents may propose laws; only people approve them (`rung laws --approve <id>`).
