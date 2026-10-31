# Laws

Rules Rung itself must never break. `rung laws` shows which currently hold.

## no-unconfirmed-pass
A check never reports `pass` unless its tool actually confirmed the result; missing tools, missing output and malformed output are errors.
- enforced by: tests
- status: approved

## verdicts-expire
A verdict is trusted only while every file it covered is byte-for-byte unchanged, and a rewritten ledger is detected.
- enforced by: tests
- status: approved

## no-secrets-in-evidence
Secret values never reach Rung's output, ledger or reports, and the repository contains no secrets.
- enforced by: secrets, tests
- status: approved

## only-new-findings-block
On a branch, only findings the branch introduces can fail a scan; existing findings, waived findings and line moves do not.
- enforced by: tests
- status: approved

## safe-supply-chain
Rung's dependencies have no known vulnerabilities, every newly added one exists and is established, and its CI workflows pass zizmor.
- enforced by: vulnerable-deps, deps, workflows
- status: approved
