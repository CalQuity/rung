"""Read a repository and describe what can be verified, without running anything.

The inspection is deliberately heuristic and cheap. It answers the questions
the planner needs: which languages, how tests run, what a user touches, which
files look like stateful protocols (candidates for TLA+), which look like pure
logic (candidates for a Lean model), and which formal artifacts already exist.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
})  # fmt: skip
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
    projects: list[Project] = field(default_factory=list)

    @property
    def primary_language(self) -> str:
        return max(self.languages, key=lambda name: self.languages[name]) if self.languages else "unknown"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def walk(root: Path):
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file():
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
        return path.read_text(errors="ignore")[:limit]
    except OSError:
        return ""


def _package_json(root: Path) -> dict:
    try:
        return json.loads((root / "package.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


PY_TOOL_CONFIGS = ("ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", "pyrightconfig.json")
MANIFESTS = frozenset(
    {"pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pytest.ini", "setup.py", *PY_TOOL_CONFIGS}
)
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
    surfaces: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    # Static checks the project has already configured, as (kind, command): kind is typecheck or lint.
    static: list[tuple[str, str]] = field(default_factory=list)
    # Tools that are configured but could not be recorded as a check, with the reason.
    unrecorded: list[str] = field(default_factory=list)


def _is_solution_config(path: Path) -> bool:
    """A tsconfig with `"files": []` and `references`: it type-checks nothing by itself."""
    text = path.read_text(errors="ignore")
    return '"references"' in text and re.search(r'"files"\s*:\s*\[\s*\]', text) is not None


def _node_runner(d: Path) -> str:
    if (d / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (d / "yarn.lock").exists():
        return "yarn"
    if (d / "bun.lockb").exists() or (d / "bun.lock").exists():
        return "bun"
    return "npm"


def _detect_project(root: Path, rel: str) -> Project | None:
    d = root / rel
    project = Project(path=rel)
    pyproject = _read(d / "pyproject.toml")
    pkg = _package_json(d)

    if pyproject or any((d / n).exists() for n in ("setup.py", "pytest.ini", *PY_TOOL_CONFIGS)):
        project.ecosystems.append("python")
        has_tests = "pytest" in pyproject or (d / "pytest.ini").exists() or (d / "tests").is_dir()
        if has_tests:
            project.test_command = "uv run pytest" if (d / "uv.lock").exists() else "python -m pytest"
        if any(k in pyproject for k in ("fastapi", "flask", "django", "starlette", "aiohttp")):
            project.surfaces.append("HTTP API")
        if "[project.scripts]" in pyproject:
            project.surfaces.append("CLI")
        if "[tool.ruff" in pyproject:
            project.lint.append("ruff (pyproject)")
        if "import-linter" in pyproject or (d / ".importlinter").exists():
            project.lint.append("import-linter")
        run = "uv run " if (d / "uv.lock").exists() else "python -m "
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
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        test_script = str(pkg.get("scripts", {}).get("test", ""))
        if test_script and _NO_TEST_SCRIPT not in test_script and not project.test_command:
            project.test_command = f"{_node_runner(d)} test"
        if any(k in deps for k in ("next", "react", "vue", "svelte", "@angular/core", "solid-js", "astro")):
            project.surfaces.append("web UI")
        if "electron" in deps:
            project.surfaces.append("desktop app")
        if any(k in deps for k in ("express", "fastify", "hono", "koa", "@nestjs/core")):
            project.surfaces.append("HTTP API")
        if "bin" in pkg:
            project.surfaces.append("CLI")
        scripts = pkg.get("scripts", {}) or {}
        runner = _node_runner(d)
        typecheck = next((s for s in ("typecheck", "type-check", "tsc", "check-types") if s in scripts), "")
        if typecheck:
            project.static.append(("typecheck", f"{runner} run {typecheck}"))
        elif "typescript" in deps and (d / "tsconfig.json").exists():
            if _is_solution_config(d / "tsconfig.json"):
                # `tsc --noEmit` on a solution config checks nothing; leave it to the developer.
                project.unrecorded.append("tsc (tsconfig.json only references other projects; add a typecheck script)")
            else:
                project.static.append(("typecheck", f"{'npx' if runner == 'npm' else runner + ' exec'} tsc --noEmit"))
        npx = "npx" if runner == "npm" else f"{runner} exec"
        if "lint" in scripts:
            project.static.append(("lint", f"{runner} run lint"))
        elif any((d / n).exists() for n in ESLINT_CONFIGS) and "eslint" in deps:
            project.static.append(("lint", f"{npx} eslint ."))
    if (d / "go.mod").exists():
        project.ecosystems.append("go")
        project.test_command = project.test_command or "go test ./..."
        project.static.append(("vet", "go vet ./..."))
        if (d / "cmd").is_dir():
            project.surfaces.append("CLI")
    if (d / "Cargo.toml").exists():
        project.ecosystems.append("rust")
        project.test_command = project.test_command or "cargo test"
        project.static.append(("lint", "cargo clippy --all-targets -- -D warnings"))
    project.lint += [name for name in LINT_FILES if (d / name).exists()]
    return project if project.ecosystems else None


def survey(root: Path) -> Inspection:
    root = root.resolve()
    result = Inspection(root=str(root))
    languages: Counter[str] = Counter()
    formal: dict[str, list[str]] = {"tla": [], "lean": [], "bend": [], "dafny": [], "alloy": []}
    protocol: list[Candidate] = []
    model: list[Candidate] = []
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
        hits = len(stateful_terms(code))
        if hits >= 6:
            protocol.append(Candidate(posix, hits, f"{hits} state/retry/lease terms in {lines} lines"))
        effects = len(SIDE_EFFECTS.findall(code))
        functions = len(re.findall(r"^\s*(?:def|function|fn|func)\s", code, re.M))
        if functions >= 2 and effects == 0 and lines <= 600:
            model.append(Candidate(posix, functions, f"{functions} functions and no I/O; a small pure core"))

    result.languages = dict(languages.most_common())
    result.formal = {k: v for k, v in formal.items() if v}
    result.protocol_candidates = sorted(protocol, key=lambda c: -c.score)[:8]
    result.model_candidates = sorted(model, key=lambda c: -c.score)[:8]

    result.projects = [p for d in sorted(manifest_dirs) if (p := _detect_project(root, d))]
    for project in result.projects:
        where = "" if project.path == "." else f" (in {project.path})"
        if project.test_command:
            result.test_commands.append(project.test_command + where)
        for surface in project.surfaces:
            if surface not in result.surfaces:
                result.surfaces.append(surface)
        result.lint += [f"{name}{where}" for name in project.lint]
    if not result.surfaces and languages:
        result.surfaces.append("library")
    return result
