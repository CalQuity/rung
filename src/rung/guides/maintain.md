# Maintaining a model when the code changes

A model was confirmed to describe the code at one commit. The code has changed since. Your job is
to make the model describe the code as it is now, so the checker's verdict is about real code again.

## Classify every change first

For each change in the diff, pick exactly one:

- `no-effect`: nothing the model describes changed (a rename, logging, formatting, an unrelated
  function in the same file).
- `model-must-change`: the code now does something the model does not say: a new step, a new or
  removed guard, a lock, a reordered `await`, a new retry. Change the model to match.
- `promise-changed`: the guarantee itself changed on purpose. Quote the evidence in `evidence`, with
  where it is: a doc comment, a test, a changelog entry or a commit message. Without such a quote,
  it is not this class.
- `promise-may-be-broken`: the change may break the guarantee. Model the code faithfully anyway and
  keep the invariant; the checker decides.

## Rules

- Update the model from the code, never from a checker result. You will not see one.
- Never remove, rename or rewrite an invariant, property or theorem, and never add a state
  constraint or turn off deadlock checking, unless a change is `promise-changed` with quoted
  evidence. Rung rejects the reply otherwise.
- Keep every `\* Source: file:line function` comment pointing at the current line.
- Return only the files that change, each with its complete contents. Return no files when the
  model already describes the code.
- Keep the plain-language labels, invariant statements, harms and scope current for the code as it
  is now; return all of them, not only what changed.

Your classifications and model edits are proposals. Rung does not treat a `no-effect` opinion or a
`promise-changed` citation as source confirmation or law approval. A person reviews the complete
model definition, assumptions, bounds, adapter and operation scope before confirming it.
