# Writing a runtime verification script

You write a verification script that drives a real application the way a user does
and records evidence. A program, not a person, will run it, with no one watching.

The script's contract:
- Language: Python 3 standard library only (subprocess, urllib, json, time, pathlib, socket).
  Optional: Playwright for web UIs, guarded by an import check that exits 2 with a clear message.
- Start what it needs (dev server, CLI build) using the project's own documented commands,
  wait until the app is actually usable (a port answers AND a real request succeeds), and
  stop only what it started. Pick a free port; never kill processes by name.
- Exercise ONE user-facing feature end to end through its real interface (HTTP requests,
  CLI invocations, browser actions), never internal functions or test-only endpoints.
- Write evidence into the directory in the RUNG_EVIDENCE_DIR environment variable:
  transcripts of commands with their output, HTTP request/response pairs as JSON,
  screenshots or a video for UIs. Evidence must survive cleanup.
- Check the observable result (status codes, output text, persisted state) and exit 1 with a
  message naming what was expected and what happened if it is wrong. Exit 0 only on success.
- Never print or save secrets; read credentials only from environment variables.

Also write a feature map: FEATURES.md listing the top 3-5 user-facing features, each with
"How a user reaches it", "How the script drives it", "What proves it works", and the source
files that implement it.
