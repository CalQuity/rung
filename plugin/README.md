# Rung plugin for Claude Code

This plugin adds one skill, `/rung:setup`, which sets up [Rung](https://github.com/CalQuity/rung) in the
repository you are working in. Rung is an open-source command-line tool (Apache 2.0) that builds a
verification kit for code that coding agents write: the project's own linters, type checkers and
tests recorded as checks, security scans, property tests, a feature map a person approves, scripts that
drive the running app and record screenshots, and TLA+ or Lean models where they pay off. Every result
is tied to the exact code it checked and expires when that code changes.

## Install

```text
/plugin marketplace add CalQuity/rung
/plugin install rung@rung
/rung:setup
```

## What the skill does, and what it runs

The skill is instructions for Claude; it ships no code of its own. Following it, Claude:

- installs the `rung-cli` package from PyPI with `uv tool install rung-cli` (or `pipx`) if `rung` is
  missing, and runs `rung doctor` to see which checkers are installed;
- runs `rung init` in the repository, which writes `rung.toml`, `LAWS.md`, `.rung/`, agent skills under
  `.claude/skills/`, a section in `AGENTS.md`, and a Claude Code Stop hook in `.claude/settings.json`
  (skipped with `--no-agents`);
- runs `rung check` and `rung kit`, which run the project's own test, lint and type-check commands and
  any installed scanners (gitleaks, osv-scanner, zizmor, semgrep);
- adds a GitHub Actions workflow that runs Rung on pull requests, if you agree.

It leaves decisions that are yours to you: accepting existing findings, waivers, approving laws and
approving the feature map.

## Data and network

The skill sends nothing anywhere. The Rung CLI it installs runs locally. It contacts the network only
to install packages, when a scanner you installed does (osv-scanner queries the OSV database), when
the `deps` check looks up new dependencies on PyPI or npm, and when you ask it to draft something with a
model: then it calls the model CLI you chose (`claude`, `codex`, or an OpenAI-compatible endpoint you
configure) with your own login. Rung stores no credentials and has no telemetry.

## Requirements

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) (or pipx). Java and TLC, Lean, Playwright and
the scanners are optional; `rung doctor` says how to install each.
