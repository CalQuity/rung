"""Everything that names Rung's version must agree, or a release ships mismatched pieces."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import rung

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert rung.__version__ == version
    plugin_version = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert plugin_version.replace("-dev.", ".dev") == version
    action_refs = set(re.findall(r"CalQuity/rung@v([\d.]+)", (ROOT / "README.md").read_text()))
    action_refs |= set(
        re.findall(r"CalQuity/rung@v([\d.]+)", (ROOT / "plugin" / "skills" / "setup" / "SKILL.md").read_text())
    )
    if ".dev" in version:
        # Documentation points to the most recent published tag during a development cycle.
        latest = re.search(r"^## (\d+\.\d+\.\d+) \(", (ROOT / "CHANGELOG.md").read_text(), re.M)
        assert latest is not None
        assert action_refs == {latest.group(1)}
    else:
        assert action_refs == {version}


def test_setup_skill_has_the_frontmatter_agents_require() -> None:
    text = (ROOT / "plugin" / "skills" / "setup" / "SKILL.md").read_text()
    front = text.split("---")[1]
    assert re.search(r"^name: setup$", front, re.M) and re.search(r"^description: ", front, re.M)
