"""Exercise the composite action's comment step with a disposable local gh fixture."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml


def main() -> None:
    action = yaml.safe_load(Path("action.yml").read_text())
    step = next(
        step for step in action["runs"]["steps"] if step.get("name") == "Post or update the pull request comment"
    )
    assert action["inputs"]["comment"]["default"] == "false" and step["continue-on-error"] is True
    with tempfile.TemporaryDirectory(prefix="rung-action-comment-") as directory:
        root = Path(directory)
        (root / "rung-report.md").write_text("<!-- rung-report -->\n## Synthetic Rung report\n")
        gh = root / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['RUNG_FIXTURE_CALLS'], 'a') as log:\n"
            "    print(json.dumps(sys.argv[1:]), file=log)\n"
            "if sys.argv[1:2] == ['api'] and '--paginate' in sys.argv:\n"
            "    print(os.environ['RUNG_FIXTURE_EXISTING'])\n"
        )
        gh.chmod(0o755)
        calls = root / "calls.jsonl"
        env = {
            "PATH": os.pathsep.join((str(root), os.environ.get("PATH", os.defpath))),
            "REPO": "fixture/project",
            "PR": "42",
            "RUNNER_TEMP": str(root),
            "RUNG_FIXTURE_CALLS": str(calls),
        }
        for existing in ("", "123"):
            calls.write_text("")
            subprocess.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                check=True,
                env={**env, "RUNG_FIXTURE_EXISTING": existing},
                capture_output=True,
            )
            lookup, update = [json.loads(line) for line in calls.read_text().splitlines()]
            assert lookup[:3] == ["api", "repos/fixture/project/issues/42/comments", "--paginate"]
            assert "github-actions[bot]" in lookup[-1] and "<!-- rung-report -->" in lookup[-1]
            if existing:
                assert update == ["api", "-X", "PATCH", f"repos/fixture/project/issues/comments/{existing}",
                                  "-F", f"body=@{root}/rung-report.md"]  # fmt: skip
            else:
                assert update == ["pr", "comment", "42", "--repo", "fixture/project",
                                  "--body-file", str(root / "rung-report.md")]  # fmt: skip
    print("The optional comment step selected create and update correctly against a local fixture.")


if __name__ == "__main__":
    main()
