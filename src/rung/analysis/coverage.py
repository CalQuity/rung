"""Explicit pytest changed-line coverage; never changes the ordinary check ledger."""

from __future__ import annotations

import configparser
import json
import math
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from rung.analysis.diagnostics import Session
from rung.checks import prove
from rung.checks.base import Context
from rung.checks.command import _run
from rung.core import git, launch
from rung.core.config import Check, Config


class CoverageError(ValueError):
    """The requested comparison cannot produce trustworthy coverage evidence."""


def _safe(root: Path, path: Path) -> Path:
    """Require a literal in-project path, with no symlink components."""
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(root):
        raise CoverageError(f"coverage path is outside the project: {path}")
    current = root
    for part in absolute.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise CoverageError(f"coverage paths cannot traverse symlinks: {path}")
    return absolute


def _sources(root: Path, sources: list[str]) -> list[Path]:
    if not sources:
        raise CoverageError("at least one --source project-relative directory is required")
    result = []
    for source in sources:
        path = Path(source)
        if path.is_absolute() or ".." in path.parts:
            raise CoverageError("--source must be a project-relative directory without '..'")
        safe = _safe(root, root / path)
        if not safe.is_dir():
            raise CoverageError(f"--source must name an existing directory: {source}")
        result.append(safe)
    return result


def _options(root: Path, cwd: Path, argv: list[str]) -> list[str]:
    """Read possible pytest addopts without executing project configuration code."""
    tokens = [*argv, *shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))]
    configs = set()
    for directory in (cwd, *cwd.parents):
        configs.update(
            directory / name for name in ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")
        )
    for index, token in enumerate(argv):
        override = token.removeprefix("--override-ini=").removeprefix("-o")
        if override.startswith("addopts="):
            tokens.extend(shlex.split(override.split("=", 1)[1]))
        if token in {"-c", "--config-file"} and index + 1 < len(argv):
            configs.add(_safe(root, cwd / argv[index + 1]))
        elif token.startswith(("-c=", "--config-file=")):
            configs.add(_safe(root, cwd / token.split("=", 1)[1]))
        if "=" in token:
            name, value = token.split("=", 1)
            if name in {"PYTEST_ADDOPTS", "addopts"}:
                tokens.extend(shlex.split(value))
    for path in configs:
        if not path.is_file():
            continue
        if path.is_relative_to(root):
            _safe(root, path)
        try:
            if path.name == "pyproject.toml":
                document = tomllib.loads(path.read_text())
                pytest = document.get("tool", {}).get("pytest", {})
                if pytest and not path.is_relative_to(root):
                    raise CoverageError("pytest configuration outside the project is unsupported")
                options = pytest.get("ini_options", {}).get("addopts", [])
                options = pytest.get("addopts", options)
                tokens.extend(shlex.split(options) if isinstance(options, str) else options)
            else:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.read(path)
                section = "tool:pytest" if path.name == "setup.cfg" else "pytest"
                if parser.has_section(section):
                    if not path.is_relative_to(root):
                        raise CoverageError("pytest configuration outside the project is unsupported")
                    tokens.extend(shlex.split(parser.get(section, "addopts", fallback="")))
        except (ValueError, TypeError, configparser.Error) as exc:
            raise CoverageError(f"cannot inspect pytest configuration {path.name}: {exc}") from exc
    if not all(isinstance(token, str) for token in tokens):
        raise CoverageError("pytest addopts must contain strings")
    return tokens


def _guard_launcher(argv: list[str]) -> None:
    """Keep collection data inside this run even through supported launchers."""
    name = Path(argv[0]).name
    if name in {"uv", "uv.exe"} and any(arg.split("=", 1)[0] == "--env-file" for arg in argv[1:]):
        raise CoverageError("uv --env-file is unsupported for coverage; it can override managed collection paths")
    if name != "env":
        return
    tokens = iter(argv[1:])
    for arg in tokens:
        flag, equals, value = arg.partition("=")
        if flag in {"-i", "--ignore-environment", "-", "-S", "--split-string"}:
            raise CoverageError(f"env {flag} is unsupported for coverage; preserve the managed collection environment")
        if flag in {"-u", "--unset", "-C", "--chdir"}:
            value = value if equals else next(tokens, "")
            if flag in {"-u", "--unset"} and value == "COVERAGE_FILE":
                raise CoverageError("env cannot unset COVERAGE_FILE during a coverage diagnostic")
        elif flag == "COVERAGE_FILE" and equals:
            raise CoverageError("env cannot override COVERAGE_FILE during a coverage diagnostic")
        elif arg == "--" or "=" in arg:
            continue
        elif not arg.startswith("-"):
            break


