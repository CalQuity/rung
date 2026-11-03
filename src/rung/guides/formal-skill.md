# Verify {check_id}

`{check_id}` is part of the formal-methods side of {project}'s verification kit:
{what}. Rung checks it with {checker} and records the verdict against the exact code it covers.

## What it promises

{promises}

## What it covers

- Model: {model_files}
- Code: {source_files}
- Functions: {functions}

## Bounds and assumptions

{scope}

A pass holds only inside these bounds and assumptions.

## Run it

`rung check {check_id}`. A pass is fresh only while the covered code is unchanged; `rung status`
shows when it went stale.

## A failure: real bug or modeling error?

{triage}

## When the code changes

If `rung check {check_id}` reports that the code changed since the model was confirmed, the model
may no longer describe it, and laws it enforces show as UNPROVEN. Run `rung maintain {check_id}`:
it classifies each change, updates the model from the code, reruns the checker and reports one of
in sync, updated, bug or blocked. Never edit an invariant, property or theorem to make a check pass.
If you reviewed the model by hand and it still describes the code, record that with
`rung confirm {check_id} --by "$(git config user.name)" --reason "{confirm_reason}"`.

## Confirmed

{confirmed}
