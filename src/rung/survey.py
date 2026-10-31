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

    @property
    def primary_language(self) -> str:
        return max(self.languages, key=self.languages.get) if self.languages else "unknown"

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


def survey(root: Path) -> Inspection:
    root = root.resolve()
    result = Inspection(root=str(root))
    languages: Counter[str] = Counter()
    formal: dict[str, list[str]] = {"tla": [], "lean": [], "bend": [], "dafny": [], "alloy": []}
    protocol: list[Candidate] = []
    model: list[Candidate] = []

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

    pkg = _package_json(root)
    scripts = pkg.get("scripts", {})
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    pyproject = _read(root / "pyproject.toml")

    if (
        "pytest" in pyproject
        or (root / "pytest.ini").exists()
        or ((root / "tests").is_dir() and languages.get("Python"))
    ):
        result.test_commands.append("pytest")
    if "test" in scripts:
        result.test_commands.append("npm test")
    if (root / "go.mod").exists():
        result.test_commands.append("go test ./...")
    if (root / "Cargo.toml").exists():
        result.test_commands.append("cargo test")

    if any(k in deps for k in ("next", "react", "vue", "svelte", "@angular/core", "electron")):
        result.surfaces.append("web UI")
    if "electron" in deps:
        result.surfaces.append("desktop app")
    if any(k in pyproject for k in ("fastapi", "flask", "django")) or any(
        k in deps for k in ("express", "fastify", "hono")
    ):
        result.surfaces.append("HTTP API")
    if "[project.scripts]" in pyproject or "bin" in pkg or (root / "cmd").is_dir():
        result.surfaces.append("CLI")
    if not result.surfaces and languages:
        result.surfaces.append("library")

    for name in (
        "ruff.toml",
        ".ruff.toml",
        ".eslintrc",
        ".eslintrc.json",
        "eslint.config.js",
        "eslint.config.mjs",
        "biome.json",
        ".golangci.yml",
        "clippy.toml",
    ):
        if (root / name).exists():
            result.lint.append(name)
    if "[tool.ruff" in pyproject:
        result.lint.append("ruff (pyproject)")
    if "import-linter" in pyproject or (root / ".importlinter").exists():
        result.lint.append("import-linter")
    return result
