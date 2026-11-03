# Writing a runtime verification script

You write a verification script that drives a real application the way a user does and records
evidence. A program, not a person, will run it, with no one watching. Rung runs it as an `evidence`
check: it passes only if the script exits 0 **and** leaves artifacts in `$RUNG_EVIDENCE_DIR`.

The script is the app-verification part of the project's verification kit, next to its linters,
tests and formal checks. It drives more than the happy path. Each feature gets named scenarios for its edge cases,
its state, its permissions and the faults that matter for it, and every one is reproducible from a
seed. People read what it recorded, so the evidence tells the story step by step.

## The contract

- Python 3. Use the standard library (subprocess, urllib, json, time, pathlib, socket, signal).
  If you need packages (Playwright for a web UI), list them in `requirements`; Rung installs them
  in an isolated environment for the run. Never install anything from inside the script.
- Start what the feature needs with the project's own documented commands (`npm run dev`,
  `uv run uvicorn ...`, a CLI build). Pick a free port. Start it in its own process group and stop
  only what you started, in a `finally` block; never kill processes by name.
- Wait until the app is actually usable: the port answers **and** a real request succeeds. Poll
  with a deadline (dev servers can take a minute on first compile) and fail with the server's last
  log lines if it never comes up.
- Exercise ONE user-facing feature end to end through its real interface: HTTP requests, CLI
  invocations, browser actions, in every scenario below. Never internal functions or test-only
  endpoints.
- Drive only routes that exist. The request lists the routes Rung found in the code (method, path,
  `file:line`, and the prefixes routers are mounted under); use those paths and no others. If the
  feature you were asked to drive is not among them, do not guess a URL and retry on 404s: say in
  `notes` that the app has no such feature, and drive the closest feature it does have.
- Keep paths portable: find the project relative to the script (`Path(__file__).resolve()`) or the
  repository root, and create scratch space with `tempfile`. Rung rejects a script that hard-codes
  a home or temporary directory path, because it would run on one machine only.
- Check the observable result (status codes, response bodies, text on the page, persisted state)
  and exit 1 with a message on stderr naming what was expected and what happened. Exit 0 only on
  success. Never weaken an assertion to make the script pass.
- Never print or save secrets; read credentials only from environment variables. If the feature
  needs a login you do not have, drive a feature that does not, and say so in `notes`.
- Isolate state so several agents can verify at once: a free port, and a temporary data directory,
  database file or schema per run. Never share or wipe a developer's own data.
- Prefer real dependencies. A mock records what a service did the last time someone looked, so a
  check against it proves less; if you must stub an external API, say so under the feature's
  Gotchas.
- If `RUNG_TARGET_URL` is set, drive that running deployment (a preview or per-change environment)
  instead of launching the app locally. This is how a system too large to start from one script is
  verified.

## Scenarios

Drive the feature through named scenarios, each with its own assertions. Cover every category that
applies to the feature; for one that does not (a shop with no sign-in has no `auth` scenarios), give
the reason in `result.json` under `not_applicable`. Rung rejects a script that only drives the happy
path.

- `happy`: the main path a user takes, start to finish. It must pass on the app as it is, because it
  shows the script can drive the feature at all. Assert on the element that carries the meaning (the
  total, the saved row, the message), not on every number or word on the page: a discount line shown
  as `-$5.00` is not a negative total. If the main path really is broken, put that assertion in its own
  `input` or `state` scenario named for the bug; Rung reports it to a person as a finding.
- `input`: empty, whitespace-only, very long and unicode/emoji input; case variations (`welcome5`
  against `WELCOME5`) and leading or trailing spaces; boundary values (zero, negative, the maximum,
  exactly at a limit, one past it); invalid formats; injection-looking strings (`<script>alert(1)</script>`,
  `' OR 1=1 --`), which must come back escaped and never run.
- `state`: repeating an action (the same coupon twice, a second coupon replacing the first); an empty
  cart or list; stale or expired state; refreshing after success; deep-linking straight to a mid-flow
  URL.
- `auth`, where the app has sign-in or permissions: signed out, the wrong user, a wrong or expired
  token. Each must be refused without leaking details.
- `fault`: the adverse conditions below.
- `robustness`: the flow works with the keyboard only; no console errors; no uncaught page
  exceptions (`page.on("pageerror")`); no 5xx response in the HAR or the recorded exchanges.

