# Changelog

All notable changes to Rung. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow the policy in [CONTRIBUTING.md](CONTRIBUTING.md#versioning).

## Unreleased

### Changed
- The `osv-scanner` preset runs `osv-scanner scan source --no-resolve`. Without it, a manifest naming
  a package the registry does not have (`stripe-checkout-retry-pro` in `requirements.txt`) failed
  transitive resolution, and the whole vulnerable-dependencies check was an ERROR. Manifests and
  lockfiles are now scanned as written; the `deps` check reports the missing package. A check that
  sets its own `run` is unaffected.
- `rung prove` with a pytest command judges each test the branch adds or changes, from a JUnit XML
  report of both runs. A new test that passes (or skips) on the merge base fails the proof, by name,
  even when another test in the same file fails there. `--json` and the proof's ledger entry gain
  `cases` (each test with its outcome on the merge base and the branch). Other runners keep the
  file-level rule.
- `rung draft tla` and `rung draft lean` cover the SQL files they added to the prompt (see below), so
  a changed definition stales the verdict. `--json` gains `context` and `warnings`.

### Added
- `rung draft tla` and `rung draft lean` find the database functions the sources call (`.rpc("name"`,
  `self._rpc("name"`, and `select name(` / `call name(` in SQL strings), search the repository's
  `*.sql` files for their `create [or replace] function` statements, and add every defining file to
  the prompt, oldest commit first, telling the model the newest definition is the one that runs. The
  command prints which files it added. A call whose definition cannot be found is a warning, on the
  terminal and in the result, because the model then has to assume the call is atomic.
- `rung draft runtime` shows the model the routes the app serves, with `file:line`: FastAPI,
  Starlette and Flask decorators (with an `APIRouter(prefix=...)` or `Blueprint(url_prefix=...)` in
  the same file), Express, Hono and Koa handlers, router mounts, and the Next.js app and pages
  routers. The model is told to drive only listed routes and to say in its notes when the intent
  names a feature that does not exist.

### Fixed
- A drafted spec, Lean adapter, drive script or property test that hard-codes a home or temporary
  directory path (`/Users/<name>/`, `/home/<name>/`, `C:\Users\`, `/tmp`, `/var/folders/`) is sent
  back to the model as a repair error, before Rung runs it. Before, a Lean adapter could fall back to
  the drafting developer's own checkout and fail on everyone else's machine.

## 0.1.3 (2026-11-01)

### Fixed
- `rung draft property`: a plain `assert` the model wrote that fails on the real code is now a
  finding for a person, not a broken test. Before, a failure without Hypothesis's "Falsifying
  example" marker was sent back to the model to rewrite, up to four times, which could hide the bug
  the tests had found. A mutant that stops the module compiling is still not counted as caught.

### Documentation
- The README describes the runtime verification kit: the drive script, the `FEATURES.md` feature
  map, the project verification skill for coding agents, and the recordings each run leaves.

## 0.1.2 (2026-11-01)

The first release on PyPI, as `rung-cli`.

### Changed
- Verdicts are one small file per check, `.rung/verdicts/<check>.json`, bound to a digest of the
  files the check covers. They replace the shared, hash-chained `.rung/ledger.jsonl`, so parallel
  branches no longer conflict and the files stay a few hundred bytes. Per-file digests and the run
  history move to the git-ignored `.rung/cache/`. Re-run your checks after upgrading.
- Commands run without a shell, and shell syntax (`&&`, `|`, `;`, redirects, a leading `cd`) is a
  configuration error. Use `cwd`, or `sh -c "..."` explicitly.
- An option a check kind does not understand is a configuration error, with a suggestion.
- Scan and deps verdicts expire (`max_age_days`, default 1 day for deps and osv-scanner), because
  advisory databases change without any file changing.
- The Claude Code Stop hook compares against the merge base, so committing edits does not hide them.

### Added
- A setup skill for coding agents, published as a Claude Code plugin (`/rung:setup`).
- The GitHub Action lists pull request edits to policy files (rung.toml, LAWS.md, waivers,
  baselines, scanner configs); `policy-changes: fail` fails the gate on them.
- A pre-commit hook (`.pre-commit-hooks.yaml`).
- A `shortcuts` check that blocks new type-checker silencers (`@ts-ignore`, `# type: ignore`,
  `as any`), lint disables, skipped, focused and deleted tests, lowered coverage thresholds and
  relaxed type-checker settings. `rung init` adds it, plus the type checkers and linters the
  project already configures.
- `rung draft property`: Hypothesis or fast-check properties for a law, kept only when they pass
  on the real code and catch at least half of the deliberate one-token bugs planted in a disposable
  copy of the project (under pytest, Vitest, Jest or `node --test`).
- Drafted checks record the model: the one Claude Code reports having used, or Codex's configured
  model, marked as such (`drafted_by`).
- Attested evidence: an `evidence` check without `run` takes recordings an agent made with its own
  browser or computer-use tool (Claude in Chrome, Codex, computer use), or a person made by hand,
  through `rung attest <check> <files> --by <tool> --note ...`. The verdict holds until a covered
  file changes.

### Fixed
- TLA+ checks fail when the config declares no invariant or property, instead of passing.
- Lean checks audit every theorem's axioms, and reject `sorry` warnings, `sorryAx` and
  attribute-prefixed axioms.
- Redacted secret findings are fingerprinted by the flagged text, so swapping one secret for another
  on the same line is a new finding. Every scanner's messages are redacted before they are stored.
- Rung's own `.rung/` state is never scanned for findings.
- `requirements` includes cannot loop or leave the repository; malformed manifests are errors.
- Zero-byte files no longer count as evidence.
- Unexpected errors exit 2 with a one-line message (`RUNG_DEBUG=1` shows the traceback).

## 0.1.1

### Changed
- CI pins every action by commit SHA, and Rung checks its own pull requests with its own action.

## 0.1.0

- First tagged version: command, evidence, scan, deps, TLA+, Lean and differential-testing checks,
  the verdict ledger, `LAWS.md`, model-drafted specs and proofs, agent skills and the GitHub Action.
