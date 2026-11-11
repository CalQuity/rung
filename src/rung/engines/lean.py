"""Build a Lean project and audit what its proofs actually rest on.

A green `lake build` is not enough when an agent wrote the proofs: `sorry`
compiles with a warning, `native_decide` trusts the compiler instead of the
kernel, and a stray `axiom` can assume the theorem outright. Rung therefore
treats a Lean check as passing only when the build succeeds, the sources contain
no escape hatches, and every named theorem depends on nothing beyond Lean's
three standard axioms.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from rung.core import sandbox
from rung.core.config import managed_path
from rung.core.errors import ToolMissing
from rung.core.proc import run as run_process

STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

# Constructs that let a "proof" compile without the kernel checking it.
ESCAPE_HATCHES = {
    "sorry": re.compile(r"\bsorry(?:Ax)?\b"),
    "admit": re.compile(r"\badmit\b"),
    "axiom declaration": re.compile(
        r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|unsafe|partial)\s+)*axiom\s", re.M
    ),
    "native_decide": re.compile(r"\bnative_decide\b"),
    "unsafe definition": re.compile(r"\bunsafe\b"),
    "implemented_by": re.compile(r"\bimplemented_by\b"),
    "extern": re.compile(r"\bextern\b"),
}

# Proofs that only work because the statement is true of anything, such as `0 ≤ n` for a natural number.
# One-line `rfl`, `simp` or `decide` proofs are left alone: they often prove real properties of a model.
TRIVIAL_PROOFS = frozenset({
    "trivial", "by trivial", "True.intro", "Nat.zero_le _", "by exact Nat.zero_le _", "Nat.zero_le n",
    "le_refl _", "Nat.le_refl _", "by exact le_refl _", "by exact Nat.le_refl _",
})  # fmt: skip
_THEOREM = re.compile(r"^(?:theorem|lemma)\s+(\S+)([\s\S]*?):=\s*([\s\S]*?)(?=^\S|\Z)", re.M)
KNOWN_GOOD_TOOLCHAIN = "leanprover/lean4:v4.35.0"

_AXIOMS_LINE = re.compile(r"'([^']+)' depends on axioms: \[([^\]]*)\]")
_NO_AXIOMS_LINE = re.compile(r"'([^']+)' does not depend on any axioms")


@dataclass
class LeanResult:
    verdict: str  # pass | fail | error
    reason: str
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    axioms: dict[str, list[str]] = field(default_factory=dict)
    output: str = ""


def find_lake() -> str:
    lake = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / "lake")
    if not os.access(lake, os.X_OK):
        raise ToolMissing("lake is not installed. Install Lean with elan (https://lean-lang.org/install/) and re-run.")
    return lake


def prepare_toolchain(project: Path, timeout: int = 900) -> None:
    """Install the project's pinned toolchain before anything identifies it.

    elan installs a `lean-toolchain` on first use. Run once here, before a check records which Lean it
    used, so that install is not mistaken for the checker changing while the check ran. Errors are left
    for the check itself to report.
    """
    try:
        subprocess.run([find_lake(), "--version"], cwd=project, capture_output=True, timeout=timeout, check=False)
    except (ToolMissing, OSError, subprocess.TimeoutExpired):
        return


def toolchain() -> str:
    """The Lean toolchain to pin: `RUNG_LEAN_TOOLCHAIN`, else the newest installed release, else a known-good one.

    Never trust a model to pick it: models remember old versions, and old toolchains both lack
    current APIs and can build executables that newer operating systems refuse to load.
    """
    if pinned := os.environ.get("RUNG_LEAN_TOOLCHAIN"):
        return pinned
    elan = shutil.which("elan") or str(Path.home() / ".elan" / "bin" / "elan")
    try:
        out = subprocess.run([elan, "toolchain", "list"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return KNOWN_GOOD_TOOLCHAIN
    releases = re.findall(r"leanprover/lean4:v(\d+)\.(\d+)\.(\d+)(?![\w-])", out)
    if not releases:
        return KNOWN_GOOD_TOOLCHAIN
    newest = max(releases, key=lambda v: tuple(int(x) for x in v))
    return "leanprover/lean4:v" + ".".join(newest)


def lake_libraries(project: Path) -> list[str]:
    """The `[[lean_lib]]` names in `lakefile.toml`: the modules to import when auditing theorems."""
    try:
        import tomllib

        data = tomllib.loads((project / "lakefile.toml").read_text())
    except (OSError, ValueError):
        return []
    return [lib["name"] for lib in data.get("lean_lib", []) if isinstance(lib, dict) and lib.get("name")]


_NAMESPACE = re.compile(r"^\s*(namespace|end|section)\s+([\w.]+)?", re.M)
_DECL = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected)\s+)?(?:theorem|lemma)\s+([\w.']+)", re.M)


def discover_theorems(project: Path) -> list[str]:
    """Fully qualified names of every theorem in the project's own sources (namespaces resolved)."""
    names = []
    for path in sorted(project.rglob("*.lean")):
        if ".lake" in path.parts or path.name == "lakefile.lean":
            continue
        stack: list[str] = []
        for line in strip_comments(path.read_text()).splitlines():
            if m := _NAMESPACE.match(line):
                keyword, name = m.group(1), m.group(2)
                if keyword == "namespace" and name:
                    stack.append(name)
                elif keyword == "end" and stack and name == stack[-1]:
                    stack.pop()
                continue
            if m := _DECL.match(line):
                names.append(".".join([*stack, m.group(1)]))
    return names


