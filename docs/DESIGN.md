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

## The ladder

| Rung | Check kind | Evidence |
|---|---|---|
| Tests | `command`, plus `rung prove` for bug fixes | Exit status; a fix proof shows the new test fails on the merge base |
| Security | `scan` (any SARIF analyzer), `deps` | New findings relative to the merge base or a baseline; registry facts for new packages |
| Architecture | `command` running a linter or import-boundary tool | Exit status |
| Runtime | `evidence` running a script that drives the real app | Hashed artifacts: transcripts, request/response pairs, screenshots, video |
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

### Runtime evidence that proves itself

`rung draft runtime` has the model write a drive script and a feature map. Rung runs the script
as an `evidence` check and feeds failures back until it passes and records evidence; only then
is it registered. This is the rule from Lauren Tan's pstack, that a verification skill never
executed is only a draft, enforced by the checker rather than stated in a prompt.

### Agents

Guidance (a skill and an AGENTS.md section) says how to use Rung and which shortcuts are off
limits. Enforcement is a Claude Code `Stop` hook: when an agent tries to finish, `rung hook
stop` checks whether the checks covering its uncommitted edits are fresh. If not, the agent is
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

## Laws

`LAWS.md` holds the rules the software must never break, one sentence each, in words
a non-specialist can approve. Each law names the checks that enforce it. A law is
**held** only when a person has approved it and every enforcing check has a fresh pass.

This generalises Bend 2's `LAWS.bend`. Bend proves laws about programs written in Bend;
Rung lets a law about a Python or TypeScript service be enforced by a TLA+ invariant, a
Lean theorem plus a differential test, and a test suite, all at once. Agents may
*propose* laws; only people approve them.

## Trust boundaries

- Rung never stores credentials. CLI providers reuse the tool's own login; HTTP
  providers read a key from an environment variable named in `rung.toml`.
- Drafting with Claude Code runs with all tools disabled (`--tools ""`); with Codex it
  runs in a read-only sandbox. The model reads only the source excerpts Rung sends.
- Model replies may only write files inside the chosen output directory.
- Spec and proof checking runs locally. Nothing is uploaded. The `deps` check sends package
  names to PyPI and npm; the scanners make whatever network calls they make themselves
  (zizmor runs with `--offline`).
- Gitleaks always runs with `--redact`.

## What Rung is not

- Not a proof assistant or a model checker. It drives TLC and Lean.
- Not a guarantee of correctness. It guarantees that the stated laws were checked
  against the exact code in the ledger. A law that says the wrong thing is checked just
  as faithfully, which is why laws need human approval.