Assert what must hold, not what the app happens to do: a total is never negative, nothing happens
twice, input comes back escaped, an error is a user-facing message and never a stack trace. A
scenario that fails because the app is wrong is a finding for a person; Rung does not ask you to make
it pass, so never weaken an assertion to fit the app.

Make the scripts themselves robust:

- Select by role and label (`get_by_role`, `get_by_label`), not CSS internals.
- Wait for observable state (`expect(...).to_be_visible()`, polling a response), never a fixed sleep.
- Give each scenario fresh state: a new browser context, a fresh data directory or database file.
- One scenario's failure must not stop the others. Catch it, record its outcome in `result.json`, go
  on, and exit 1 at the end if any scenario failed.
- `python drive.py --feature <name>` drives one feature; `RUNG_SCENARIOS` (comma-separated scenario
  names) drives only those scenarios. A person uses these to look at one case.

## Seeds

Rung sets `RUNG_SEED` (an integer) for every run, and may run the script several times with
different seeds. Create `random.Random(int(os.environ.get("RUNG_SEED", "0")))` once and derive every
choice from it: which inputs to try, which backend call to delay and for how long, when to reload.
Never use the clock or unseeded randomness to decide anything. The same seed must give the same
scenarios and the same outcomes; Rung runs the same seed twice while drafting and rejects a script
whose outcomes differ. Record the seed in `result.json`.

## Faults

`RUNG_FAULTS` is a comma-separated list of fault scenario names to drive. When it is unset, drive the
fault scenarios the script declares (a `FAULTS` list at the top of the script); when it is set but
empty, drive none. Use these names, with the parameters chosen from the seed:

- `network`: with Playwright, `context.route("**/api/**", handler)` delays or aborts a seeded choice
  of the feature's backend calls (`route.abort()`, or wait a seeded number of milliseconds, then
  `route.continue_()`); `context.set_offline(True)` mid-flow, then back online.
- `double-submit`: click an action that must happen once (pay, send, place order) twice quickly, or
  from two tabs at once, then assert it happened once.
- `reload`: reload the page or go back mid-flow, then assert the state is still right and nothing was
  applied twice.
- `restart`: kill the app's process group mid-flow (`os.killpg`), restart it with the same data
  directory, continue, and assert the state is consistent.
- `clock`: install Playwright's fake clock (`page.clock.install()`) and fast-forward
  (`page.clock.fast_forward("25:00:00")`) to drive expiry and session timeouts.
- `dependency-down`: start the app pointing a dependency (database, cache, payment API) at a dead
  port, and assert the user sees an error message, not a stack trace or a hang.

Drive only the faults that matter for the feature. Under every fault, assert the invariants: nothing
happened twice, no state was lost or corrupted, and the user saw a clear error, never a stack trace.
For an HTTP API or a CLI, inject the same faults with the standard library: kill and restart the
process, send a request twice concurrently from threads, or put a small `http.server`-based proxy in
front of the app that delays or drops a seeded choice of requests.

## result.json

Write `result.json` into the evidence directory, and print it as the last line of stdout:

```json
{
  "feature": "apply a coupon at checkout",
  "seed": 4242,
  "passed": false,
  "not_applicable": {"auth": "the checkout page has no sign-in"},
  "scenarios": [
    {"name": "happy", "category": "happy", "faults": [], "passed": true,
     "assertions": [{"name": "total drops to $32.00", "expected": "$32.00", "observed": "$32.00",
                     "passed": true}]},
    {"name": "reload", "category": "fault", "faults": ["reload after applying WELCOME5"], "passed": false,
     "assertions": [{"name": "coupon applied once after reload", "expected": "$32.00",
                     "observed": "$27.00", "passed": false}]}
  ]
}
```

Each scenario has a `name`, a `category` (happy, input, state, auth, fault or robustness), the
`faults` it injected as plain descriptions, whether it `passed`, and its `assertions`, each with what
was expected and what was observed.

When the project has an approved feature map (`.rung/verify/features/`), drive its approved features
with the recipes it gives. Name each scenario after the sub-feature ID it proves (`coupon-apply`), or
list the IDs it proves under `covers`, and record each artifact's feature and entry point. A
sub-feature the run cannot reach goes under `unreachable` with the precondition it lacked, e.g.
`"unreachable": {"pay-card": "the Pay button is disabled: payment is not built yet"}`; it is never
reported as proven through another path.

