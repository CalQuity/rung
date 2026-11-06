"""Read a repository and describe what can be verified, without running anything.

The inspection is deliberately heuristic and cheap. It answers the questions
the planner needs: which languages, how tests run, what a user touches, which
files look like stateful protocols (candidates for TLA+), which look like pure
logic (candidates for a Lean model), and which formal artifacts already exist.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rung.core import git

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".lake",
    "dist",
    "build",
    ".next",
    "target",
    ".mypy_cache",
    ".pytest_cache",
    ".rung",
    "vendor",
}
LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".cs": "C#",
    ".swift": "Swift",
}
# Words that tend to appear where state transitions, leases and retries live.
STATEFUL_WORDS = frozenset({
    "state", "status", "transition", "retry", "retries", "backoff", "lease", "leases", "lock", "locks",
    "claim", "claimed", "queue", "dispatch", "timeout", "expire", "expired", "expires", "ack", "nack",
    "commit", "rollback", "reconcile", "reconciliation", "idempotent", "idempotency", "attempt", "attempts",
    # Code that moves money: a missed interleaving here charges a customer twice.
    "charge", "charges", "charged", "paid", "payment", "payments", "refund", "refunds", "refunded", "checkout",
    "capture", "captured", "settle", "settled", "transfer", "balance",
})  # fmt: skip
# UI components re-render on state, so their "state" is not a protocol, and they are not a pure core either.
UI_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte")
_REACT = re.compile(r"""from\s+['"]react['"]|require\(['"]react['"]\)""")


def is_ui_component(rel: Path, text: str) -> bool:
    return rel.suffix in UI_SUFFIXES or "components" in rel.parts or bool(_REACT.search(text))


_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def stateful_terms(code: str) -> list[str]:
    """Protocol-flavoured words in identifiers, splitting snake_case and camelCase (`leaseValid` -> lease)."""
    return [w for w in (m.lower() for m in _WORD.findall(code)) if w in STATEFUL_WORDS]


SIDE_EFFECTS = re.compile(
    r"\b(open|requests|httpx|fetch|subprocess|socket|cursor|execute|session|write|print|"
    r"datetime\.now|time\.time|random)\b"
)


@dataclass
class Candidate:
    path: str
    score: int
    reason: str


@dataclass
class Inspection:
    root: str
    languages: dict[str, int] = field(default_factory=dict)
    test_commands: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    formal: dict[str, list[str]] = field(default_factory=dict)
    protocol_candidates: list[Candidate] = field(default_factory=list)
    model_candidates: list[Candidate] = field(default_factory=list)
    agent_docs: list[str] = field(default_factory=list)
    app_files: list[str] = field(default_factory=list)  # files that serve the app itself (a request handler)
    projects: list[Project] = field(default_factory=list)

    @property
    def primary_language(self) -> str:
        return max(self.languages, key=lambda name: self.languages[name]) if self.languages else "unknown"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def walk(root: Path):
    for name in git.list_files(root):
        rel = Path(name)
        path = root / rel
        if not SKIP_DIRS.intersection(rel.parts) and not path.is_symlink():
            yield path, rel


_STRINGS_AND_COMMENTS = re.compile(
    r'"""[\s\S]*?"""'  # Python triple-quoted strings
    r"|'''[\s\S]*?'''"
    r'|"(?:\\.|[^"\\\n])*"'  # double-quoted strings
    r"|'(?:\\.|[^'\\\n])*'"  # single-quoted strings
    r"|`(?:\\.|[^`\\])*`"  # template literals
    r"|/\*[\s\S]*?\*/"  # block comments
    r"|//[^\n]*"  # line comments
    r"|#[^\n]*"
)


def code_only(text: str) -> str:
    """Strip string literals and comments so keyword counts reflect code, not prose."""
    return _STRINGS_AND_COMMENTS.sub(" ", text)


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        if path.is_symlink():
            return ""
        with path.open(encoding="utf-8", errors="ignore") as source:
            return source.read(limit)
    except OSError:
        return ""


def _package_json(root: Path) -> dict:
    try:
        return _mapping(json.loads(_read(root / "package.json")))
    except json.JSONDecodeError:
        return {}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


PY_TOOL_CONFIGS = ("ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", "pyrightconfig.json")
PY_REQUIREMENTS = ("requirements.txt", "requirements-dev.txt", "requirements-test.txt")
MANIFESTS = frozenset({
    "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pytest.ini", "setup.py", "pom.xml",
    "build.gradle", "build.gradle.kts", "Makefile", "makefile", "GNUmakefile", "Gemfile", "Rakefile",
    "mix.exs", "Package.swift", *PY_TOOL_CONFIGS, *PY_REQUIREMENTS,
})  # fmt: skip
ESLINT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", ".eslintrc",
                  ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs")  # fmt: skip
MAX_PROJECT_DEPTH = 6  # monorepos nest packages (packages/web/app); deeper is almost always fixtures
_NO_TEST_SCRIPT = "no test specified"
LINT_FILES = (
    "ruff.toml", ".ruff.toml", ".eslintrc", ".eslintrc.json", "eslint.config.js", "eslint.config.mjs",
    "biome.json", ".golangci.yml", "clippy.toml",
)  # fmt: skip


@dataclass
class Project:
    """One buildable project inside the repository: the root, or e.g. `backend/` and `frontend/`."""

    path: str
    ecosystems: list[str] = field(default_factory=list)
    test_command: str = ""
    additional_tests: list[tuple[str, str]] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    # Static checks the project has already configured, as (kind, command): kind is typecheck or lint.
    static: list[tuple[str, str]] = field(default_factory=list)
    # Tools that are configured but could not be recorded as a check, with the reason.
    unrecorded: list[str] = field(default_factory=list)

    def add_test(self, ecosystem: str, command: str) -> None:
        if not self.test_command:
            self.test_command = command
        else:
            self.additional_tests.append((f"{ecosystem}-tests", command))


def _is_solution_config(path: Path) -> bool:
    """A tsconfig with `"files": []` and `references`: it type-checks nothing by itself."""
    text = path.read_text(errors="ignore")
    return '"references"' in text and re.search(r'"files"\s*:\s*\[\s*\]', text) is not None


def _node_runner(d: Path, root: Path) -> str:
    for current in (d, *d.parents):
        manager = str(_package_json(current).get("packageManager", "")).partition("@")[0]
        if manager in {"npm", "pnpm", "yarn", "bun"}:
            return manager
        for tool, files in (
            ("pnpm", ("pnpm-lock.yaml", "pnpm-workspace.yaml")),
            ("yarn", ("yarn.lock",)),
            ("bun", ("bun.lockb", "bun.lock")),
            ("npm", ("package-lock.json", "npm-shrinkwrap.json")),
        ):
            if any((current / name).is_file() for name in files):
                return tool
        if current == root:
            break
    return "npm"


def _uses_uv(d: Path, root: Path) -> bool:
    return any((p / "uv.lock").is_file() for p in (d, *d.parents) if p.is_relative_to(root))


def _remove_duplicate_make_tests(root: Path, projects: list[Project]) -> None:
    """Prefer a discovered suite over a Make target whose sole recipe delegates to it."""
    by_path = {project.path: project for project in projects}
    for project in projects:
        if project.test_command != "make test":
            continue
        cwd = root / project.path
        makefile = next((cwd / name for name in ("GNUmakefile", "makefile", "Makefile")
                         if (cwd / name).is_file()), None)  # fmt: skip
        if makefile is None:
            continue
        text = _read(makefile)
        targets = re.findall(r"^([^#:\t\n]+):", text, re.M)
        if sum("test" in names.split() for names in targets) != 1:
            continue
        if re.search(r"^[ ]*(?:-?include|sinclude|define|export|unexport|override|ifeq|ifneq|ifdef|ifndef)\b"
                     r"|^[^#\t\n]*=|^\.(?:RECIPEPREFIX|EXPORT_ALL_VARIABLES|ONESHELL)\b", text, re.M):  # fmt: skip
            continue
        recipe = re.search(r"^test:[ ]*\n\t([^\n]+)(?:\n(?!(?:[ ]*(?:#[^\n]*)?\n)*\t)|\Z)", text, re.M)
        if not recipe or re.search(r"[$`\\|;<>]", recipe[1]):
            continue
        try:
            argv = shlex.split(recipe[1])
        except ValueError:
            continue
        if len(argv) < 4 or argv[0] != "cd" or argv[2] != "&&" or "&&" in argv[3:]:
            continue
        child = (cwd / argv[1]).resolve()
        if child == cwd or not child.is_relative_to(root):
            continue
        delegate = by_path.get(child.relative_to(root).as_posix())
        if not delegate or not delegate.test_command:
            continue
        command = argv[3:]
        if command[-1] in {"-q", "--quiet"} and "pytest" in command:
            command = command[:-1]
        suites = [delegate.test_command, *(cmd for _, cmd in delegate.additional_tests)]
        if not any(command == shlex.split(suite) for suite in suites):
            continue
        project.test_command = project.additional_tests.pop(0)[1] if project.additional_tests else ""


def _detect_project(root: Path, rel: str) -> Project | None:
    d = root / rel
    project = Project(path=rel)
    pyproject = _read(d / "pyproject.toml")
    pkg = _package_json(d)
    make_test = any(re.search(r"^test\s*:", _read(d / name), re.M) for name in ("GNUmakefile", "makefile", "Makefile"))
    if make_test:
        project.ecosystems.append("make")

    requirements = [_read(d / name) for name in PY_REQUIREMENTS]
    if pyproject or any(requirements) or any((d / n).exists() for n in ("setup.py", "pytest.ini", *PY_TOOL_CONFIGS)):
        project.ecosystems.append("python")
        uv = _uses_uv(d, root)
        pytest_req = any(re.search(r"^\s*pytest(?:\s|[<>=!~;\[]|$)", text, re.M) for text in requirements)
        if "pytest" in pyproject or pytest_req or (d / "pytest.ini").is_file():
            project.add_test("python", "uv run pytest" if uv else "python -m pytest")
        elif not project.test_command and (d / "tests").is_dir():
            sources = sorted((d / "tests").glob("test_*.py"))[:30]
            tests = [_read(file) for file in sources]
            unittest = re.compile(r"^(?:import unittest|from unittest import)\b", re.M)
            if tests and all(unittest.search(text) for text in tests):
                project.add_test("python", "python -m unittest discover -s tests")
            elif any(re.search(r"^(?:async )?def test_\w+\s*\(", text, re.M) for text in tests):
                project.add_test("python", "uv run pytest" if uv else "python -m pytest")
            elif not make_test:
                project.unrecorded.append("tests (test directory found; configure its runner in rung.toml)")
        if any(k in pyproject for k in ("fastapi", "flask", "django", "starlette", "aiohttp")):
            project.surfaces.append("HTTP API")
        if "[project.scripts]" in pyproject:
            project.surfaces.append("CLI")
        if "[tool.ruff" in pyproject:
            project.lint.append("ruff (pyproject)")
        if "import-linter" in pyproject or (d / ".importlinter").exists():
            project.lint.append("import-linter")
        run = "uv run " if uv else "python -m "
        pyright = "[tool.pyright" in pyproject or (d / "pyrightconfig.json").exists()
        mypy = "[tool.mypy" in pyproject or (d / "mypy.ini").exists() or (d / ".mypy.ini").exists()
        if pyright:
            project.static.append(("typecheck", f"{run}pyright"))
        if mypy:  # both can be configured; each gets its own check
            project.static.append(("mypy" if pyright else "typecheck", f"{run}mypy ."))
        if "[tool.ruff" in pyproject or (d / "ruff.toml").exists() or (d / ".ruff.toml").exists():
            project.static.append(("lint", f"{run}ruff check ."))
        if "import-linter" in project.lint:
            project.static.append(("imports", "uv run lint-imports" if run == "uv run " else "lint-imports"))
    if pkg:
        project.ecosystems.append("node")
        deps = _mapping(pkg.get("dependencies")) | _mapping(pkg.get("devDependencies"))
        scripts = _mapping(pkg.get("scripts"))
        runner = _node_runner(d, root)
        test_script = scripts.get("test")
        if isinstance(test_script, str) and test_script.strip() and _NO_TEST_SCRIPT not in test_script:
            project.add_test("node", f"{runner} test")
        if any(k in deps for k in ("next", "react", "vue", "svelte", "@angular/core", "solid-js", "astro")):
            project.surfaces.append("web UI")
        if "electron" in deps:
            project.surfaces.append("desktop app")
        if any(k in deps for k in ("express", "fastify", "hono", "koa", "@nestjs/core")):
            project.surfaces.append("HTTP API")
        if "bin" in pkg:
            project.surfaces.append("CLI")
        npx = "npx --no-install" if runner == "npm" else f"{runner} exec"
        typecheck = next((s for s in ("typecheck", "type-check", "tsc", "check-types") if s in scripts), "")
        if typecheck:
            project.static.append(("typecheck", f"{runner} run {typecheck}"))
        elif "typescript" in deps and (d / "tsconfig.json").exists():
            if _is_solution_config(d / "tsconfig.json"):
                # `tsc --noEmit` on a solution config checks nothing; leave it to the developer.
                project.unrecorded.append("tsc (tsconfig.json only references other projects; add a typecheck script)")
            elif "next" in deps and ".next/" in _read(d / "tsconfig.json"):
                project.unrecorded.append("tsc (add a typecheck script that generates Next.js route types before tsc)")
            else:
                project.static.append(("typecheck", f"{npx} tsc --noEmit"))
        if "lint" in scripts:
            project.static.append(("lint", f"{runner} run lint"))
        elif any((d / n).exists() for n in ESLINT_CONFIGS) and "eslint" in deps:
            project.static.append(("lint", f"{npx} eslint ."))
    if (d / "go.mod").exists():
        project.ecosystems.append("go")
        project.add_test("go", "go test ./...")
        project.static.append(("vet", "go vet ./..."))
        if (d / "cmd").is_dir():
            project.surfaces.append("CLI")
    if (d / "Cargo.toml").exists():
        project.ecosystems.append("rust")
        project.add_test("rust", "cargo test --locked")
        project.static.append(("lint", "cargo clippy --all-targets -- -D warnings"))
    if (d / "pom.xml").is_file():
        project.ecosystems.append("maven")
        project.add_test("maven", "./mvnw test" if (d / "mvnw").is_file() else "mvn test")
    if (d / "build.gradle").is_file() or (d / "build.gradle.kts").is_file():
        project.ecosystems.append("gradle")
        project.add_test("gradle", "./gradlew test" if (d / "gradlew").is_file() else "gradle test")
    rake = _read(d / "Rakefile")
    if rake or (d / "Gemfile").is_file():
        project.ecosystems.append("ruby")
        if re.search(r"Rake::TestTask|task\s+(?::test\b|['\"]test['\"])", rake):
            project.add_test("ruby", "bundle exec rake test" if (d / "Gemfile").is_file() else "rake test")
    if (d / "mix.exs").is_file():
        project.ecosystems.append("elixir")
        project.add_test("elixir", "mix test")
    if (d / "Package.swift").is_file():
        project.ecosystems.append("swift")
        project.add_test("swift", "swift test")
    if make_test and not project.test_command:
        # A native runner reports each case, which `rung prove` needs; `make test` is the fallback.
        project.add_test("make", "make test")
    project.lint += [name for name in LINT_FILES if (d / name).exists()]
    return project if project.ecosystems else None


_STDLIB_SERVER = re.compile(r"\b(BaseHTTPRequestHandler|http\.server|wsgiref\.simple_server|createServer)\b")
_HTML = re.compile(r"text/html|<html|<form|html\.escape", re.I)


def _served_surfaces(text: str) -> list[str]:
    """An app served with the standard library (`http.server`, Node's `http.createServer`) has no framework
    in its manifest to give it away; its code does."""
    if not _STDLIB_SERVER.search(text):
        return []
    return ["web UI"] if _HTML.search(text) else ["HTTP API"]


def survey(root: Path) -> Inspection:
    root = root.resolve()
    result = Inspection(root=str(root))
    languages: Counter[str] = Counter()
    formal: dict[str, list[str]] = {"tla": [], "lean": [], "bend": [], "dafny": [], "alloy": []}
    protocol: list[Candidate] = []
    model: list[Candidate] = []
    served: list[str] = []  # surfaces found in the code itself, for apps no manifest declares
    manifest_dirs: set[str] = {"."}

    for path, rel in walk(root):
        suffix = path.suffix
        if suffix in LANGUAGES:
            languages[LANGUAGES[suffix]] += 1
        posix = rel.as_posix()
        if suffix == ".tla":
            formal["tla"].append(posix)
        elif suffix == ".lean":
            formal["lean"].append(posix)
        elif suffix == ".bend":
            formal["bend"].append(posix)
        elif suffix == ".dfy":
            formal["dafny"].append(posix)
        elif suffix == ".als":
            formal["alloy"].append(posix)
        if path.name in MANIFESTS and len(rel.parts) <= MAX_PROJECT_DEPTH + 1:
            manifest_dirs.add(rel.parent.as_posix() or ".")
        if path.name in {"AGENTS.md", "CLAUDE.md", ".cursorrules"}:
            result.agent_docs.append(posix)
        if posix.startswith(".github/workflows/") or path.name in {".gitlab-ci.yml", "Jenkinsfile"}:
            result.ci.append(posix)

        if suffix not in LANGUAGES or "test" in path.stem.lower() or "tests" in rel.parts:
            continue
        text = _read(path)
        code = code_only(text)
        lines = text.count("\n") + 1
        ui = is_ui_component(rel, text)
        hits = 0 if ui else len(stateful_terms(code))
        if hits >= 6:
            protocol.append(Candidate(posix, hits, f"{hits} state/retry/lease terms in {lines} lines"))
        serves = _served_surfaces(text)
        served += [s for s in serves if s not in served]
        if serves:
            result.app_files.append(posix)
        effects = len(SIDE_EFFECTS.findall(code))
        functions = len(re.findall(r"^\s*(?:def|function|fn|func)\s", code, re.M))
        if functions >= 1 and effects == 0 and not serves and not ui and lines <= 600:
            model.append(Candidate(posix, functions, f"{functions} functions and no I/O; a small pure core"))

    result.languages = dict(languages.most_common())
    result.formal = {k: v for k, v in formal.items() if v}
    result.protocol_candidates = sorted(protocol, key=lambda c: -c.score)[:8]
    result.model_candidates = sorted(model, key=lambda c: -c.score)[:8]

    result.projects = [p for d in sorted(manifest_dirs) if (p := _detect_project(root, d))]
    _remove_duplicate_make_tests(root, result.projects)
    for project in result.projects:
        where = "" if project.path == "." else f" (in {project.path})"
        if project.test_command:
            result.test_commands += [
                cmd + where for cmd in [project.test_command, *(c for _, c in project.additional_tests)]
            ]
        for surface in project.surfaces:
            if surface not in result.surfaces:
                result.surfaces.append(surface)
        result.lint += [f"{name}{where}" for name in project.lint]
    result.surfaces += [s for s in served if s not in result.surfaces]
    if not result.surfaces and languages:
        result.surfaces.append("library")
    return result