def _command(root: Path, check: Check, sources: list[Path], report: Path) -> tuple[list[str], Path]:
    argv = shlex.split(check.require("run"))
    if check.kind != "command" or not prove.runs_pytest(argv):
        raise CoverageError("coverage requires a configured pytest command check")
    cwd = _safe(root, root / check.get("cwd", "."))
    inner, inner_cwd = argv, cwd
    _guard_launcher(inner)
    while delegated := launch.delegated(inner, inner_cwd):
        inner, inner_cwd = delegated.argv, _safe(root, delegated.cwd)
        _guard_launcher(inner)
    options = _options(root, inner_cwd, argv)
    for token in options:
        flag = token.split("=", 1)[0]
        if flag.startswith("--cov") or flag == "--no-cov":
            raise CoverageError(
                f"existing coverage option {flag} is unsupported; use a pytest check without coverage options"
            )
        if flag in {"--collect-only", "--co", "--help", "--version"}:
            raise CoverageError(f"pytest option {flag} does not execute tests")
    added = [*(f"--cov={path}" for path in sources), f"--cov-report=json:{report}"]
    # A pytest -- separator may coexist with an earlier wrapper -- separator.
    suffix = len(inner)
    start = len(argv) - suffix
    if argv[start:] != inner:
        # uv run -m synthesizes python -m; locate the pytest module token instead.
        start = max(index for index, value in enumerate(argv) if value == "pytest")
    separator = next((index for index in range(start, len(argv)) if argv[index] == "--"), len(argv))
    return [*argv[:separator], *added, *argv[separator:]], inner_cwd