## Evidence

Write everything into `Path(os.environ["RUNG_EVIDENCE_DIR"])`. Evidence must survive cleanup.

- A structured result, `result.json`, as above.
- Group each scenario's files in a subdirectory named after the scenario
  (`$RUNG_EVIDENCE_DIR/reload/`), so the evidence page shows them under that scenario.
- Commands: a transcript of each command and its output, `transcript.txt`.
- HTTP: each request and response as JSON, `http_exchanges.json`.
- Server logs: what the app printed while you drove it, `server.log`.
- Web UIs, with Playwright's sync API (`from playwright.sync_api import sync_playwright, expect`):
  - Launch Chromium headless and create one context per scenario, recording a video and a HAR:
    `browser.new_context(record_video_dir=str(scenario_dir), record_video_size={"width": 1280, "height": 720},
    record_har_path=str(scenario_dir / "network.har"))`.
  - Record a Playwright trace in each context:
    `context.tracing.start(screenshots=True, snapshots=True, sources=True)` before driving, and
    `context.tracing.stop(path=str(scenario_dir / "trace.zip"))` before closing, also when the scenario
    fails. A person replays it with `npx playwright show-trace trace.zip` or at trace.playwright.dev.
  - Navigate with `page.goto(url, wait_until="networkidle")`; act through visible roles and labels
    (`page.get_by_role("button", name="Save")`, `page.get_by_label(...)`), not CSS internals.
  - Take a full-page screenshot after each meaningful step, named after the step number and what it
    proves, so the gallery reads as a story:
    `page.screenshot(path=str(scenario_dir / "03-coupon-applied-total-32.png"), full_page=True)`.
  - Assert on what a user sees (`expect(page.get_by_text("Saved")).to_be_visible()`) and save the
    browser console's errors and page exceptions to `console.json`.
  - Call `context.close()` before `browser.close()`: Playwright writes the video and the HAR only when
    the context closes. The video is a `.webm` file.

## Recording with your own browser or computer-use tool

A script is the default because anyone can re-run it. When a script cannot reach the feature (a
login flow, a native app, a canvas UI, an OAuth redirect), drive it yourself instead, with whatever
the session has: Claude in Chrome (record a GIF with `gif_creator`, screenshots), Codex's browser or
computer use, Claude computer use, or Playwright MCP. Then:

- Declare the check without `run`: `rung add evidence <id> --covers "<files behind the feature>"
  --require .gif` (or `.png`, `.webm`). Narrow covers matter: the attestation holds until one of them
  changes.
- Save the recording and screenshots to files, then record them:
  `rung attest <id> demo.gif step-2.png --by claude-in-chrome --note "Saved a draft invoice; it
  appeared in the list with status Draft"`.
- The note states what you drove and what you saw, as plainly as an assertion. If you did not see
  the expected result, do not attest; report the bug.

Rung records who attested and hashes the files, but it cannot re-run a recording, so this is weaker
than a script: say in your summary that the evidence is attested.

## Structure of drive.py

Organise the script as small functions a later agent can reuse: `launch()` (start what is needed and
wait until usable), `doctor()` (a read-only check that the running instance is the right one and
healthy), one `drive_<feature>()` per mapped feature, and `cleanup()` (stop only what `launch`
started, never deleting evidence). `python drive.py` drives the first feature; `python drive.py
--feature <name>` drives another. Drive the real path a user takes; a proof through one convenient
entry point is incomplete when the map lists others.

## The feature map

Write `FEATURES.md` with one section per user-facing feature (the top 3-5), each with these headings:

- **How a user reaches it**: the route, command, menu or button.
- **How the script drives it**: the `drive_<feature>` function and the selectors or requests it uses.
- **What proves it works**: the observable end state the script asserts.
- **Scenarios**: each scenario by name and category, with what it asserts; and each category that
  does not apply, with the reason.
- **Gotchas**: preconditions (seed data, login, feature flags) and anything that makes it flaky.

List the source files behind each feature. If a precondition is missing, the feature fails and the
reason goes under Gotchas; never weaken the check to make it pass.
