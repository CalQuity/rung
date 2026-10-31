"""Run the TLC model checker and turn its output into a structured result.

TLC is located through `RUNG_TLA2TOOLS` or `~/.cache/rung/tla2tools.jar`.
Its metadata goes to a temporary directory, so checking never litters the repo
with `states/` folders.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rung.errors import ToolMissing

DEFAULT_JAR = Path.home() / ".cache" / "rung" / "tla2tools.jar"
JAR_URL = "https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar"

_STATE_HEADER = re.compile(r"^State (\d+): <(.*)>\s*$")
_STATE_COUNTS = re.compile(r"(\d[\d,]*) states generated, (\d[\d,]*) distinct states found")
_INVARIANT = re.compile(r"^Error: Invariant (\S+) is violated")
_PROPERTY = re.compile(r"^Error: Temporal properties were violated")


@dataclass
class TraceState:
    index: int
    action: str
    values: dict[str, str]


@dataclass
class TLCResult:
    verdict: str  # pass | fail | error
    reason: str
    violated: str = ""
    trace: list[TraceState] = field(default_factory=list)
    distinct_states: int = 0
    generated_states: int = 0
    output: str = ""


def find_jar() -> Path:
    jar = Path(os.environ.get("RUNG_TLA2TOOLS", DEFAULT_JAR)).expanduser()
    if not jar.is_file():
        raise ToolMissing(
            f"tla2tools.jar not found at {jar}. Download it from {JAR_URL} or set RUNG_TLA2TOOLS to its path."
        )
    return jar


def find_java() -> str:
    java = shutil.which("java")
    if not java:
        raise ToolMissing("java is not on PATH; TLC needs Java 11 or later")
    return java


def _action_label(header: str) -> str:
    """`Claim(w1) line 12, col 3 to ... of module M` -> `Claim(w1)`."""
    return re.split(r"\s+line \d+, col", header, maxsplit=1)[0].strip()


def parse_trace(lines: list[str]) -> list[TraceState]:
    states: list[TraceState] = []
    current: TraceState | None = None
    last_var = ""
    for line in lines:
        header = _STATE_HEADER.match(line)
        if header:
            current = TraceState(int(header.group(1)), _action_label(header.group(2)), {})
            states.append(current)
            last_var = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("/\\ ") and " = " in stripped:
            name, _, value = stripped[3:].partition(" = ")
            last_var = name.strip()
            current.values[last_var] = value.strip()
        elif stripped and last_var and not stripped.startswith(("Error", "State ", "Back to state")):
            current.values[last_var] += " " + stripped
        elif not stripped:
            last_var = ""
    return states


def _count(text: str) -> int:
    return int(text.replace(",", ""))


def parse_output(output: str, returncode: int) -> TLCResult:
    lines = output.splitlines()
    result = TLCResult(verdict="error", reason="TLC did not report a result", output=output)

    counts = _STATE_COUNTS.findall(output)
    if counts:
        result.generated_states, result.distinct_states = (_count(x) for x in counts[-1])

    for line in lines:
        if m := _INVARIANT.match(line):
            result.verdict, result.violated = "fail", m.group(1)
            result.reason = f"invariant {m.group(1)} is violated"
            break
        if line.startswith("Error: Deadlock reached"):
            result.verdict, result.violated = "fail", "deadlock"
            result.reason = "deadlock reached"
            break
        if _PROPERTY.match(line):
            result.verdict, result.violated = "fail", "temporal property"
            result.reason = "a temporal property is violated"
            break

    if result.verdict == "fail":
        result.trace = parse_trace(lines)
        return result

    if "No error has been found" in output and returncode == 0:
        result.verdict, result.reason = "pass", "model checking completed with no error"
        return result

    for line in lines:
        if "Parse Error" in line or "Semantic error" in line or line.startswith("Error:"):
            result.reason = line.strip()
            break
    return result


def run(spec: Path, config: Path | None = None, *, workers: str = "auto", timeout: int = 600) -> TLCResult:
    """Model-check `spec` with TLC and return the parsed result."""
    jar, java = find_jar(), find_java()
    spec = spec.resolve()
    cmd = [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC", "-workers", workers]
    with tempfile.TemporaryDirectory(prefix="rung-tlc-") as meta:
        cmd += ["-metadir", meta]
        if config is not None:
            cmd += ["-config", str(config.resolve())]
        cmd.append(spec.name)
        try:
            proc = subprocess.run(cmd, cwd=spec.parent, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return TLCResult(verdict="error", reason=f"TLC timed out after {timeout}s")
    return parse_output(proc.stdout + proc.stderr, proc.returncode)


def render_trace(trace: list[TraceState]) -> str:
    """Print a counterexample as steps, showing only the variables each step changed."""
    out: list[str] = []
    previous: dict[str, str] = {}
    for state in trace:
        if state.index == 1:
            out.append("  1. initial state")
            changed = state.values
        else:
            out.append(f"  {state.index}. {state.action}")
            changed = {k: v for k, v in state.values.items() if previous.get(k) != v}
        for name, value in changed.items():
            out.append(f"       {name} = {value}")
        previous = state.values
    return "\n".join(out)
