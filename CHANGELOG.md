# Changelog

All notable changes to Rung. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow the policy in [CONTRIBUTING.md](CONTRIBUTING.md#versioning).

## Unreleased

### Added
- `rung init --dry-run` previews file writes and commands. `--refresh` appends newly detected checks,
  and `rung doctor --strict` checks the executables the kit declares.
- `rung plan --path PATH` and `rung plan --changed` show which check scopes match a change.
- `rung gate` reruns the merge checks, checks protected policy before running repository code, and
  requires fresh evidence for every check enforcing an approved law.
- Adopting repositories can opt in to running their PR gate in a private Daytona sandbox with a
  reviewed snapshot and offline dependencies.
- Structured runtime checks validate the seed, scenario names, and assertion outcomes on every run.
- `rung agents` installs CLI workflows for Claude Code and Codex. Versioned JSON exposes scope
  gaps, replay commands and artifact digests. Terminal runs group results and preserve plain-text
  labels with `NO_COLOR`.
- `rung agents --codex-hooks` installs a project-local Stop hook for review in Codex.
- `examples/acme-store`: a small shop to try Rung on in a minute, built the way a team would build
  one today (FastAPI and SQLite behind a Next.js, TypeScript and Tailwind front end), with a few bugs
  planted for Rung to find. Spoilers are in its `SOLUTIONS.md`.

### Changed
- Generated workspace checks use repository-wide coverage. The survey resolves workspace package
  managers and records test suites for multiple ecosystems in the same directory.
- The GitHub Action blocks policy changes by default. Evidence uploads and write-token comments
  are opt-in. Verdicts with missing or modified artifacts now read as stale.
- Pytest regression proofs reject setup failures and missing case reports. Other runners require
  `--expect` to identify the failure on the merge base.
- Scanners distinguish configured finding exit codes from failed analyses. Gitleaks fingerprints
  ignore unstable SARIF column offsets. Review and regenerate Gitleaks baselines and waivers made
  with earlier versions when you migrate.
- Verdicts now cover executable permissions and symbolic links as well as bytes. Re-run checks
  once after upgrading to refresh older verdicts.
- Formal checks disclose when their result covers only a model. An approved law needs a confirmed
  source link or an implementation check to hold and pass the merge gate.
- `rung kit` no longer marks "Driving the app" ok when the feature map is approved but no drive
  script exists yet; it needs attention, and the next step is `rung draft runtime`.
- `rung init` names the project by its folder when run from inside it, instead of printing the full path.
- Reviewing the feature map, `A` approves the current feature and every one after it.
- `rung init` shows the drafted feature map in three lines per feature (what it does and its
  sub-feature IDs) and points to the full map and its page before asking for decisions; `rung map
  --brief` does the same. A drive-script draft with more than 12 scenarios prints the counts and the
  failing ones instead of a line per scenario.
- `rung check` on a drive script reads on its own: a failure names the scenarios that failed and lists
  each failed assertion with what it expected and what the app did (up to 12, the rest on the
  evidence page), instead of the script's last output lines. More than six evidence files are
  summarised as a count and their directory; every file is still hashed into the verdict. With one
  seed, the per-seed line no longer repeats the headline.
- The model drafting a feature map or drive script now reads the app itself: the files that declare
  routes and pages, and two levels of the project's own modules they import, Python and TypeScript
  alike (`from app import pricing`, `@/components/...`, relative imports), in sub-projects such as
  `backend/` and `frontend/`. Empty `__init__.py` files and design-system primitives
  (`components/ui/`) are left out to make room.
- The survey no longer suggests UI components as TLA+ or pure-core candidates, and counts the words of
  code that moves money (charge, paid, payment, refund, checkout) as signs of a protocol worth
  modelling. When an app has both a web UI and an API, the kit drives the web UI.
- The Claude Code plugin lives in its own folder, `plugin/` (its manifest, the `setup` skill, a README
  that says what the skill runs and what it sends, a license and an icon), so it can be listed in
  Anthropic's plugin directory. Install with `/plugin marketplace add CalQuity/rung` and
  `/plugin install rung@rung`; the marketplace keeps its name, so existing installs update in place.
  The setup skill's raw URL is now `.../main/plugin/skills/setup/SKILL.md`.

## 0.3.0 (2026-11-04)

### Added
- **`rung kit`** shows the project's verification kit part by part: linters and type checkers, tests,
  property tests, security gates, driving the app, formal models, and the agent skills and hook. Each
  part lists its checks and their state (fresh, stale, failing, drifted, never run), or, when the part
  is empty, why and the command that fills it. It ends with the one command to run next: a failing
  check first, then a drifted model, then stale checks, then the missing parts and proposed laws.
  `--json` gives the same.
- **Feature map you approve.** `rung map` drafts `.rung/verify/features/` from the code: a README
  index (baseline preconditions, driving conventions, proof and skip reporting) and one file per
  user-facing feature with four sections: sub-features with IDs, every user entry point, the
  driving recipe, gotchas. Rung checks the shape and sends a broken map back to the model. A person
  then approves each feature, marks it not built yet, or drops it, in the terminal or with
  `--approve`, `--not-built` and `--drop`; `.rung/verify/features/index.html` shows the map as cards,
  with each sub-feature's latest result and screenshots once a script has run.
  `rung draft runtime` drives only approved features, requires a scenario per approved sub-feature
  (named after its ID or listing it under `covers`) or a reason under `unreachable`, and refuses a map
  with nothing approved or features changed since approval. Approval pins digests of each feature's
  file and source files; `rung map --redraft` refreshes the map, keeps approval for unchanged features
  and asks again for changed ones. `rung kit` shows the map's state and the step it needs.
- **`rung init` builds the whole kit.** Besides the checks it recorded before, it installs the agent
  skills, AGENTS.md rules and the Claude Code Stop hook (`--no-agents` to skip), then offers to draft
  each part a model writes and the kit lacks: a feature map to approve and then a script that drives
  the approved features (with `--yes`, the map is drafted and waits for approval), property tests for a pure
  core, a TLA+ model of stateful code and a Lean model of a pure core (the formal ones only when Java
  and TLC, or Lean, are installed). For each it lists the candidate files it found and drafts only
  the one the person picks. `--yes` drafts every part with its top candidate without asking;
  `--no-drafts` drafts nothing. Without a terminal, or when the provider is not logged in, it drafts
  nothing. It ends with the kit and the next command. Running it again fills the parts still missing.

### Changed
- The survey finds web apps and APIs served with the standard library (`http.server`, `wsgiref`,
  Node's `http.createServer`), which have no framework in their manifest, and counts a module with a
  single pure function as a candidate for property tests or a model. A module that serves requests
  is no longer counted as a pure core.
- `rung init` ends with the kit instead of the recommendation list; `rung plan` still prints that.
- `rung init` installs the agent skills, AGENTS.md rules and the Claude Code Stop hook by default.
  To keep the 0.2 behaviour, pass `--no-agents` (and run `rung agents` when you want them).
- Once a project has a feature map, `rung draft runtime` drives only its approved features and stops
  with an error while none is approved or an approved feature changed since approval. Projects without
  a map (an existing `.rung/verify/FEATURES.md`) draft as before. When the model returns a drive
  script, a failed draft now says why the last run failed, and the model sees each failed assertion
  of a failing happy path with what it expected and observed.

## 0.2.0 (2026-11-03)

### Added
- **Seeded runs.** Evidence checks pass `RUNG_SEED` to the drive script. New options: `runs` (default
  1) runs the script once per seed, `seed` sets the first seed (default: derived from the check id, so
  runs are reproducible). Each run writes to its own `seed-<N>/` directory; the check passes only if
  every run passes. A failure names the seed and prints the command that reproduces it,
  `rung check <id> --seed N`, which runs only that seed and does not record a verdict. The verdict's
  details and `rung check --json` gain `runs` (seed, verdict, scenarios, failed assertions),
  `failing_seed`, `failing_scenario`, `reproduce` and `report`.
- **Scenarios and faults.** New evidence options `faults` and `scenarios`, passed to the script as
  `RUNG_FAULTS` and `RUNG_SCENARIOS`; `rung check <id> --scenario NAME` runs one scenario. When the
  script writes `result.json`, a run fails if it records a failed scenario, reports a different seed,
  or has no scenario for a requested name. The runtime guide now asks for named scenarios per feature
  in six categories (happy, input, state, auth, fault, robustness) with fault injection through
  Playwright (`context.route`, `set_offline`, `page.clock`), double submits, reloads, server restarts
  and a dependency on a dead port.
- **Drafting checks.** `rung draft runtime` rejects a script that does not read `RUNG_SEED`,
  `RUNG_FAULTS` and `RUNG_SCENARIOS`, writes no `result.json`, drives only the happy path, or leaves
  a category without a scenario or a stated reason. It runs the accepted seed a second time and sends
  the script back if any assertion's outcome changed. A scenario the app fails while the happy path
  passes is reported as a finding: the check is registered, the command exits 1, and the model is not
  asked to make it pass. `--require .zip` and `.har` are explained to the model as a Playwright trace
  and a HAR.
- **Evidence page.** Every scripted evidence run writes a self-contained `index.html` (inline CSS,
  relative links, no network): each seed's result, scenarios, faults and assertions with expected and
  observed values, screenshots grouped by scenario in step order, the video, links to the trace and
  HAR, and the reproduce command. `rung evidence <check> [--seed N] [--out DIR]` writes it again.
  `rung check` prints its path.
- `rung report` (Markdown and the PR comment) lists each evidence check's seeds and outcomes, the
  failing seed and scenario, the reproduce command, categories marked not applicable and the evidence
  page. `--evidence-url` adds a link to the recordings.
- The GitHub Action uploads `.rung/evidence/` as the `rung-evidence` workflow artifact and links it
  from the report comment and step summary. New input `upload-evidence` (default `"true"`).
- Formal models that cannot silently go stale. Each `tla` and `lean` check records, in a committed
  `.rung/formal/<check>.json`, the commit and a digest of the code its model was last confirmed to
  describe. When that code changes, `rung check` no longer reruns TLC on the unchanged spec and calls
  it a pass: the check is an error ("the code changed since the model was confirmed at <commit>; the
  model may no longer describe it") naming the changed files and ending with the command to run,
  `rung status` shows it as `drifted`, and laws it enforces are `unproven` with the reason.
- `rung maintain <check>` gives the drafting model the spec or model files, the diff of the covered
  files (and SQL definitions) since the confirmed commit and the commit messages, has it classify
  each change (no effect, the model must change, the promise changed on purpose with quoted
  evidence, the promise may be broken), and updates the model from the code. A reply that removes or
  rewrites an invariant, property or theorem, or adds a state constraint, without such evidence is
  sent back. Rung then reruns the checker and, for TLA+, the fail-ability check, and reports one
  outcome: in sync, updated, bug or blocked. The confirmed commit moves forward for in sync, updated,
  and a bug found on an updated model; a blocked attempt is kept in `.rung/cache/maintain/<check>/`
  and the model files are left as they were.
- `rung confirm <check> --by NAME --reason TEXT` records that a person reviewed a TLA+ or Lean model
  against the current code, then reruns the check.
- Fail-ability for TLA+: after a draft passes, and on `rung maintain`, Rung removes one action guard
  at a time and runs TLC on each mutant. If no invariant other than `TypeOK` notices any guard
  missing, the spec goes back to the model as too weak. The score ("4/5 guard mutants caught by an
  invariant") is recorded and shown by `rung check` and `rung report`.
- Readable counterexamples: TLA+ drafts return a plain-language label for each action and a
  statement and harm for each invariant. A counterexample is printed as numbered steps in those
  words with only the variables each step changed, next to the raw trace, and saved as a Mermaid
  `stateDiagram-v2` (`counterexample.mmd`) in the check's evidence; a pass under 200 states also
  writes `state-graph.mmd` from TLC's DOT dump.
- TLA+ and Lean drafts return a `scope` (bounds, assumptions, what is not covered) and the functions
  they mirror, shown by `rung check`, `rung laws` and `rung report` and stored in the verdict.
- Laws gain an optional `harm:` line, proposed by every draft and shown by `rung laws` and the report.
- An accepted TLA+ or Lean draft writes `.claude/skills/verify-<check>/SKILL.md`: what it promises,
  the covered files and functions, bounds and assumptions, `rung check <check>`, how to tell a real
  bug from a modeling error, when to run `rung maintain`, and the confirmed commit. `rung maintain`
  and `rung confirm` keep it current.

### Changed
- Scripted evidence runs write into `.rung/evidence/<check>/<time>/seed-<N>/` instead of
  `.rung/evidence/<check>/<time>/`. Nothing to do after upgrading; scripts still read
  `RUNG_EVIDENCE_DIR`.
- A drive script that times out is an `error` for that seed, with the other seeds still run and
  reported.
- An accepted TLA+ draft is checked once more through `rung check`, so it leaves a verdict and the
  readable counterexample or state graph.

### Fixed
- Drafting with Claude Code also passes `--strict-mcp-config`. `--tools ""` turns off the built-in
  tools only, so MCP servers from the user's own configuration stayed available to the drafting model
  (a draft reported calling a Google Drive search). The model now sees only what Rung sends, as the
  design says.

## 0.1.4 (2026-11-01)

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
