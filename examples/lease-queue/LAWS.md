# Laws

Rules this software must never break. Each law names the Rung checks that
enforce it; `rung laws` shows which laws currently hold. A law an agent proposes
stays `proposed` until a person reads it and changes it to `approved`.

## result-from-lease-holder
A job's recorded result always comes from the worker that holds its current lease.
- enforced by: lease-protocol, lease-model-proofs, impl-matches-model
- status: approved

## only-holder-completes
Only a worker holding a valid lease can complete a job.
- enforced by: lease-model-proofs, impl-matches-model
- status: approved
