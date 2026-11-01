# Changelog

All notable changes to Rung. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow the policy in [CONTRIBUTING.md](CONTRIBUTING.md#versioning).

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
