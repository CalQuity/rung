from __future__ import annotations

import subprocess
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


def run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository on `main` with one commit, ready for branch-based tests."""
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "config", "user.email", "dev@example.com")
    run_git(root, "config", "user.name", "Dev")
    run_git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("demo\n")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-qm", "init")
    return root


def commit_all(root: Path, message: str) -> None:
    run_git(root, "add", "-A")
    run_git(root, "commit", "-qm", message)