def weak_theorems(project: Path) -> list[str]:
    """Theorems whose entire proof is a one-liner that would prove the statement about anything."""
    weak = []
    for path in sorted(project.rglob("*.lean")):
        if ".lake" in path.parts:
            continue
        for name, _statement, proof in _THEOREM.findall(strip_comments(path.read_text())):
            if " ".join(proof.split()) in TRIVIAL_PROOFS:
                weak.append(f"{name}: proved by `{' '.join(proof.split())}`; it may say nothing about the model")
    return weak


def theorem_statements(project: Path) -> dict[str, str]:
    """Each theorem's name and statement (the text between the name and `:=`) in the project's sources."""
    out = {}
    for path in sorted(project.rglob("*.lean")):
        if ".lake" in path.parts:
            continue
        for name, statement, _proof in _THEOREM.findall(strip_comments(path.read_text())):
            out[name] = statement.strip()
    return out


def strip_comments(source: str) -> str:
    """Remove comments and string literals, so a word inside either is never mistaken for code."""
    out: list[str] = []
    i, depth, string = 0, 0, False
    while i < len(source):
        pair = source[i : i + 2]
        char = source[i]
        if depth:
            if pair == "/-":
                depth += 1
                out.extend("  ")
                i += 2
                continue
            if pair == "-/":
                depth -= 1
                out.extend("  ")
                i += 2
                continue
        elif string:
            if char == "\\":
                out.extend("  ")
                i += 2
                continue
            if char == '"':
                string = False
        elif raw := re.match(r'r(#+)"', source[i:]):
            end_marker = '"' + raw.group(1)
            end = source.find(end_marker, i + len(raw.group(0)))
            if end < 0:
                raise ValueError("unterminated raw Lean string")
            end += len(end_marker)
            out.extend("\n" if c == "\n" else " " for c in source[i:end])
            i = end
            continue
        elif char == "'" and (literal := re.match(r"'(?:\\.|[^'\\])'", source[i:])):
            out.extend(" " * len(literal.group(0)))
            i += len(literal.group(0))
            continue
        elif char == "«":
            end = source.find("»", i + 1)
            if end < 0:
                raise ValueError("unterminated quoted Lean identifier")
            out.append(" ")
            out.extend(source[i + 1 : end])
            out.append(" ")
            i = end + 1
            continue
        elif pair == "--":
            end = source.find("\n", i)
            end = len(source) if end < 0 else end
            out.extend(" " * (end - i))
            i = end
            continue
        elif pair == "/-":
            depth = 1
            out.extend("  ")
            i += 2
            continue
        elif char == '"':
            string = True
        else:
            out.append(char)
            i += 1
            continue
        out.append("\n" if char == "\n" else " ")
        i += 1
    if depth or string:
        raise ValueError("unterminated Lean comment or string")
    return "".join(out)


def scan_sources(project: Path) -> list[str]:
    """Report every escape hatch in the project's own `.lean` files."""
    findings = []
    for path in sorted(project.rglob("*.lean")):
        if ".lake" in path.parts or path.name == "lakefile.lean":
            continue
        try:
            code = strip_comments(path.read_text())
        except ValueError as exc:
            findings.append(f"{path.relative_to(project)}: {exc}")
            continue
        for label, pattern in ESCAPE_HATCHES.items():
            for match in pattern.finditer(code):
                line = code.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(project)}:{line}: {label}")
    return findings


def parse_axioms(output: str) -> dict[str, list[str]]:
    axioms: dict[str, list[str]] = {}
    for name, listed in _AXIOMS_LINE.findall(output):
        if name in axioms:
            raise ValueError(f"duplicate axiom report for {name}")
        axioms[name] = [a.strip() for a in listed.split(",") if a.strip()]
    for name in _NO_AXIOMS_LINE.findall(output):
        if name in axioms:
            raise ValueError(f"duplicate axiom report for {name}")
        axioms[name] = []
    return axioms