def _changes(root: Path, sources: list[Path], base: str) -> dict[str, list[int]]:
    tracked = git._paths(
        git._git(root, "diff", "--no-ext-diff", "--no-renames", "--relative", "--name-only", "-z", base, "--")
    )
    untracked = set(git._paths(git._git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    changes = {}
    for name in sorted(set(tracked) | untracked):
        path = root / name
        if path.suffix != ".py" or not any(path.is_relative_to(source) for source in sources):
            continue
        path = _safe(root, path)
        if not path.is_file():
            continue  # Deleted source has no new-side opportunities.
        if name in untracked:
            lines = set(range(1, len(path.read_bytes().splitlines()) + 1))
        else:
            diff = git._git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--text",
                "--unified=0",
                base,
                "--",
                f":(literal){name}",
            )
            lines = set()
            for start, count in re.findall(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
                lines.update(range(int(start), int(start) + int(count or "1")))
        if lines:
            changes[name] = sorted(lines)
    return changes


def _line_set(record: dict, key: str) -> set[int]:
    values = record.get(key)
    if not isinstance(values, list) or any(type(line) is not int or line < 1 for line in values):
        raise CoverageError(f"coverage report {key} must be a list of positive integer line numbers")
    if len(set(values)) != len(values):
        raise CoverageError(f"coverage report {key} contains duplicate lines")
    return set(values)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CoverageError(f"coverage JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _records(root: Path, cwd: Path, report: Path) -> dict[str, tuple[set[int], set[int], set[int]]]:
    if report.is_symlink() or not report.is_file():
        raise CoverageError(
            "pytest did not generate a fresh coverage JSON report; install pytest-cov in the target environment"
        )
    try:
        document = json.loads(report.read_text(), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise CoverageError(f"coverage JSON report is unreadable: {exc}") from exc
    files = document.get("files") if isinstance(document, dict) else None
    if not isinstance(files, dict):
        raise CoverageError("coverage JSON report must contain a files object")
    records = {}
    for name, record in files.items():
        if not isinstance(name, str) or not name or not isinstance(record, dict):
            raise CoverageError("coverage JSON contains an invalid file record")
        path = Path(name)
        path = _safe(root, path if path.is_absolute() else cwd / path)
        if not path.is_file():
            raise CoverageError(f"coverage report file does not exist: {name}")
        key = path.relative_to(root).as_posix()
        if key in records:
            raise CoverageError(f"coverage report contains ambiguous aliases for {key}")
        executed, missing, excluded = (
            _line_set(record, field) for field in ("executed_lines", "missing_lines", "excluded_lines")
        )
        maximum = len(path.read_bytes().splitlines())
        if any(line > maximum for line in executed | missing | excluded):
            raise CoverageError(f"coverage report names lines beyond the source file: {name}")
        if executed & missing or missing & excluded:
            raise CoverageError(f"coverage report contains contradictory line records: {name}")
        if "executable_lines" in record and _line_set(record, "executable_lines") != (executed | missing) - excluded:
            raise CoverageError(f"coverage report executable lines disagree with executed/missing: {name}")
        records[key] = executed - excluded, missing, excluded
    return records


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def run(
    config: Config,
    check: Check,
    *,
    sources: list[str],
    base: str,
    fail_under: float | None = None,
    timeout: float = 600,
) -> dict[str, Any]:
    """Measure new-side executable statement lines, with advisory results by default."""
    parameters = {"sources": sources, "base": base, "base_ref": base, "fail_under": fail_under, "timeout": timeout}
    parameters = {
        key: repr(value) if isinstance(value, float) and not math.isfinite(value) else value
        for key, value in parameters.items()
    }
    session = Session(config, check, "coverage", parameters)
    result: dict[str, Any] = {
        "state": "incomplete",
        "summary": "Changed-line coverage is incomplete",
        "errors": [],
        "exit_code": 2,
        "base": base,
        "metrics": {"covered": 0, "total": 0, "percent": None, "changed_files": 0, "unmeasured_files": 0},
        "files": [],
        "limitations": [
            "Line coverage does not measure branch coverage or assertion quality.",
            "Subprocess logs retain the existing process runner output limit per stream.",
        ],
    }
    try:
        if fail_under is not None and (
            isinstance(fail_under, bool)
            or not isinstance(fail_under, int | float)
            or not math.isfinite(fail_under)
            or not 0 <= fail_under <= 100
        ):
            raise CoverageError("--fail-under must be a finite number from 0 to 100")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise CoverageError("--timeout must be a positive finite number")
        root = config.root.resolve()
        selected = _sources(root, sources)
        merge_base = git.merge_base(root, base)
        result["base"] = merge_base
        parameters["base"] = merge_base
        changes = _changes(root, selected, merge_base)
        result["metrics"]["changed_files"] = len(changes)
        report = session.path("coverage.json")
        argv, cwd = _command(root, check, selected, report)
        result["command"], result["cwd"] = argv, str(cwd)
        environment = {"COVERAGE_FILE": str(session.path(".coverage"))}
        completed = _run(
            Context(root), check, environment, argv=argv, timeout=min(timeout, check.get_int("timeout", 1800))
        )
        session.path("stdout.txt").write_text(completed.stdout)
        session.path("stderr.txt").write_text(completed.stderr)
        result["test_exit_code"] = completed.returncode
        if completed.returncode:
            message = f"pytest exited {completed.returncode}; this is not a successful test diagnostic"
            if "unrecognized arguments" in completed.stderr or "No module named" in completed.stderr:
                message += "; ensure pytest and pytest-cov are installed in the configured target environment"
            result["errors"].append(message)
        records = _records(root, cwd, report)
        for path, changed in changes.items():
            measured = records.get(path)
            executed, missing, excluded = measured or (set(), set(), set())
            changed_set = set(changed)
            row = {
                "path": path,
                "changed_lines": changed,
                "executable_lines": sorted(changed_set & (executed | missing)),
                "covered_lines": sorted(changed_set & executed),
                "missing_lines": sorted(changed_set & missing),
                "excluded_lines": sorted(changed_set & excluded),
                "unmeasured": measured is None,
            }
            result["files"].append(row)
            result["metrics"]["covered"] += len(row["covered_lines"])
            result["metrics"]["total"] += len(row["executable_lines"])
            result["metrics"]["unmeasured_files"] += int(measured is None)
        session.path("normalized.json").write_text(json.dumps(result["files"], indent=2))
        metrics = result["metrics"]
        if metrics["unmeasured_files"]:
            result["errors"].append(f"{metrics['unmeasured_files']} changed source file(s) were not measured")
        if metrics["total"]:
            metrics["percent"] = 100 * metrics["covered"] / metrics["total"]
        if not result["errors"]:
            result["state"] = "complete" if metrics["total"] else "not_applicable"
            result["summary"] = (
                f"{metrics['covered']}/{metrics['total']} changed executable lines covered ({metrics['percent']:.1f}%)"
                if metrics["total"]
                else "No changed executable lines; changed-line coverage is N/A"
            )
            result["exit_code"] = int(
                fail_under is not None and metrics["percent"] is not None and metrics["percent"] < fail_under
            )
    except subprocess.TimeoutExpired as exc:
        session.path("stdout.txt").write_text(_text(exc.output))
        session.path("stderr.txt").write_text(_text(exc.stderr))
        result["errors"].append(f"pytest timed out after {exc.timeout}s")
    except (CoverageError, git.GitError, OSError, ValueError, TypeError) as exc:
        result["errors"].append(str(exc))
    finally:
        # The JSON report is the authoritative retained coverage artifact. SQLite
        # is temporary collection state and cannot undergo text redaction.
        try:
            (session.directory / ".coverage").unlink(missing_ok=True)
        except OSError as exc:
            result["state"], result["exit_code"] = "incomplete", 2
            result["errors"].append(f"cannot remove temporary coverage database: {exc}")
    return session.finish(result)
