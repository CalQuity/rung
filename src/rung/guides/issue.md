# Report a reproduced finding on GitHub

Turn a verified Rung finding into an actionable issue in the affected repository. Use the
project's issue template where one exists. This skill is agent guidance, not a background
publisher: `rung check`, `rung score-my-code` and installing skills do not create issues.

## Establish what failed

Read `rung report --json` and the relevant check definition, then run `rung check <check-id>`
against the current source. Record the source commit, local changes, check ID, exact command,
runtime/dependency versions and the new verdict. If a runtime result supplies a replay command,
use it to narrow the failure and rerun the configured check to record the final evidence.

A missing executable, expired evidence, unsupported environment, skipped test or unreviewed
scanner warning is not by itself an application defect. Diagnose it first. Review the test's
expected result against the intended behavior and source. When the target checkout or original
environment is unavailable, distinguish supplied observations from local reproduction and leave
the unverified parts explicit. Do not invent a Rung verdict. For a model counterexample, establish
that the real implementation can take those steps. Do not weaken tests or approved laws.

Reduce the failing scenario to safe temporary data. Keep a passing control when possible.
State preconditions, expected behavior, actual behavior and practical impact separately. Do not
claim novelty, severity, exploitability or production impact beyond the evidence.

## Select the destination and check for duplicates

Confirm the affected repository from its remotes and ownership, including whether the checkout
is a fork. Do not assume that the repository containing Rung is the destination. Use an explicit
`--repo OWNER/REPO` for GitHub commands. If the destination remains ambiguous, ask the user.

Inspect `CONTRIBUTING.md`, `SECURITY.md` and `.github/ISSUE_TEMPLATE/`. Follow a private reporting
route for a suspected vulnerability or sensitive data; do not publish exploit details or secrets
in a public issue. If no private route is documented, keep a sanitized draft local and ask for
the appropriate destination. Redact credentials, customer data and local private paths. Inspect snippets
before attaching them; do not upload an entire ledger or HTML report without reviewing it.

Search open and closed issues for the symptom, affected function and error, then read plausible
matches. Search results are candidates, not proof of uniqueness. For example, after replacing
these illustrative values with the verified destination and search terms:

```sh
gh issue list --repo OWNER/REPO --state all --search 'symptom function' --limit 100 --json number,title,url,state
gh issue view 123 --repo OWNER/REPO --json title,body,state,url
```

If an existing issue covers the same root cause, return its link and prepare any additional
reproduction evidence locally. Check a closed issue's resolution and fix history before treating
the observation as a regression; do not reopen it without authorization. Do not create another
issue or post a comment without permission
to do so. Keep distinct symptoms of one root cause in one report.

## Draft the report

Write a Markdown body to a local temporary file. Include:

- A specific title naming the behavior and trigger.
- A short description of the issue and affected component.
- Source revision, relevant local changes and tested environment.
- Preconditions and the minimal failing scenario, including runnable reproduction commands or
  a small test using synthetic data.
- Expected versus actual behavior, a short redacted failure excerpt and the Rung check/verdict.
- Passing controls, scope exclusions and known limitations.
- Impact supported by the tested scenario.
- Recommended fix, with a source-based explanation. Clearly label an untested fix as a proposal;
  if the cause is uncertain, give a concrete next diagnostic step instead of inventing a patch.
- A regression-test recommendation that checks the failing behavior and a relevant passing case.

Attribute the work accurately: an agent or person may author the probe; the underlying test tool
executes it; Rung records its outcome. A failing check is not a count of confirmed bugs.

## Create the issue within the user's publishing scope

For a confirmed issue, create the report when the user has authorized GitHub issue creation in
the affected repository. A standing project instruction to file verified findings can supply that
authorization. Respect any current instruction to keep work local, review first or avoid external
writes. Otherwise, present the completed draft and ask for publishing permission. A failed check
alone does not grant permission to publish.

Use an explicit title and `--body-file` so Markdown, backticks and shell syntax remain literal.
Substitute verified values and the actual draft path; do not execute these placeholders:

```sh
gh issue create --repo OWNER/REPO --title 'Specific behavior under the tested condition' --body-file /path/to/issue.md
```

Read the returned issue back with `gh issue view` and confirm its destination, title and body.
Return its URL and summarize the evidence and proposed fix. If creation times out or its result
is uncertain, search for the attempted title and read plausible matches before retrying. If that
bounded reconciliation cannot establish whether it was created, stop and keep the uncertainty
visible rather than creating another issue. If authentication, connectivity or
permissions prevent creation, retain the draft and report the limitation; do not retry blindly or
claim an issue was filed. Never change GitHub credentials, visibility, labels or assignees to work
around a publishing failure.
