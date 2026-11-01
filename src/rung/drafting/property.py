"""`rung draft property`: a model writes property-based tests for a law; real runs and mutants decide.

The loop:

1. The model writes one test file of Hypothesis (Python) or fast-check (TypeScript/JavaScript)
   properties for the source files and the laws the developer gives.
2. Rung runs it. A broken test (an import error, a typo) goes back to the model. A property that
   fails on the real code is a *finding* (the code or the law is wrong) and stops the draft for a
   person to judge, exactly like a TLC counterexample.
3. A passing file is run against mutants of the source: one-token bugs such as `<` to `<=`. A file
   that no mutant can fail is too weak to catch anything; the surviving mutants go back to the model
   as the reason to strengthen it. This is how a draft avoids the classic weak generated test that
   passes on everything.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rung import guides
from rung.core import proc
from rung.core.config import ConfigError
from rung.core.errors import ToolMissing
from rung.drafting.common import LAWS, LAWS_FORMAT, DraftResult, schema, source_block
from rung.drafting.providers import Provider
from rung.engines import mutate

FORMAT = (
    """

Return `test`: the complete contents of the test file (it is written to the path given in the request),
and put anything the developer should know in `notes`."""
    + LAWS_FORMAT
)
SCHEMA = schema(test={"type": "string"}, laws=LAWS, notes={"type": "string"})
RUN_TIMEOUT = 300
MAX_MUTANTS = 10
GOOD_ENOUGH = 0.5  # share of mutants a property file must catch before Rung stops asking for more

_FINDING = re.compile(r"Falsifying example|Failing test case|Counterexample:|Property failed after", re.I)
# Errors that mean the test file itself is broken, even inside a failing example.
_BROKEN_TEST = re.compile(
    r"\b(?:NameError|ImportError|ModuleNotFoundError|SyntaxError|IndentationError|ReferenceError)\b"
)
_MISSING_PACKAGE = re.compile(r"No module named '(hypothesis|pytest)'|Cannot find (?:module|package) 'fast-check'")


@dataclass
class Target:
    """Where the property file lives and how to run it."""

    language: str  # python | typescript
    project: Path  # the directory the command runs in
    test_file: Path
    argv: list[str]

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


def _nearest(start: Path, root: Path, names: tuple[str, ...]) -> Path:
    for d in (start, *start.parents):
        if any((d / n).exists() for n in names):
            return d
        if d == root:
            break
    return root


def target_for(root: Path, sources: list[Path], slug: str) -> Target:
    """Pick the language, project directory, test file path and runner for these sources."""
    first = sources[0]
    if first.suffix == ".py":
        project = _nearest(first.parent, root, ("pyproject.toml", "setup.py", "pytest.ini"))
        test = project / "tests" / "properties" / f"test_{slug.replace('-', '_')}_properties.py"
        rel = test.relative_to(project).as_posix()
        if (project / "uv.lock").exists():
            argv = [
                "uv",
                "run",
                "--with",
                "hypothesis",
                "--with",
                "pytest",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                rel,
            ]
        else:
            argv = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", rel]
        return Target("python", project, test, argv)
    if first.suffix in mutate.JS:
        project = _nearest(first.parent, root, ("package.json",))
        try:
            pkg = json.loads((project / "package.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read {project / 'package.json'}: {exc}") from exc
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "fast-check" not in deps:
            raise ToolMissing(f"add fast-check to {project.relative_to(root) or '.'} first: npm install -D fast-check")
        ext = "ts" if first.suffix in (".ts", ".tsx", ".mts", ".cts") else "js"
        test = project / "tests" / "properties" / f"{slug}.property.test.{ext}"
        rel = test.relative_to(project).as_posix()
        if "vitest" in deps:
            argv = ["npx", "vitest", "run", rel]
        elif "jest" in deps:
            argv = ["npx", "jest", rel]
        else:
            raise ToolMissing("property tests run under Vitest or Jest; neither is in package.json")
        return Target("typescript", project, test, argv)
    raise ConfigError(f"property tests are drafted for Python and TypeScript/JavaScript, not {first.suffix} files")


def undeclared(target: Target) -> list[str]:
    """A warning when the project does not declare the property library its own test run will import."""
    if target.language == "python":
        manifest = target.project / "pyproject.toml"
        text = manifest.read_text() if manifest.exists() else ""
        if "hypothesis" not in text.lower():
            fix = (
                "uv add --dev hypothesis"
                if (target.project / "uv.lock").exists()
                else "add it to your dev dependencies"
            )
            return [f"hypothesis is not a declared dependency, so the project's own test run will fail to import "
                    f"{target.test_file.name}: {fix}"]  # fmt: skip
    return []  # fast-check is required up front for TypeScript


def classify(returncode: int, output: str) -> str:
    """pass, finding (a property failed on real code) or error (the test itself is broken)."""
    if returncode == 0 and not re.search(r"\bno tests ran\b|No test files found", output):
        return "pass"
    if returncode == 1 and _FINDING.search(output) and not _BROKEN_TEST.search(output):
        return "finding"
    return "error"


def _run(target: Target) -> tuple[int, str]:
    try:
        result = proc.run(target.argv, cwd=target.project, timeout=RUN_TIMEOUT)
    except FileNotFoundError as exc:
        raise ToolMissing(f"cannot run `{target.command}`: {exc}") from exc
    except subprocess.TimeoutExpired:
        return 124, f"`{target.command}` timed out after {RUN_TIMEOUT}s"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def mutation_score(target: Target, sources: list[Path]) -> tuple[int, list[mutate.Mutant]]:
    """Run the property file against each mutant; return how many it caught and which survived."""
    sample = [m for s in sources for m in mutate.mutants(s, limit=MAX_MUTANTS)][:MAX_MUTANTS]
    survivors = []
    for mutant in sample:
        with mutate.applied(mutant):
            code, _ = _run(target)
        if code == 0:
            survivors.append(mutant)
    return len(sample) - len(survivors), survivors


def _tail(text: str, n: int = 60) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def draft(
    provider: Provider, root: Path, sources: list[Path], slug: str, *, intent: str = "", max_rounds: int = 4
) -> tuple[DraftResult, Target]:
    target = target_for(root, sources, slug)
    system = guides.load("property") + FORMAT
    imports = (
        "Import the code under test as the project's own tests do (the test runs from "
        f"`{target.project.relative_to(root).as_posix() or '.'}`)."
    )
    prompt = (
        f"Write {'Hypothesis' if target.language == 'python' else 'fast-check'} property-based tests for this code. "
        f"The file is `{target.test_file.relative_to(root).as_posix()}`, run with `{target.command}`. {imports}\n"
        + (
            f"The laws it must check (from the developer): {intent}\n"
            if intent
            else "State the laws you infer from names, docstrings and comments.\n"
        )
        + "\n"
        + source_block(root, sources)
    )
    target.test_file.parent.mkdir(parents=True, exist_ok=True)
    notes, laws = "", []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        notes, laws = reply["notes"].strip(), reply["laws"]
        target.test_file.write_text(re.sub(r"^```.*$\n?", "", reply["test"], flags=re.M).rstrip() + "\n")
        code, output = _run(target)
        if missing := _MISSING_PACKAGE.search(output):
            raise ToolMissing(f"`{missing.group(0)}`: install the test dependencies, then draft again")
        verdict = classify(code, output)
        if verdict == "finding":
            message = "a property fails on the real code. Decide: a bug in the code, or a wrong law?"
            return DraftResult(True, message, [target.test_file], round_no, _tail(output), notes=notes,
                               laws=laws), target  # fmt: skip
        if verdict == "error":
            prompt += f"\n\nThe test file did not run cleanly:\n{_tail(output)}\nFix the test (not the code)."
            continue
        caught, survivors = mutation_score(target, sources)
        total = caught + len(survivors)
        if total == 0 or (caught and (caught / total >= GOOD_ENOUGH or round_no == max_rounds)):
            score = f"caught {caught} of {total} deliberate bugs" if total else "no mutants could be made"
            warnings = [f"survived: {m.describe}" for m in survivors] + undeclared(target)
            result = DraftResult(True, f"properties hold on the real code and {score}", [target.test_file],
                                 round_no, notes=notes, laws=laws, warnings=warnings)  # fmt: skip
            return result, target
        listed = "\n".join(f"- {m.describe}" for m in survivors)
        prompt += (
            f"\n\nYour properties pass, but they also pass when the code is broken like this:\n{listed}\n"
            "Strengthen them so a bug like these fails a property: pin exact values the law implies and "
            "include boundary inputs. Do not test implementation details."
        )
    return DraftResult(False, f"no property file caught a deliberate bug after {max_rounds} rounds",
                       [target.test_file], max_rounds, notes=notes, laws=laws), target  # fmt: skip
