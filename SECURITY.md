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
- **Model output.** Claude Code drafting requests disable tools, and Codex drafting requests use
  a read-only sandbox. Runtime and property trials and generated runtime replays run in a disposable copy. The CLI confines
  writes to that copy using sandbox-exec on macOS or Bubblewrap on Linux and masks common
  credential locations. Trials without dependency installation use loopback networking.
  Trials that install dependencies through uv can reach the network.
- **Execution boundary.** The draft guard permits reads of the toolchain and other accessible
  inputs. Use an isolated worker without real credentials for untrusted code or dependencies.
  `--allow-unisolated` applies to a draft trial on a worker whose isolation you provide. Scripted
  evidence uses the guard when drafted or explicitly configured to isolate. Formal builds and other
  project checks run with the current user's permissions.
- **Commands.** Checks run the commands declared in `rung.toml` as given, without a shell. Treat
  `rung.toml` like any other build script: review changes to it.

- **Pull requests.** Treat test runners, build steps, and repo-supplied scripts as executable code.
  Run the gate on a disposable runner with a read-only token and no secrets. Keep the required
  workflow and additional policy paths under repository-owner review. The gate preflights declared
  policy and re-runs checks. A ledger hash is a local integrity check.
- **What the gate protects against.** The merge gate catches accidental or agent-made weakening of
  checks: edited policy, scanner configs, test hooks, launch scripts, and the gate's own settings,
  which it reads from the base revision's `[gate]` table. It does not protect against a hostile
  pull request. In the local executor, pull request code runs as the runner user and can alter the
  runner, the installed Rung, later steps and the report. The action therefore fails in the step
  that ran the checks. Never give that job a write token or secrets. Post comments from a separate
  `workflow_run` workflow that does not run pull request code (see the README).
- **Policy approval.** A maintainer approves a reviewed policy change by applying the label named
  by the base `[gate]` table's `approval_label` (default `rung-policy-approved`). It counts only on
  the run that applying it starts, so each new push needs a fresh approval, and removing the label
  re-runs the gate without it when the workflow also triggers on `unlabeled`. Anyone with triage
  access can apply labels: limit those roles to maintainers.
- **Optional Daytona execution.** The trusted coordinator uses the API key to create a private,
  short-lived sandbox. Keep the key and other secrets out of the snapshot. The sandbox receives
  the commit and reachable Git history and returns check verdicts and short summaries. Treat
  those summaries as output from code under review. Daytona keeps secrets away from pull request
  code, but like the local executor it gives no integrity against that code: the receipt is
  produced in the same sandbox where pull request code runs. The receipt and report record the
  resolved snapshot ID. Keep approval and policy decisions with repository owners.
- **Artifacts.** CI uploads are disabled by default. Screenshots, HAR files, traces, and raw result
  files can contain private data. Use synthetic inputs and test accounts before enabling uploads.
- **Registry requests.** The `deps` check queries public package registries by package name.
  Put internal package names in that check's `allow` list before running it.
