# Verification kit evaluation

Evaluate a fresh checkout as a developer adopting Rung, as a contributor maintaining a kit, and as a reviewer deciding whether a change is ready. Score the following dimensions independently. A claim needs an observed command, a repository citation, or an explicit account of why the experiment could not run.

| Dimension | What to exercise | Evidence of excellent behavior |
| --- | --- | --- |
| First run and CLI | Discover the workflow from help, preview adoption, initialize unattended, re-run, and diagnose missing prerequisites. | The developer can start with existing checks and understand every write, cost, and next action. Errors and structured output are useful without parsing prose. |
| Project fit | Try a web app, an API or service, a library or CLI, a stateful worker, and a polyglot workspace. Include an unfamiliar or empty repo. | Kits account for actual tools, scopes, and runtime entry points. Recommendations explain the evidence gap and distinguish detection from guesses. Unsupported setups have a working manual path. |
| Development lifecycle | Walk a feature from acceptance criteria and pre-change risk through local iteration, agent handoff, PR review, CI, and release. | Checks get stricter as changes progress. Intent, changed scope, results, and unresolved evidence are visible at each handoff, and the merge gate is reproducible on a clean machine. |
| Check quality | Seed regressions in ordinary tests, contracts and invariants, runtime behavior, dependencies, and concurrent code. | Existing tools are reused. Regression tests demonstrate sensitivity, runtime failures are reproducible, and formal results describe their link to production code. A check with a weak oracle or missing prerequisite cannot be mistaken for proof. |
| Evidence integrity | Modify inputs, checks, artifacts, scope, or base after a pass. Try stale, skipped, and fabricated evidence. | A verdict binds its inputs and the decision it supports. Reports disclose limitations and provide replay instructions. CI has a trust boundary separate from the author of the change. |
| Safe adoption and execution | Try untrusted changes, missing tools, dangerous shell commands, partial failure, repeated setup, and sensitive logs or recordings. | The developer can inspect and control execution and data transfer. Sensitive defaults are conservative, interrupted operations are recoverable, and gate failures explain how to proceed. |
| Agent and CI integration | Drive the same workflow from a shell, an agent, and clean CI. Check machine-readable statuses and change-aware enforcement. | Integration relies on durable commands, exit codes and schemas. Agents receive targeted actions. Waivers, approvals, and other policy decisions have explicit ownership and are auditable. |
| Agent-operated workflow | From a Codex or Claude Code session, find the installed workflow and drive preview, change planning, checks, replay, and reporting through the CLI. Repeat from a subdirectory and in an unattended environment. | An agent can discover the right commands, execute them, recover from failures, and report evidence with stable machine-readable fields. Project-local integrations call the same CLI and preserve developer decisions. |
| Terminal output and accessibility | Compare setup, check, gate, and status in an interactive terminal, a narrow terminal, redirected logs, and JSON mode. Exercise both clean and failing runs. | Headers, spacing, and semantic color reveal status, scope, causes, and the next action. Text labels carry the meaning without color. Redirected output and structured data are clean and predictable. |
| Open-source operability | Install from the supported package, reproduce examples, add an adapter or recipe, and run contributor checks. | Documentation matches the shipped behavior. Examples are runnable and show a defect being caught. A small extension follows visible interfaces and has focused regression coverage. |
| Value beyond model prompting | Disable the provider and repeat setup, a failing check, a regression proof, evidence inspection, and the merge decision. For model-assisted drafting, inspect what the CLI validates before it accepts a proposal. | Core decisions come from executed checks, reproducible failures, and auditable policy. The CLI rejects defective proposals mechanically and keeps working when a provider is unavailable. |
| Implementation language and distribution | Measure startup and survey cost on representative repositories. Build and install the distributable, exercise child process and path behavior on supported platforms, and trace the work needed to add a checker. | The implementation meets the product's measured speed, portability, installation, and maintainability needs. Evaluate a rewrite by the constraints it would remove, the toolchain dependencies that would remain, and the risk of losing existing behavior. |

## Scoring

