# Verify {project}

`.rung/verify/drive.py` launches the app, drives a feature end to end and records
evidence. The feature map (`.rung/verify/features/`, or `.rung/verify/FEATURES.md` in
an older setup) says for each feature how a user reaches it, how to drive it, and what
observable state proves it works; a person approved the features the script drives.

- Run `rung check {check_id}`. A pass means the script ran and left evidence; the
  evidence paths are in the output and the ledger.
- Changed or added a feature? Run `rung map --redraft` so a person can approve the changed
  features, then `rung draft runtime` (or extend `drive.py` by hand) and `rung check {check_id}`.
  Never weaken what the script asserts, and never approve map changes yourself.
- A failure is a product bug until shown otherwise. Fix the app, not the script.
- A feature the script cannot reach? Drive it with your browser or computer-use tool, record it,
  and `rung attest <check> <files> --by <tool> --note "<what you saw>"` (an evidence check
  declared without `run`).
- A failure names its seed and prints the command that reproduces it exactly:
  `rung check {check_id} --seed <N>`. Add `--scenario <name>` to run one scenario. Neither records a
  verdict; run `rung check {check_id}` once it is fixed.
- See what the script saw: each run writes an evidence page, `index.html`, in its directory under
  `.rung/evidence/{check_id}/` (`rung evidence {check_id}` rewrites it, `--seed N` for one seed). It
  has each scenario's assertions, screenshots and video. Open a Playwright trace with
  `npx playwright show-trace <path>/trace.zip` (or drop it on trace.playwright.dev) to step through the
  run with the DOM, network and console at every action; the `.har` file opens in browser dev tools.
- TLA+ and Lean checks, the formal-methods part of this kit, each have their own `verify-<check>`
  skill; when one reports that the code changed since its model was confirmed, run `rung maintain <check>`.
