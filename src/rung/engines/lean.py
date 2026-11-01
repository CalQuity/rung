"""Build a Lean project and audit what its proofs actually rest on.

A green `lake build` is not enough when an agent wrote the proofs: `sorry`
compiles with a warning, `native_decide` trusts the compiler instead of the
kernel, and a stray `axiom` can assume the theorem outright. Rung therefore
treats a Lean check as passing only when the build succeeds, the sources contain
no escape hatches, and every named theorem depends on nothing beyond Lean's
three standard axioms.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rung.core.errors import ToolMissing

STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

# Constructs that let a "proof" compile without the kernel checking it.
ESCAPE_HATCHES = {
    "sorry": re.compile(r"\bsorry\b"),
    "admit": re.compile(r"\badmit\b"),
    "axiom declaration": re.compile(r"^\s*(?:private\s+|protected\s+)?axiom\s", re.M),
    "native_decide": re.compile(r"\bnative_decide\b"),
    "unsafe definition": re.compile(r"^\s*(?:@\[[^\]]*\]\s*)?unsafe\s", re.M),
    "implemented_by": re.compile(r"\bimplemented_by\b"),
    "extern": re.compile(r"@\[\s*extern\b"),
}

# Proofs that only work because the statement is true of anything, such as `0 ≤ n` for a natural number.
# One-line `rfl`, `simp` or `decide` proofs are left alone: they often prove real properties of a model.
TRIVIAL_PROOFS = frozenset({
    "trivial", "by trivial", "True.intro", "Nat.zero_le _", "by exact Nat.zero_le _", "Nat.zero_le n",
    "le_refl _", "Nat.le_refl _", "by exact le_refl _", "by exact Nat.le_refl _",
})  # fmt: skip
_THEOREM = re.compile(r"^(?:theorem|lemma)\s+(\S+)([\s\S]*?):=\s*([\s\S]*?)(?=^\S|\Z)", re.M)
KNOWN_GOOD_TOOLCHAIN = "leanprover/lean4:v4.35.0"

_BLOCK_COMMENT = re.compile(r"/-.*?-/", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")
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


def strip_comments(source: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def scan_sources(project: Path) -> list[str]:
    """Report every escape hatch in the project's own `.lean` files."""
    findings = []
    for path in sorted(project.rglob("*.lean")):
        if ".lake" in path.parts or path.name == "lakefile.lean":
            continue
        code = strip_comments(path.read_text())
        for label, pattern in ESCAPE_HATCHES.items():
            for match in pattern.finditer(code):
                line = code.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(project)}:{line}: {label}")
    return findings


def parse_axioms(output: str) -> dict[str, list[str]]:
    axioms: dict[str, list[str]] = {}
    for name, listed in _AXIOMS_LINE.findall(output):
        axioms[name] = [a.strip() for a in listed.split(",") if a.strip()]
    for name in _NO_AXIOMS_LINE.findall(output):
        axioms[name] = []
    return axioms


def audit_axioms(lake: str, project: Path, modules: list[str], theorems: list[str]) -> tuple[dict[str, list[str]], str]:
    """Ask Lean itself which axioms each theorem depends on."""
    source = "\n".join(f"import {m}" for m in modules) + "\n\n"
    source += "\n".join(f"#print axioms {t}" for t in theorems) + "\n"
    audit = project / ".lake" / "rung_audit.lean"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(source)
    proc = subprocess.run([lake, "env", "lean", str(audit)], cwd=project, capture_output=True, text=True, timeout=600)
    output = proc.stdout + proc.stderr
    return parse_axioms(output), output


def run(project: Path, theorems: list[str], *, timeout: int = 1800) -> LeanResult:
    """Build the project, scan for escape hatches, and audit theorem axioms."""
    lake = find_lake()
    build = subprocess.run([lake, "build"], cwd=project, capture_output=True, text=True, timeout=timeout)
    output = build.stdout + build.stderr
    if build.returncode != 0:
        return LeanResult("fail", "lake build failed", output=output)

    findings = scan_sources(project)
    axioms: dict[str, list[str]] = {}
    if theorems:
        modules = sorted({t.rsplit(".", 1)[0] for t in theorems if "." in t})
        axioms, audit_out = audit_axioms(lake, project, modules, theorems)
        output += "\n" + audit_out
        for theorem in theorems:
            if theorem not in axioms:
                findings.append(f"{theorem}: Lean did not report its axioms (does it exist?)")
                continue
            extra = sorted(set(axioms[theorem]) - STANDARD_AXIOMS)
            if extra:
                findings.append(f"{theorem}: depends on non-standard axioms {', '.join(extra)}")

    warnings = weak_theorems(project)
    if findings:
        return LeanResult("fail", f"{len(findings)} trust problem(s)", findings=findings, warnings=warnings,
                          axioms=axioms, output=output)  # fmt: skip
    checked = f"{len(theorems)} theorem(s) kernel-checked" if theorems else "build succeeded"
    if warnings:
        checked += f", {len(warnings)} possibly trivial"
    return LeanResult("pass", checked, findings=findings, warnings=warnings, axioms=axioms, output=output)
