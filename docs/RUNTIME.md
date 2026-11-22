# Runtime verification & feature maps

Rung can drive your running app like a user—through Playwright for web UIs, or HTTP and CLI calls for APIs and tools—and bind screenshots, videos, traces, and structured assertions to the exact code revision it checked.

## 1. Draft and approve a feature map

```bash
rung map
rung map --approve coupons checkout --not-built pay
```

`rung map` reads routes, pages, handlers, and imported modules to write `.rung/verify/features/`:

- `README.md` — how to launch the app, driving conventions, and proof rules.
- One file per user-facing feature with sub-feature IDs, user entry points, driving recipes, and gotchas.
- `.rung/verify/features/index.html` — a card per feature showing approval state, latest results, and screenshots once a drive script has run.

Approval pins digests of each feature file and the source code it describes. When either changes, `rung kit` and `rung draft runtime` flag the drift; run `rung map --redraft` to refresh changed features while keeping existing approvals, `not-built`, and `drop` decisions.

## 2. Draft a drive script

```bash
rung draft runtime --require .png
# Or target one flow directly:
rung draft runtime --intent "create, list and delete a todo through the HTTP API"
```

Rung generates `.rung/verify/drive.py`, tests it in a disposable copy under an OS write guard (`sandbox-exec` on macOS, Bubblewrap on Linux), verifies determinism across two runs of the same seed, and verifies that breaking application sources causes at least one named assertion to fail.

- **Six scenario categories:** `happy`, `input` (empty, long, Unicode, boundary, invalid), `state` (repeats, reloads, stale state, deep links), `auth`, `fault`, and `robustness` (keyboard-only, zero console errors, no 5xx). A category that does not apply requires a stated reason.
- **Findings go to people:** if the happy path passes while an edge-case or fault scenario fails, Rung registers the check and reports the finding to you instead of letting the model weaken the script.
- **Project verification skill:** generates `.claude/skills/verify-<project>/` so coding agents re-drive the app after user-facing edits.

## 3. Seeds, faults, and replay

Rung passes `RUNG_SEED`, `RUNG_FAULTS`, and `RUNG_SCENARIOS` to the drive script. Set `runs = 3` in `rung.toml` to run multiple seeds (`seed`, `seed+1`, `seed+2`).

When a seed fails, Rung prints the exact reproduction command (which runs without overwriting your recorded verdict):

```bash
rung check runtime --seed 4242
rung check runtime --seed 4242 --scenario reload
```

Supported external fault scenarios include slow/aborted network calls and offline mode (`context.route`, `set_offline`), double submits, mid-flow reloads, process restarts on the same database, fake clocks, and dead upstream ports.

## 4. Evidence pages and artifacts

Each run writes to `.rung/evidence/<check>/<timestamp>/seed-<N>/` and generates a self-contained `index.html` (inline CSS, relative links, no network requests) with:

- Scenario outcomes and expected vs. observed assertions
- Step-ordered screenshots and video (`--require .webm`)
- Links to Playwright traces (`--require .zip`, open with `npx playwright show-trace trace.zip`) and HAR logs (`--require .har`)
- The exact replay command

Regenerate the page for the latest run with:

```bash
rung evidence runtime
```

> **Privacy:** HARs, traces, videos, and screenshots capture what the browser sees. Drive with test accounts and synthetic data before uploading artifacts in CI.

## Manual attestation (when a script cannot reach the app)

For flows a script cannot automate (e.g., native apps or external OAuth), declare an `evidence` check without `run` and attach captures with:

```bash
rung add evidence checkout-manual --covers "frontend/**" --require .png
rung attest checkout-manual screenshot.png --by claude-in-chrome --note "verified coupon total"
```

Attested verdicts record who captured them and expire when covered files change, but because Rung cannot re-run them, `rung gate` does not accept them as repeatable merge checks.
