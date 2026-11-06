from __future__ import annotations

from pathlib import Path

import pytest

from rung.core import git

from .conftest import commit_all, run_git


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("**/*.py", "a.py", True),
        ("**/*.py", "src/deep/a.py", True),
        ("**/*.py", "src/de\nep/a.py", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/deep/a.py", False),
        (".github/workflows/**", ".github/workflows/ci.yml", True),
        ("**/requirements*.txt", "requirements-dev.txt", True),
        ("**/tests/**", "pkg/tests/test_x.py", True),
        ("a?.txt", "ab.txt", True),
        ("a?.txt", "a/b.txt", False),
    ],
)
def test_glob_match(pattern: str, path: str, expected: bool) -> None:
    assert git.glob_match(pattern, path) is expected


def test_list_files_respects_gitignore(repo: Path) -> None:
    (repo / ".gitignore").write_text("build/\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("x")
    (repo / "kept.py").write_text("x = 1\n")
    files = git.list_files(repo)
    assert "kept.py" in files and "build/out.js" not in files


def test_list_files_outside_git_skips_dependency_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x")
    (tmp_path / "app.js").write_text("x")
    assert git.list_files(tmp_path) == ["app.js"]


def test_expand_keeps_unmatched_patterns_literal(repo: Path) -> None:
    assert git.expand(repo, ["missing.py"]) == ["missing.py"]


def test_changed_files_include_branch_commits_edits_and_untracked(repo: Path) -> None:
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "committed.py").write_text("a\n")
    commit_all(repo, "feature work")
    (repo / "README.md").write_text("edited\n")
    (repo / "new.py").write_text("b\n")
    assert git.changed_files(repo, "main") == {"committed.py", "README.md", "new.py"}


def test_worktree_checks_out_the_base_and_cleans_up(repo: Path) -> None:
    base = git.head(repo)
    (repo / "README.md").write_text("changed on the branch\n")
    commit_all(repo, "change")
    with git.worktree(repo, base) as tree:
        assert (tree / "README.md").read_text() == "demo\n"
        location = tree
    assert not location.exists()
    assert str(location) not in run_git(repo, "worktree", "list")


def test_default_base_prefers_main(repo: Path) -> None:
    run_git(repo, "checkout", "-qb", "feature")
    assert git.default_base(repo) == "main"


def test_batch_reads_keep_unusual_names_and_following_files_aligned(repo: Path) -> None:
    paths = {"alpha\npart.py": b"first\n", "second\rpart.py": b"second\n", "tests/test_demo.py": b"third\n"}
    for path, content in paths.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    commit_all(repo, "unusual filenames")
    assert set(paths).issubset(git.list_files(repo))
    assert git.show_many(repo, git.head(repo), list(paths)) == paths


def test_default_base_finds_the_only_local_branch(repo: Path) -> None:
    run_git(repo, "branch", "-m", "primary")
    assert git.default_base(repo) == "refs/heads/primary"
