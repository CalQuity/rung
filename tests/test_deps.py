from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rung.checks import deps, runner
from rung.core import config, git

from .conftest import commit_all, run_git


def test_parse_requirements_ignores_options_urls_and_comments() -> None:
    text = "# pinned\nRequests==2.31.0\nflask[async]>=3 ; python_version>'3.8'\n-r base.txt\ngit+https://x/y\n"
    assert deps.parse_manifest("requirements.txt", text) == {deps.Package("pypi", "requests"),
                                                              deps.Package("pypi", "flask")}  # fmt: skip


def test_parse_pyproject_skips_the_project_itself() -> None:
    text = '[project]\nname = "my_app"\ndependencies = ["httpx>=0.27", "Pydantic_Core"]\n'
    text += '[project.optional-dependencies]\ndev = ["pytest"]\n[dependency-groups]\nlint = ["ruff"]\n'
    names = {p.name for p in deps.parse_manifest("pyproject.toml", text)}
    assert names == {"httpx", "pydantic-core", "pytest", "ruff"}


def test_parse_package_json_skips_local_and_git_specs() -> None:
    text = json.dumps({"dependencies": {"react": "^19", "local": "file:../x"}, "devDependencies": {"@scope/x": "1"}})
    assert {p.name for p in deps.parse_manifest("package.json", text)} == {"react", "@scope/x"}


@pytest.fixture
def registry(monkeypatch):
    now = datetime.now(UTC)
    ages = {"requests": 4000, "httpx": 2000, "fresh-pkg": 3}

    def fake(pkg: deps.Package):
        if pkg.name not in ages:
            return None
        return now - timedelta(days=ages[pkg.name])

    monkeypatch.setattr(deps, "first_published", fake)


def _repo_with_requirements(repo: Path, lines: list[str]) -> config.Config:
    (repo / "requirements.txt").write_text("requests==2.31.0\n")
    (repo / "rung.toml").write_text('[[check]]\nid = "deps"\nkind = "deps"\n')
    commit_all(repo, "deps")
    run_git(repo, "checkout", "-qb", "feature")
    (repo / "requirements.txt").write_text("\n".join(["requests==2.31.0", *lines]) + "\n")
    return config.load(repo)


def test_new_established_package_passes(repo: Path, registry) -> None:
    cfg = _repo_with_requirements(repo, ["httpx==0.27"])
    result = runner.run_check(cfg, cfg.check("deps"), base=git.merge_base(repo, "main"))
    assert result.verdict == "pass"


def test_hallucinated_and_brand_new_packages_block(repo: Path, registry) -> None:
    cfg = _repo_with_requirements(repo, ["reqeusts-toolbelt-pro==1.0", "fresh-pkg==0.0.1"])
    result = runner.run_check(cfg, cfg.check("deps"), base=git.merge_base(repo, "main"))
    assert result.verdict == "fail"
    text = "\n".join(result.outcome.lines)
    assert "MISSING pypi:reqeusts-toolbelt-pro" in text
    assert "NEW     pypi:fresh-pkg" in text


def test_allow_list_accepts_a_reviewed_package(repo: Path, registry) -> None:
    cfg = _repo_with_requirements(repo, ["fresh-pkg==0.0.1"])
    cfg.check("deps").options["allow"] = ["fresh-pkg"]
    result = runner.run_check(cfg, cfg.check("deps"), base=git.merge_base(repo, "main"))
    assert result.verdict == "pass"


def test_b17_poetry_groups_npm_aliases_and_requirement_includes() -> None:
    pyproject = (
        '[tool.poetry.dependencies]\npython = "^3.11"\nhttpx = "*"\nlocal = {path = "../x"}\n'
        '[tool.poetry.group.dev.dependencies]\npytest = "*"\n'
    )
    assert {p.name for p in deps.parse_manifest("pyproject.toml", pyproject)} == {"httpx", "pytest"}
    pkg = json.dumps({"dependencies": {"react-alias": "npm:react@19", "scoped": "npm:@scope/real@1"}})
    assert {p.name for p in deps.parse_manifest("package.json", pkg)} == {"react", "@scope/real"}
    files = {"requirements/base.txt": "requests==2.31\n"}
    found = deps.parse_manifest("requirements/dev.txt", "-r base.txt\npytest\n", files.get)
    assert {p.name for p in found} == {"requests", "pytest"}


def test_b17_base_manifests_come_from_the_base_revision(repo: Path, registry) -> None:
    (repo / "requirements.txt").write_text("requests==2.31.0\n")
    (repo / "rung.toml").write_text('[[check]]\nid = "deps"\nkind = "deps"\n')
    commit_all(repo, "deps")
    run_git(repo, "checkout", "-qb", "rename")
    run_git(repo, "mv", "requirements.txt", "requirements-prod.txt")
    cfg = config.load(repo)
    result = runner.run_check(cfg, cfg.check("deps"), base=git.merge_base(repo, "main"))
    assert result.verdict == "pass" and "no new dependencies" in result.outcome.summary