- **1–2** · The workflow is absent or routinely gives an unsafe answer.
- **3–4** · Parts work with manual intervention. Common cases have substantial gaps.
- **5–6** · The ordinary path works. Important cases fail, require workarounds, or remain unverified.
- **7–8** · Representative paths work. Remaining limitations are material to at least one tested developer workflow.
- **9** · Representative workflows and meaningful failure paths are demonstrated. Remaining limitations are documented and minor within the supported scope.
- **10** · The same bar holds across additional environments and adversarial cases, with repeatable independent evidence.

## Review protocol

Run each review in a fresh source snapshot without implementation history, design rationale,
prior reviews, or the completion threshold. Give reviewers the same dimension names, scoring
anchors, and core exercises. Reviewers may choose additional tests, which they should identify
separately so regressions can be compared against the same exercises.

For each dimension, require a score, reproducible evidence, and the reason that evidence fits the
anchor. If a reviewer cites a material gap, require its effect on the developer workflow and the
command that reproduced it. If they cite missing evidence, require the unfinished or unavailable
exercise and why it matters to the score. Ask the reviewer to clarify a deduction that has no
supporting rationale. Keep that score provisional until the reviewer explains it. Record the
snapshot and distinguish tests the reviewer ran from tests the maintainer ran separately.

Treat each review as one batch. Record the disposition of every finding, implement the fixes
together, and run the combined checks before freezing the next snapshot. Record why a reported
limitation remains when addressing it requires an unavailable environment or a deliberate
project decision. Start the next blind review only after the batch has been verified.

### Core exercises for each round

| Dimension | Repeatable exercise |
| --- | --- |
| First run and CLI | Install the built wheel into a clean tool environment. Preview Acme Store setup, compare files before and after preview, initialize without a model, and run the generated frontend typecheck after installing locked dependencies. Run strict doctor against a missing configured tool. |
| Project fit | Initialize the shipped web and stateful examples, a four-stack Python/Node/Go/Rust workspace, and an empty repo. Record discovered checks, their working directories, and any tool or runner the survey leaves for manual setup. |
| Development lifecycle | Plan a change in one workspace, then a shared-input change. Demonstrate local rerun, staleness, a fix proof, and a fresh gate and report on the same change. |
| Check quality | Exercise the lease example before and after its documented fix. Add a meaningful and a vacuous pytest regression test. Run a seeded runtime check with one failing assertion and replay that seed. |
| Evidence integrity | After a pass, change a covered source, a stored artifact, and the verdict record in separate runs. Compare reports and ensure a partial replay leaves the full-run record unchanged. |
| Safe adoption and execution | Preview and apply with malformed agent settings. Run a merge-gate fixture with a source regression plus a changed test wrapper, including one command with interpreter flags. Exercise a disposable draft's write guard and a missing sandbox prerequisite. |
| Agent and CI integration | Run the composite action's shell flow from a full checkout. Verify a failure and protected policy edit. Exercise the optional executor using a fake provider that can return an error and an invalid receipt. Report hosted provider runs separately. |
| Agent-operated workflow | Install each agent's project workflow into existing settings. Run plan, check, replay, and report from a nested directory using JSON. Feed the Stop hook a stale result, a fresh result, and a repeated stop. Distinguish payload tests from actual agent sessions. |
| Terminal output and accessibility | Capture a passing and failing run in a 32-column PTY. Include a wide Unicode diagnostic and an assertion with a source location. Repeat with `NO_COLOR` and redirected JSON. |
| Open-source operability | Follow the contributor guide from a clean checkout through locked installation, the full suite, lint, format, and typing. Build the wheel and sdist, install a distributable, follow one shipped example, and trace a small checker extension through its documented registration points. Wait for required checks to finish before scoring. |
| Value beyond model prompting | Disable the provider and demonstrate a failing executable check, a fix proof, an evidence receipt, and a merge decision. Use a local fake model to see whether a generated drive that ignores application behavior is held for review. |
| Implementation language and distribution | Install the wheel on two supported Python versions, measure startup and a workspace survey, exercise Git paths containing spaces and newlines, and run a subprocess timeout on a supported OS. Trace one checker through its registration and packaging. |
