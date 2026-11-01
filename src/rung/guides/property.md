# Writing property-based tests that catch bugs

A property test states something that must hold for **every** input, and lets a generator search
for one where it does not: Hypothesis in Python, fast-check in TypeScript and JavaScript. Rung keeps
a property only if it passes on the real code **and fails** when the code is broken on purpose
(`<` turned into `<=`, `+` into `-`, `and` into `or`). A property that survives every such bug says
too little, however true it is.

## What makes a strong property

- State a law the developer cares about, not the implementation. Good shapes:
  - **Round trip:** `decode(encode(x)) == x`, `parse(render(x)) == x`.
  - **Invariant:** a result always satisfies a condition: a total is never negative, a list stays
    sorted, a balance equals the sum of its entries, a refund never exceeds the charge.
  - **Oracle:** compare with a slow, obviously correct version written in the test (a loop instead of
    a formula, a brute-force search), for small inputs.
  - **Metamorphic:** how the output must change when the input changes: adding an item never lowers
    a cart total; a stricter filter returns a subset; permuting the input does not change the sum.
  - **Idempotence and commutativity:** `f(f(x)) == f(x)`; merge order does not matter.
- Pin down exact values where the law gives them. "The result is a number" survives every mutant;
  "the result equals the sum of the line items, minus the discount, never below zero" does not.
- Include the boundaries the law names: zero, empty, one element, exact limits, equal values.
  Mutants such as `<` to `<=` are only caught by inputs on the boundary.
- Never reimplement the function under test and compare with it. That tests nothing.

## Generators

- Generate the domain, not arbitrary data: amounts as integers in cents within realistic bounds,
  valid identifiers, well-formed records. Build them with `st.builds`/`fc.record`, not with heavy
  filtering (`assume`, `.filter`), which makes the search slow and shallow.
- Keep sizes small (lists up to 20 elements, numbers up to 10^6); bugs appear in small cases and
  the shrinker reports the smallest one.

## The files

- Python: a pytest file using Hypothesis. Put `@settings(max_examples=200, deadline=None)` on each
  property. Import the code the way the project's own tests do.
- TypeScript/JavaScript: a test file for the project's runner (Vitest or Jest) using
  `fc.assert(fc.property(..., (...) => { ... }), { numRuns: 200 })`.
- Tests only: no changes to the code under test, no network, no files outside a temporary directory,
  no randomness other than the generator's.
- One test function per law, named after it, with the law as its docstring or a comment.

## When a property fails on the real code

That is a finding: either the code has a bug or the law is wrong, and only a person can say which.
Do not weaken the property to make it pass.
