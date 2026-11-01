- Before saying a change is done, run `rung check --changed` and read the result.
  "The build passed" is not evidence; a fresh verdict in the ledger is.
- A TLC counterexample is either a bug in the code or a wrong spec. Fix the code.
  Only change a spec or an invariant when a person has agreed the spec was wrong.
- Never weaken a law, an invariant, a theorem, a property or a test to make it pass.
  Laws in LAWS.md that are `approved` are not yours to edit. Never add a waiver.
- Never use `sorry`, `admit`, `axiom`, `native_decide`, `unsafe`, `implemented_by`
  or `extern` in Lean. Rung rejects them, and the rejection is correct.
- When you fix a bug, prove the test catches it: `rung prove '<test command>'`.
- New invariants go into LAWS.md as `status: proposed`, one plain sentence each.
- Do not add a dependency without checking it exists and is established;
  `rung check` vets new packages against their registry.
- Writing TLA+ or Lean? Follow the `rung-tla` and `rung-lean` skills.
