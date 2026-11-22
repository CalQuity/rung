# CI, merge gates, and pre-commit

## GitHub Actions

Pin a release tag or reviewed commit SHA—never a mutable branch:

```yaml
name: rung
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
permissions:
  contents: read
  pull-requests: read
jobs:
  rung:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: CalQuity/rung@v0.6.1
```

Run the same merge decision locally or on another CI provider with:

```bash
rung gate --base origin/main
```

## Policy protection & `[gate]` settings

`rung gate` checks protected policy files **before** running any repository code. Edits to `rung.toml`, `LAWS.md`, waivers, baselines, generated drive scripts, formal models, declared `specs`, Node `package.json` scripts, static verifier launchers, or `.github/workflows/**` block by default.

Configure gate settings in `[gate]` in `rung.toml`—the gate always reads `[gate]` from the **merge base**, so a pull request cannot weaken the gate judging it:

```toml
[gate]
policy_changes = "fail"                   # or "warn" to report without blocking
policy_paths = [".semgrep/rules/**"]      # additional protected files
require_coverage = ["src/**"]             # changed paths requiring a declared execution check
approval_label = "rung-policy-approved"   # maintainer label approving policy edits
```

To approve a reviewed policy change, a maintainer applies the `approval_label` on the PR. The action verifies the label on that run only, so later pushes require fresh approval. Locally, pass `rung gate --pr-label rung-policy-approved` or `--allow-policy-changes`.

When a custom command claims changed targets via `verifies` or `require_coverage`, declare its verifier scripts with `--specs`:

```bash
rung add command refund --run 'python3 verify_refund.py' \
  --verifies 'app/**' --specs verify_refund.py 'verification/**'
```

## Sticky PR comments without granting write tokens to PR code

Keep the job that runs pull request code read-only and secret-free. To post a sticky PR comment, use a separate `workflow_run` workflow that never checks out or executes pull request code:

```yaml
# .github/workflows/rung-comment.yml
on:
  workflow_run:
    workflows: [rung]
    types: [completed]
permissions:
  pull-requests: write
jobs:
  comment:
    if: github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          RUN: ${{ github.event.workflow_run.id }}
          SHA: ${{ github.event.workflow_run.head_sha }}
          CONCLUSION: ${{ github.event.workflow_run.conclusion }}
        run: |
          pr=$(gh pr list --repo "$REPO" --state open --search "$SHA" --json number --jq '.[0].number')
          [ -n "$pr" ] || exit 0
          printf '<!-- rung-report -->\nRung gate: **%s**. [Run details](https://github.com/%s/actions/runs/%s)\n' \
            "$CONCLUSION" "$REPO" "$RUN" > report.md
          existing=$(gh api "repos/$REPO/issues/$pr/comments" --paginate --jq '.[]
            | select(.user.login == "github-actions[bot]" and (.body | startswith("<!-- rung-report -->"))) | .id' | head -n 1)
          if [ -n "$existing" ]; then
            gh api -X PATCH "repos/$REPO/issues/comments/$existing" -F body=@report.md > /dev/null
          else
            gh pr comment "$pr" --repo "$REPO" --body-file report.md
          fi
```

## Pre-commit hook

Add Rung to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/CalQuity/rung
  rev: v0.6.1
  hooks:
    - id: rung-check
```

## Linux Bubblewrap notes

On Linux runners, the GitHub Action installs Bubblewrap for isolated (`isolate = true`) evidence checks. On Ubuntu 23.10+ self-hosted runners where AppArmor restricts unprivileged user namespaces, allow user namespaces (`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`) or install a `bwrap` AppArmor profile.

## Optional: isolated Daytona executor

Repositories that want to keep PR test execution off the GitHub runner can opt into `executor: daytona` with a private, credential-free Daytona snapshot containing Git, Rung (at the same commit as the pinned action), and offline dependencies:

```yaml
- uses: CalQuity/rung@<reviewed-commit-sha>
  with:
    executor: daytona
    daytona-snapshot: my-project-verify-v1
  env:
    DAYTONA_API_KEY: ${{ secrets.DAYTONA_API_KEY }}
```

The coordinator transfers a Git bundle (up to 100 MiB) into a network-isolated ephemeral sandbox that expires after 26 minutes and is deleted on completion. From another CI provider, install with `uv tool install rung-cli --with daytona==0.227.3` and run `rung gate --base origin/main --executor daytona --daytona-snapshot my-project-verify-v1`.
