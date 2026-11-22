"""Phase 1 adoption ergonomics: zero-config check/prove and fast, opt-in init."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rung import cli
from rung.core import config

from .conftest import commit_all, run_git


def rung(root: Path, *args: str) -> int:
    return cli.main(["-C", str(root), *args])


def test_zero_config_check_catches_shortcuts_without_writing_files(repo: Path, capsys) -> None:
    (repo / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    commit_all(repo, "initial")
    run_git(repo, "checkout", "-qb", "agent-edit")
    (repo / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b  # type: ignore\n")

    capsys.readouterr()
    code = rung(repo, "check", "--changed", "--json")
    data = json.loads(capsys.readouterr().out)

    assert code == 1
    assert data["zero_config"] is True
    assert data["recorded"] is False
    assert data["selected"] == ["shortcuts", "deps"]
    by_id = {row["id"]: row for row in data["results"]}
    assert by_id["shortcuts"]["verdict"] == "fail"
    assert by_id["deps"]["verdict"] == "pass"
    assert not (repo / ".rung").exists()
    assert not (repo / "rung.toml").exists()


def test_zero_config_check_passes_cleanly_from_subdirectory(repo: Path, capsys) -> None:
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "calc.py").write_text("def total(x: int) -> int:\n    return x\n")
    commit_all(repo, "initial")
    run_git(repo, "checkout", "-qb", "clean-edit")
    (sub / "calc.py").write_text("def total(x: int) -> int:\n    return max(0, x)\n")

    capsys.readouterr()
    code = rung(sub, "check", "--changed")
    out = capsys.readouterr().out

    assert code == 0
    assert "Quick check (no rung.toml)" in out
    assert "Zero-config run (no files written)" in out
    assert not (repo / ".rung").exists()


def test_zero_config_check_still_rejects_unknown_check_ids(repo: Path, capsys) -> None:
    (repo / "app.py").write_text("x = 1\n")
    commit_all(repo, "initial")

    capsys.readouterr()
    assert rung(repo, "check", "nonexistent") == 2
    assert "rung init" in capsys.readouterr().err


def test_zero_config_prove_works_without_rung_toml_or_writes(repo: Path, capsys) -> None:
    (repo / "money.py").write_text("def refund(charged, requested):\n    return requested\n")
    commit_all(repo, "buggy")
    run_git(repo, "checkout", "-qb", "fix")
    (repo / "money.py").write_text("def refund(charged, requested):\n    return min(charged, requested)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_money.py").write_text(
        "from money import refund\n\ndef test_capped():\n    assert refund(100, 150) == 100\n"
    )
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"

    capsys.readouterr()
    code = rung(repo, "prove", cmd, "--json")
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["verdict"] == "pass"
    assert data["recorded"] is False
    assert not (repo / ".rung").exists()


def test_init_is_fast_zero_llm_and_skips_external_scanners_by_default(repo: Path, monkeypatch, capsys) -> None:
    (repo / "app.py").write_text("def main():\n    return 1\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\non: push\njobs: {}\n")
    commit_all(repo, "repo")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("rung.cli.commands.setup.ask", lambda prompt, default="": asked.append(prompt) or "n")
    monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}" if tool in {"gitleaks", "zizmor"} else None)

    capsys.readouterr()
    code = rung(repo, "init", "--no-agents")
    assert code == 0
    assert not asked  # never prompted for an LLM provider or drafting
    cfg = config.load(repo)
    assert cfg.llm.provider == "none"
    ids = {check.id for check in cfg.checks}
    assert "deps" in ids and "shortcuts" in ids
    assert "secrets" not in ids and "workflows" not in ids

    # Opting into external security scanners adds them cleanly.
    assert rung(repo, "init", "--with-security", "--no-agents") == 0
    ids_with_sec = {check.id for check in config.load(repo).checks}
    assert "secrets" in ids_with_sec and "workflows" in ids_with_sec


def test_init_dry_run_recommends_plain_rung_init(repo: Path, capsys) -> None:
    (repo / "app.py").write_text("x = 1\n")
    commit_all(repo, "repo")

    capsys.readouterr()
    assert rung(repo, "init", "--dry-run", "--no-agents") == 0
    out = capsys.readouterr().out
    assert "rung init --no-agents" in out
    assert "--no-drafts" not in out
