"""`rung draft property`: a model writes property-based tests for a law; real runs and mutants decide.

The loop, all inside a disposable copy of the project (`rung.drafting.sandbox`):

1. The model writes one test file of Hypothesis (Python) or fast-check (TypeScript/JavaScript)
   properties for the source files and the laws the developer gives.
2. Rung runs it. A test that is broken (it fails in its own code, or runs nothing) goes back to the
   model. A property that fails on the real code is a *finding* (the code or the law is wrong) and
   stops the draft for a person to judge, exactly like a TLC counterexample. Failures Rung cannot
   attribute to the test are treated as findings too, so a real bug is never handed back to the
   model to make disappear.
3. A canary proves the tests import the copied code (not an installed package), then the file runs
   against mutants of the source: one-token bugs such as `<` to `<=`. Only a property failure counts
   as catching one; a mutant that breaks the build or times out is not counted. A file that catches
   fewer than half goes back to the model with the bugs it missed.

The developer's files are only written at the end: the accepted test, or nothing if a test already
exists at that path. A failed draft is kept under `.rung/cache/drafts/`.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rung import guides
from rung.core import proc
from rung.core.config import ConfigError
from rung.core.errors import RungError, ToolMissing
from rung.drafting import sandbox
from rung.drafting.common import LAWS, LAWS_FORMAT, DraftResult, machine_path_problem, schema, source_block
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
GOOD_ENOUGH = 0.5  # share of viable mutants a property file must catch
CANARY = "rung-canary-7f3a"
DRAFTS_DIR = Path(".rung") / "cache" / "drafts"

# A run that never got as far as running tests: a module that does not compile or import.
_BUILD_FAILED = re.compile(
    r"Transform failed|Cannot find module|ERR_MODULE_NOT_FOUND|error TS\d+|SyntaxError: |ERROR collecting|"
    r"ImportError while importing test module"
)
# Errors that mean the test's own code is broken, when the failure happens inside the test file.
_BROKEN = {"NameError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError", "ReferenceError",
           "AttributeError", "TypeError", "UnboundLocalError"}  # fmt: skip
_PY_LOCATION = re.compile(r"^([^\s:]+\.py):\d+: (\w+)", re.M)  # pytest's `file.py:12: AssertionError`
_JS_LOCATION = re.compile(r"^\s*at .*?([^\s()]+\.[cm]?[jt]sx?):\d+:\d+", re.M)
_JS_ERROR = re.compile(r"\b(\w*Error)\b:")
_PASSED = re.compile(r"(\d+) passed|^\S* pass (\d+)$", re.M)
_MISSING_PACKAGE = re.compile(r"No module named '(hypothesis|pytest)'|Cannot find (?:module|package) 'fast-check'")


@dataclass
class Target:
    """Where the property file lives and how to run it; paths are relative to the repository root."""

    language: str  # python | typescript
    project: str  # the directory the command runs in
    test_file: str
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


def target_for(root: Path, sources: list[Path], slug: str, out: str = "") -> Target:
    """Pick the language, project directory, test file path and runner for these sources."""
    first = sources[0]
    if first.suffix == ".py":
        project = _nearest(first.parent, root, ("pyproject.toml", "setup.py", "pytest.ini"))
        test = root / out if out else project / "tests" / "properties" / f"test_{slug.replace('-', '_')}_properties.py"
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
        language = "python"
    elif first.suffix in mutate.JS:
        project = _nearest(first.parent, root, ("package.json",))
        try:
            pkg = json.loads((project / "package.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read {project / 'package.json'}: {exc}") from exc
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "fast-check" not in deps:
            raise ToolMissing(f"add fast-check to {project.relative_to(root) or '.'} first: npm install -D fast-check")
        ext = "ts" if first.suffix in (".ts", ".tsx", ".mts", ".cts") else "js"
        test = root / out if out else project / "tests" / "properties" / f"{slug}.property.test.{ext}"
        rel = test.relative_to(project).as_posix()
        argv = _js_runner(pkg, deps, rel)
        language = "typescript"
    else:
        raise ConfigError(f"property tests are drafted for Python and TypeScript/JavaScript, not {first.suffix} files")
    if project not in test.resolve().parents and test.resolve().parent != project.resolve():
        raise ConfigError(f"the test file must be inside {project.relative_to(root) or 'the project'}")
    return Target(language, project.relative_to(root).as_posix(), test.relative_to(root).as_posix(), argv)


def undeclared(root: Path, target: Target) -> list[str]:
    """A warning when the project does not declare the property library its own test run will import."""
    project = root / target.project
    if target.language == "python":
        manifest = project / "pyproject.toml"
        text = manifest.read_text() if manifest.exists() else ""
        if "hypothesis" not in text.lower():
            fix = "uv add --dev hypothesis" if (project / "uv.lock").exists() else "add it to your dev dependencies"
            return [f"hypothesis is not a declared dependency, so the project's own test run will fail to import "
                    f"{Path(target.test_file).name}: {fix}"]  # fmt: skip
    return []  # fast-check is required up front for TypeScript


def _js_runner(pkg: dict, deps: dict, rel: str) -> list[str]:
    """The project's own test runner, pointed at one file: Vitest, Jest, or Node's built-in `node --test`."""
    script = str((pkg.get("scripts") or {}).get("test", ""))
    if "vitest" in script or ("vitest" in deps and "jest" not in script):
        return ["npx", "vitest", "run", rel]
    if "jest" in script or "jest" in deps:
        return ["npx", "jest", rel]
    tokens = shlex.split(script) if script else []
    if tokens and tokens[0] == "node" and "--test" in tokens:
        # Keep the loader flags (e.g. `--import tsx`) and replace the file globs with this file.
        return [*tokens[: tokens.index("--test") + 1], rel]
    raise ToolMissing("property tests run under Vitest, Jest or `node --test`; package.json uses none of them")


def passed_count(output: str) -> int:
    """How many tests the runner says passed (pytest, Vitest, Jest, `node --test`)."""
    return max((int(a or b) for a, b in _PASSED.findall(output)), default=0)


def classify(returncode: int, output: str, sources: list[str], test_file: str) -> str:
    """pass, finding (a test failed on the code) or error (the test itself is broken, or ran nothing).

    Exit status 1 means tests ran and failed. That is a finding, whether or not the failure came with
    a generated example: a plain `assert` the model wrote can catch the bug just as well. The test is
    broken instead when the run could not build or import, or when a non-assertion error (a typo, a
    wrong call) is raised in the test file itself. An error raised in the code under test is always a
    finding, even a `NameError`; anything Rung cannot place goes to a person as a finding.
    """
    if returncode == 0:
        return "pass" if passed_count(output) > 0 else "error"
    if returncode != 1 or _BUILD_FAILED.search(output):
        return "error"
    py = _PY_LOCATION.findall(output)
    js = _JS_LOCATION.findall(output)
    where, kind = "", ""
    if py:
        where, kind = Path(py[-1][0]).name, py[-1][1]  # pytest lists the innermost frame last
    elif js:
        where = Path(js[0]).name  # JavaScript stacks list it first
        # fast-check wraps the real error in a plain `Error: Property failed`; the specific type is the cause.
        kind = next((k for k in _JS_ERROR.findall(output) if k != "Error"), "")
    if where in sources:
        return "finding"
    if where == test_file and kind in _BROKEN:
        return "error"
    return "finding"


def _run(target: Target, base: Path) -> tuple[int, str]:
    """Run the property file in the copy at `base`, without credentials in the environment."""
    project = base / target.project
    path = [str(project / "src"), str(project)] if (project / "src").is_dir() else [str(project)]
    # The copy's own code must win over an installed (editable) package pointing at the real tree, and
    # bytecode is never cached for files Rung mutates.
    env = sandbox.scrubbed_env({"PYTHONPATH": ":".join(path), "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        result = proc.run(target.argv, cwd=project, timeout=RUN_TIMEOUT, env=env)
    except FileNotFoundError as exc:
        raise ToolMissing(f"cannot run `{target.command}`: {exc}") from exc
    except subprocess.TimeoutExpired:
        return 124, f"`{target.command}` timed out after {RUN_TIMEOUT}s"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _exercises_copy(target: Target, base: Path, sources: list[str]) -> bool:
    """Whether the tests import the copied sources: a canary appended to one must show up in the run."""
    source = base / sources[0]
    line = f'\nraise RuntimeError("{CANARY}")\n' if source.suffix == ".py" else f'\nthrow new Error("{CANARY}");\n'
    original = source.read_bytes()
    try:
        source.write_bytes(original + line.encode())
        _, output = _run(target, base)
    finally:
        source.write_bytes(original)
    return CANARY in output


@dataclass
class Score:
    caught: list[mutate.Mutant]
    survived: list[mutate.Mutant]
    nonviable: list[mutate.Mutant]  # broke the build, crashed the runner or timed out: not evidence either way

    @property
    def viable(self) -> int:
        return len(self.caught) + len(self.survived)


def mutation_score(target: Target, base: Path, sources: list[str]) -> Score:
    """Run the property file against each mutant in the copy; only a property failure counts as a catch."""
    sample = [m for s in sources for m in mutate.mutants(base / s, limit=MAX_MUTANTS)][:MAX_MUTANTS]
    score = Score([], [], [])
    names = [Path(s).name for s in sources]
    for mutant in sample:
        with mutate.applied(mutant):
            code, output = _run(target, base)
        verdict = classify(code, output, names, Path(target.test_file).name)
        {"finding": score.caught, "pass": score.survived}.get(verdict, score.nonviable).append(mutant)
    return score


def _tail(text: str, n: int = 60) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def _keep_failed(root: Path, target: Target, body: str) -> Path:
    path = root / DRAFTS_DIR / Path(target.test_file).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def draft(
    provider: Provider,
    root: Path,
    sources: list[Path],
    slug: str,
    *,
    intent: str = "",
    out: str = "",
    max_rounds: int = 4,
) -> tuple[DraftResult, Target]:
    target = target_for(root, sources, slug, out)
    final = root / target.test_file
    if final.exists():
        raise ConfigError(f"{target.test_file} already exists; choose another path with --out, or move it first")
    rel_sources = [s.relative_to(root).as_posix() for s in sources]
    names = [Path(s).name for s in rel_sources]
    system = guides.load("property") + FORMAT
    prompt = (
        f"Write {'Hypothesis' if target.language == 'python' else 'fast-check'} property-based tests for this code. "
        f"The file is `{target.test_file}`, run with `{target.command}` from `{target.project}`. Import the code "
        "under test as the project's own tests do.\n"
        + (
            f"The laws it must check (from the developer): {intent}\n"
            if intent
            else "State the laws you infer from names, docstrings and comments.\n"
        )
        + "\n"
        + source_block(root, sources)
    )
    notes, laws, body = "", [], ""
    with sandbox.copy_of(root) as box:
        test_in_box = box / target.test_file
        test_in_box.parent.mkdir(parents=True, exist_ok=True)
        for round_no in range(1, max_rounds + 1):
            reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
            notes, laws = reply["notes"].strip(), reply["laws"]
            body = re.sub(r"^```.*$\n?", "", reply["test"], flags=re.M).rstrip() + "\n"
            if problem := machine_path_problem({target.test_file: body}):
                prompt += problem
                continue
            test_in_box.write_text(body)
            code, output = _run(target, box)
            if missing := _MISSING_PACKAGE.search(output):
                raise ToolMissing(f"`{missing.group(0)}`: install the test dependencies, then draft again")
            verdict = classify(code, output, names, test_in_box.name)
            if verdict == "finding":
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(test_in_box, final)
                message = "a property fails on the real code. Decide: a bug in the code, or a wrong law?"
                return DraftResult(True, message, [final], round_no, _tail(output), notes=notes, laws=laws), target
            if verdict == "error":
                why = "ran no tests" if code == 0 else "did not run cleanly"
                prompt += f"\n\nThe test file {why}:\n{_tail(output)}\nFix the test (not the code)."
                continue
            if not _exercises_copy(target, box, rel_sources):
                raise RungError(
                    "the tests pass, but they do not import the code being tested from the project (an "
                    "installed copy of the package?), so deliberate bugs could not reach them. Make the "
                    "project's own tests import its source, then draft again."
                )
            score = mutation_score(target, box, rel_sources)
            warnings = [f"survived: {m.describe}" for m in score.survived]
            warnings += [f"not counted (broke the build or timed out): {m.describe}" for m in score.nonviable]
            if score.viable == 0 or len(score.caught) / score.viable >= GOOD_ENOUGH:
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(test_in_box, final)
                if score.viable:
                    caught = f"{len(score.caught)} of {score.viable}"
                    message = f"properties hold on the real code and caught {caught} deliberate bugs"
                else:
                    message = (
                        "properties hold on the real code; INCONCLUSIVE: no deliberate bug could be made to test them"
                    )
                result = DraftResult(True, message, [final], round_no, notes=notes, laws=laws,
                                     warnings=warnings + undeclared(root, target))  # fmt: skip
                return result, target
            listed = "\n".join(f"- {m.describe}" for m in score.survived)
            prompt += (
                f"\n\nYour properties pass, but they also pass when the code is broken like this:\n{listed}\n"
                "Strengthen them so a bug like these fails a property: pin exact values the law implies and "
                "include boundary inputs. Do not test implementation details."
            )
    kept = _keep_failed(root, target, body) if body else None
    message = f"no property file caught at least half the deliberate bugs after {max_rounds} rounds"
    if kept:
        message += f"; the last attempt is in {kept.relative_to(root)}"
    return DraftResult(False, message, [kept] if kept else [], max_rounds, notes=notes, laws=laws), target
