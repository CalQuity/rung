# Recipes

These recipes show the check combinations that pay off for common kinds of project.
Start with the first two rows of every recipe; add the rest where a mistake would be expensive.

## Every project

```bash
rung add command tests --run "pytest -q" --covers "src/**" "tests/**"
rung add scan secrets --tool gitleaks
rung add scan vulnerable-deps --tool osv-scanner
rung add deps deps
rung agents --hooks
```

When you fix a bug, prove the regression test catches it:

```bash
rung prove "pytest tests/test_refunds.py" --expect "refund exceeded charge"
```

## REST or CRUD API

- **Runtime evidence.** `rung draft runtime --intent "create, read, update and delete an order"`.
  The model writes `.rung/verify/drive.py`, which starts the server on a free port, drives the
  endpoints over HTTP and saves every request and response. Rung keeps the script only after it
  has passed.
- **Contract fuzzing.** If you have an OpenAPI schema, add a property-based API test as a command:
  `rung add command api-contract --run "schemathesis run openapi.yaml --url http://localhost:8000"`.
  Start the server inside a wrapper script if needed.
- **Laws.** Write the invariants that matter in `LAWS.md`: "a user can only read orders in their
  own account", "deleting an order is idempotent". Enforce each with a test, or with a Lean model
  plus a differential test when the logic is a small pure function, such as permissions.

## Web UI

- **Runtime evidence with video.** Ask `rung draft runtime` for a Playwright script, or write
  one. Save a recording into `$RUNG_EVIDENCE_DIR` and require it:
  `rung add evidence ui-flow --run "python3 e2e/checkout.py" --require .webm`.
- **Framework-specific static checks** run as commands, for example
  `rung add command react --run "npx -y react-doctor@latest --blocking error"`.
- **Accessibility** checks (axe through Playwright) are commands or part of the evidence script.

## Library

- Tests plus fix proofs (`rung prove`).
- Property-based tests (Hypothesis, fast-check, proptest) as `command` checks. They are the
  most general way to enforce a law.
- For a small pure core (a parser, a pricing rule, a state reducer), `rung draft lean <file>`
  writes a Lean model, proves its laws and sets up the differential test.

## Background jobs, queues, schedulers, sync

The bugs here need a specific interleaving: a lease expiring mid-task, a retry racing a
completion, two workers claiming the same job. Tests rarely reach them.

```bash
rung draft tla src/jobs/queue.py --intent "a job's result always comes from the worker holding its lease"
```

TLC explores every interleaving of the model. A counterexample stops the draft and comes to you
as a numbered trace. See [`examples/lease-queue`](../examples/lease-queue).

## Monorepo

Give each check the files it covers, so `rung check --changed` runs only what a change touches:

```toml
[[check]]
id = "web-tests"
kind = "command"
run = "pnpm --filter web test"
covers = ["apps/web/**", "packages/ui/**"]

[[check]]
id = "api-tests"
kind = "command"
run = "uv run pytest services/api"
covers = ["services/api/**"]
```

## Adopting scanners on an existing codebase

Turning on a scanner usually surfaces hundreds of old findings. Do not fix them all first.

```bash
rung baseline secrets        # accept today's findings (commit .rung/baselines/secrets.json)
rung check --changed         # or compare each branch with its merge base
```

From then on only new findings block. To accept one specific finding, use a waiver with a
reason and an expiry:

```bash
rung waive secrets 7a9fe0a79cbca79f --reason "test fixture with a fake token" --days 90
```
