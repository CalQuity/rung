# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/CalQuity/rung/security/advisories/new)
rather than in a public issue. Include the version (`rung --version`), what you ran, and what
happened. We aim to acknowledge reports within three working days.

## Supported versions

Rung is in an early preview (0.x). Fixes go into the latest release only.

## What Rung handles, and how

- **Credentials.** Rung never stores model credentials. It reuses the login of the Claude Code or
  Codex CLI, or reads an API key from the environment variable named in `rung.toml`.
- **Secrets in findings.** Gitleaks always runs with `--redact`, so secret values do not reach
  Rung's output, reports or the ledger.
- **Model output.** Drafted files are written only inside the chosen output directory, and every
  draft is judged by a checker (TLC, the Lean kernel, a differential test, the real app) before it
  is registered. Claude Code drafts run with all tools disabled; Codex drafts run in a read-only
  sandbox.
- **Commands.** Checks run the commands declared in `rung.toml` as given, without a shell. Treat
  `rung.toml` like any other build script: review changes to it.
