from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An empty project with a minimal rung.toml and one covered source file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "rung.toml").write_text(
        f"""[project]
name = "demo"

[llm]
provider = "none"

[[check]]
id = "unit"
kind = "command"
run = "{sys.executable} -c 'pass'"
covers = ["src/*.py"]
"""
    )
    return tmp_path
