# Verify {project}

`.rung/verify/drive.py` launches the app, drives a feature end to end and records
evidence. `.rung/verify/FEATURES.md` maps each feature to how a user reaches it, how
to drive it, and what observable state proves it works.

- Run `rung check {check_id}`. A pass means the script ran and left evidence; the
  evidence paths are in the output and the ledger.
- Changed a feature the script does not drive yet? Extend `drive.py` and FEATURES.md,
  then run `rung check {check_id}` again. Never weaken what the script asserts.
- A failure is a product bug until shown otherwise. Fix the app, not the script.
