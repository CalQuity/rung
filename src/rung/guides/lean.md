# Writing Lean 4 models with kernel-checked proofs

The goal is a small executable model of the code's **intended** behaviour, theorems that state
the laws that matter, proofs the kernel accepts with no escape hatches, and a program that lets
Rung compare the model with the real code on random inputs.

## Project

- Core Lean only: no Mathlib, no Batteries. `aesop`, `ring`, `linarith`, `norm_num` and
  `positivity` do not exist here. Core has `simp`, `omega`, `decide`, `grind`, `split`, `cases`,
  `induction`, `fun_induction`, `bv_decide`.
- Rung writes `lean-toolchain` itself, pinned to the installed Lean ({lean_version}). Write code for
  that version.
- The API changes between versions. When an identifier is unknown, assume it was renamed and look
  it up (`#check @Name`); do not rely on memory. Known changes:
  - `String.trim` no longer exists. `s.trimAscii` returns a `String.Slice`; use
    `s.trimAscii.toString` to get a `String` back.
  - `String.splitOn` and `String.toNat?` are unchanged.

## The model

- State as a `structure` deriving `Repr, DecidableEq`; actions as an `inductive`; behaviour as a
  total function `step : State → Action → State` (or `Option State` when an action can be refused).
- Match the code's arithmetic. `Nat` subtraction stops at zero; use `Int` where the code can go
  negative. Represent money in integer minor units (paise, cents), never floats.
- Everything a theorem mentions must be total and computable: no `partial def`, `noncomputable`,
  `unsafe`, `@[implemented_by]` or `@[extern]`. Prefer structural recursion; otherwise
  `termination_by` with a measure and `decreasing_by omega`.
- Avoid functions that need a proof argument: use `xs[i]?` with a default, or a total helper.

## Theorems

- State each law the developer cares about, plus the invariants that make the model trustworthy.
  Freeze statements before proving them, and never weaken one to make it prove.
- No trivial theorems. A statement that holds for any input regardless of the model, such as
  `0 ≤ n` for a natural number or `x = x`, proves nothing. Rung flags theorems whose whole proof is
  `rfl`, `trivial`, `Nat.zero_le _` or similar.
- Prefer statements about every input (`∀`) over checks of one example.

## Proving

- One tactic at a time. Unfold with `simp only [f]` or `unfold f`; case-split `if`/`match` with
  `split` (or `by_cases h : c` then `simp [h]`); close arithmetic with `omega`; finite facts with
  `decide`.
- Induction over a list with an accumulator needs `induction xs generalizing acc`.
- "failed to synthesize Decidable" means unfold the definition first or derive the instance.
- "motive is not type correct" means generalize the term first, or use `simp` instead of `rw`.
- When the induction step fails, check whether the leftover state is reachable. If it is, you found
  a bug in the model or the code; if not, strengthen the invariant.

## Scope

- State the scope: each bound, each assumption ("amounts fit in 64 bits") and what the model leaves
  out. A proof holds only inside it.
- When the covered code changes, the check is an error until a person reviews and confirms the
  model with `rung confirm`; `rung maintain <check>` proposes an update. Never weaken a theorem unless the promise
  changed on purpose and you can quote where.

## Trust gate (Rung enforces it)

- Never `sorry`, `admit`, `axiom`, `native_decide`, `apply?` or `#eval` left in a proof.
- `#print axioms` for every theorem may show only `propext`, `Classical.choice` and `Quot.sound`.

## The differential-testing program

- `Main.lean` reads stdin line by line. Each line is space-separated natural numbers; each number
  encodes one action (document the encoding in a comment, e.g. `0 = expire, 1 + 2w = claim by w`).
  It applies the actions from the initial state and prints the final state as one line of
  `key=value` pairs, in a fixed order.
- `drt_adapter.py` does the same with the real code: same encoding, same initial state, same output
  format. It must import and call the real functions, never reimplement them. Find the code
  relative to the adapter or the repository root (`Path(__file__).resolve().parents[n]`); never
  hard-code an absolute path such as a home or temporary directory, or it will not run elsewhere.
- Use `IO.getStdin`, `stream.getLine` (an empty string means end of input) and `IO.println`.
