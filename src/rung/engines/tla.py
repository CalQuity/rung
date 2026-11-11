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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from rung.core.errors import ToolMissing
from rung.core.proc import run as run_process

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
    loop_to: int | None = None


@dataclass
class TLCResult:
    verdict: str  # pass | fail | error
    reason: str
    violated: str = ""
    trace: list[TraceState] = field(default_factory=list)
    distinct_states: int = 0
    generated_states: int = 0
    output: str = ""


_CFG_SECTION = re.compile(
    r"^\s*(INVARIANTS?|PROPERTY|PROPERTIES|CONSTRAINTS?|ACTION_CONSTRAINTS?|CHECK_DEADLOCK|"
    r"CONSTANTS?|SPECIFICATION|INIT|NEXT|SYMMETRY|VIEW|ALIAS|POSTCONDITION)\b(.*)$"
)


def strip_comments(text: str) -> str:
    """Remove nested TLA+/cfg comments while respecting quoted strings."""
    out: list[str] = []
    i, depth, string = 0, 0, False
    while i < len(text):
        pair, char = text[i : i + 2], text[i]
        if depth:
            if pair in ("(*", "*)"):
                depth += 1 if pair == "(*" else -1
                out.extend("  ")
                i += 2
                continue
            out.append("\n" if char == "\n" else " ")
        elif string:
            out.append(char)
            if char == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                string = False
        elif pair == "(*":
            depth = 1
            out.extend("  ")
            i += 2
            continue
        elif pair == "\\*":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            out.extend(" " * (end - i))
            i = end
            continue
        else:
            out.append(char)
            string = char == '"'
        i += 1
    if depth or string:
        raise ValueError("unterminated TLA+ comment or string")
    return "".join(out)


def module_paths(spec: Path) -> list[Path]:
    """Resolve supported local imports; standard modules come from the bound checker jar."""
    if any(
        "TLA-Library" in os.environ.get(key, "") for key in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS")
    ):
        raise ValueError("external TLA-Library resolution is unsupported; put dependencies beside the spec")
    with zipfile.ZipFile(find_jar()) as jar:
        standard = {Path(name).stem for name in jar.namelist() if name.endswith(".tla")}
    pending, found = [spec], []
    while pending:
        path = pending.pop()
        if path in found:
            continue
        if not path.resolve().is_relative_to(spec.parent.resolve()):
            raise ValueError("external module symlink is unsupported")
        text = strip_comments(path.read_text())
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
        if re.search(r"\bTLCSet\b", code):
            raise ValueError("programmatic TLC control (TLCSet) is unsupported")
        found.append(path)
        modules = re.findall(r"\bINSTANCE\s+(\w+)", code)
        for group in re.findall(r"\bEXTENDS\s+(\w+(?:\s*,\s*\w+)*)", code):
            modules.extend(re.findall(r"\w+", group))
        for module in modules:
            local = spec.parent / f"{module}.tla"
            if local.exists():
                pending.append(local)
            elif module not in standard:
                raise ValueError(f"unresolved module {module}; put it beside the spec")
    return found


def declared_properties(cfg: Path) -> dict[str, list[str]]:
    """What a TLC config asks TLC to check, and settings that narrow what a pass means."""
    out: dict[str, list[str]] = {"invariants": [], "properties": [], "narrowing": []}
    try:
        text = cfg.read_text()
    except OSError:
        return out
    section = ""
    for raw in strip_comments(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := _CFG_SECTION.match(line):
            section, rest = m.group(1), m.group(2).strip()
            if section.startswith("CONSTRAINT") or section.startswith("ACTION_CONSTRAINT"):
                out["narrowing"].append("state constraint")
            if section == "CHECK_DEADLOCK" and rest.upper().startswith("FALSE"):
                out["narrowing"].append("deadlock checking off")
            names = rest.split()
        else:
            names = line.split()
        if section.startswith(("INVARIANT", "PROPERT")) and any(
            not re.fullmatch(r"[A-Za-z_]\w*(?:![A-Za-z_]\w*)*", n) for n in names
        ):
            raise ValueError("unsupported property list in TLC config")
        if section.startswith("INVARIANT"):
            out["invariants"] += names
        elif section.startswith("PROPERT"):
            out["properties"] += names
    return out


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
        loop = re.match(r"^(?:State (\d+): Stuttering|(?:State \d+: )?Back to state (\d+))", line)
        if loop and states:
            target = states[-1].index if loop.group(1) else int(loop.group(2))
            states.append(
                TraceState(states[-1].index + 1, "Stuttering" if loop.group(1) else "Back to state", {}, target)
            )
            current, last_var = None, ""
            continue
        header = _STATE_HEADER.match(line)
        if header:
            current = TraceState(int(header.group(1)), _action_label(header.group(2)), {})
            states.append(current)
            last_var = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        value_line = stripped.removeprefix("/\\ ")
        if re.match(r"^[A-Za-z_]\w* = ", value_line):
            name, _, value = value_line.partition(" = ")
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


def run(
    spec: Path, config: Path | None = None, *, workers: str = "auto", timeout: int = 600, dump: Path | None = None
) -> TLCResult:
    """Model-check `spec` with TLC and return the parsed result; `dump` writes the state graph there as DOT."""
    jar, java = find_jar(), find_java()
    try:
        module_paths(spec)
        if config is not None:
            declared = declared_properties(config)
            if not declared["invariants"] and not declared["properties"]:
                return TLCResult("error", "the config selects no invariant or property")
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        return TLCResult("error", str(exc))
    spec = spec.resolve()
    cmd = [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC", "-workers", workers]
    with tempfile.TemporaryDirectory(prefix="rung-tlc-") as meta:
        cmd += ["-metadir", meta]
        if config is not None:
            cmd += ["-config", str(config.resolve())]
        if dump is not None:
            cmd += ["-dump", "dot,actionlabels", str(dump.resolve().with_suffix(""))]
        cmd.append(spec.name)
        try:
            proc = run_process(cmd, cwd=spec.parent, timeout=timeout, require_complete=True)
        except subprocess.TimeoutExpired:
            return TLCResult(verdict="error", reason=f"TLC timed out after {timeout}s")
    return parse_output(proc.stdout + proc.stderr, proc.returncode)


def render_trace(trace: list[TraceState]) -> str:
    """Print a counterexample as steps, showing only the variables each step changed."""
    out: list[str] = []
    previous: dict[str, str] = {}
    for state in trace:
        if state.loop_to is not None:
            out.append(f"  {state.index}. {state.action}: loop to state {state.loop_to}")
            continue
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
