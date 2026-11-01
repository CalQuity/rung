"""Shellcheck every `run:` block of the composite action (actionlint only reads workflows)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def main() -> int:
    action = yaml.safe_load(Path("action.yml").read_text())
    failed = 0
    for i, step in enumerate(action["runs"]["steps"], 1):
        script = step.get("run")
        if not script:
            continue
        # GitHub expressions are substituted before bash runs; stand them in with a plain value.
        body = script.replace("${{", "__GH_EXPR__").replace("}}", "")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write("#!/usr/bin/env bash\nset -eo pipefail\n" + body)
        result = subprocess.run(["shellcheck", "--severity=style", fh.name], capture_output=True, text=True)
        Path(fh.name).unlink()
        if result.returncode != 0:
            failed += 1
            print(f"step {i} ({step.get('name', 'unnamed')}):\n{result.stdout}")
    print("action.yml: all run blocks pass shellcheck" if not failed else f"{failed} step(s) failed shellcheck")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
