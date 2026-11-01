# Writing a runtime verification script

You write a verification script that drives a real application the way a user does and records
evidence. A program, not a person, will run it, with no one watching. Rung runs it as an `evidence`
check: it passes only if the script exits 0 **and** leaves artifacts in `$RUNG_EVIDENCE_DIR`.

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
  invocations, browser actions. Never internal functions or test-only endpoints.
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

## Evidence

Write everything into `Path(os.environ["RUNG_EVIDENCE_DIR"])`. Evidence must survive cleanup.

- A structured result, `result.json`: the feature, whether it passed, and each assertion with what
  was expected and what was observed. Also print it as the last line of stdout, so an agent can read
  the outcome without parsing logs.
- Commands: a transcript of each command and its output, `transcript.txt`.
- HTTP: each request and response as JSON, `http_exchanges.json`.
- Server logs: what the app printed while you drove it, `server.log`.
- Web UIs, with Playwright's sync API (`from playwright.sync_api import sync_playwright, expect`):
  - Launch Chromium headless and create the context with
    `browser.new_context(record_video_dir=str(evidence), record_video_size={"width": 1280, "height": 720})`.
  - Navigate with `page.goto(url, wait_until="networkidle")`; act through visible roles and text
    (`page.get_by_role("button", name="Save")`, `page.get_by_text(...)`), not CSS internals.
  - Take a full-page screenshot after each meaningful step:
    `page.screenshot(path=str(evidence / "01-home.png"), full_page=True)`.
  - Assert on what a user sees (`expect(page.get_by_text("Saved")).to_be_visible()`) and save the
    browser console's errors to `console.json`.
  - Call `context.close()` before `browser.close()`: Playwright writes the video only when the
    context closes. The video is a `.webm` file in the evidence directory.

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
- **Gotchas**: preconditions (seed data, login, feature flags) and anything that makes it flaky.

List the source files behind each feature. If a precondition is missing, the feature fails and the
reason goes under Gotchas; never weaken the check to make it pass.