def find_lean(project: Path, *, timeout: float = 10) -> str:
    """Resolve the pinned compiler through elan, without running project Lake tooling."""
    elan = shutil.which("elan") or str(Path.home() / ".elan" / "bin" / "elan")
    resolved = run_process([elan, "which", "lean"], cwd=project, timeout=timeout, require_complete=True)
    if resolved.returncode != 0 or not Path(resolved.stdout.strip()).is_file():
        raise ValueError("Lean declaration audit failed to resolve the pinned compiler")
    return resolved.stdout.strip()


def audit_axioms(
    lake: str,
    project: Path,
    modules: list[str],
    theorems: list[str],
    *,
    timeout: float = 600,
    command_prefix: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], str]:
    """Inspect declaration data without executing imported module initializers or extensions."""
    source = Path(__file__).with_name("lean_audit.lean").read_text()
    audit = managed_path(project, ".lake/rung_audit.lean")
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(source)
    started = time.monotonic()
    compiler = find_lean(project, timeout=min(10, timeout))
    libraries = [project / ".lake" / "build" / "lib" / "lean"]
    libraries += sorted((project / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
    args = [
        compiler,
        "--run",
        str(audit),
        json.dumps(modules),
        json.dumps(theorems),
        json.dumps([str(path) for path in libraries]),
    ]
    env = {
        **(environment if environment is not None else sandbox.scrubbed_env()),
        "LEAN_PATH": str(Path(compiler).parent.parent / "lib" / "lean"),
        "LEAN_SYSROOT": str(Path(compiler).parent.parent),
    }
    proc = run_process(
        [*command_prefix, *args],
        cwd=project,
        timeout=max(0.01, timeout - (time.monotonic() - started)),
        env=env,
        require_complete=True,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise ValueError("Lean declaration audit failed: " + output[-1500:])
    records = json.loads(proc.stdout)
    if not isinstance(records, list) or len(records) != len(theorems):
        raise ValueError("Lean declaration audit returned an incomplete proof set")
    axioms: dict[str, list[str]] = {}
    for record in records:
        name = record["name"]
        if name not in theorems or name in axioms or record["kind"] != "theorem" or not record["isProp"]:
            raise ValueError("Lean declaration audit did not establish a unique proposition theorem")
        axioms[name] = record["axioms"]
    return axioms, output


def run(
    project: Path,
    theorems: list[str],
    *,
    timeout: int = 1800,
    command_prefix: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> LeanResult:
    """Build the project, scan for escape hatches, and audit a nonempty theorem set."""
    project = project.resolve()
    started = time.monotonic()
    findings = scan_sources(project)
    if findings:
        return LeanResult("fail", f"{len(findings)} trust problem(s)", findings=findings)
    lake = find_lake()
    build = run_process(
        [*command_prefix, lake, "build"], cwd=project, timeout=timeout, env=environment, require_complete=True
    )
    output = build.stdout + build.stderr
    if build.returncode != 0:
        return LeanResult("fail", "lake build failed", output=output)
    if "declaration uses 'sorry'" in output or "declaration uses `sorry`" in output:
        findings.append("the build reports a declaration that uses `sorry`")
    theorems = theorems or discover_theorems(project)
    if not theorems:
        return LeanResult("fail", "no selected proposition theorems; build success is not a proof", output=output)
    modules = lake_libraries(project) or sorted({t.rsplit(".", 1)[0] for t in theorems if "." in t})
    try:
        axioms, audit_out = audit_axioms(
            lake,
            project,
            modules,
            theorems,
            timeout=max(0.01, timeout - (time.monotonic() - started)),
            command_prefix=command_prefix,
            environment=environment,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return LeanResult("error", str(exc), output=output)
    output += "\n" + audit_out
    for theorem in theorems:
        extra = sorted(set(axioms[theorem]) - STANDARD_AXIOMS)
        if extra:
            findings.append(f"{theorem}: depends on non-standard axioms {', '.join(extra)}")
    warnings = weak_theorems(project)
    if findings:
        return LeanResult(
            "fail",
            f"{len(findings)} trust problem(s)",
            findings=findings,
            warnings=warnings,
            axioms=axioms,
            output=output,
        )
    return LeanResult(
        "pass", f"{len(theorems)} theorem(s) kernel-checked", warnings=warnings, axioms=axioms, output=output
    )
