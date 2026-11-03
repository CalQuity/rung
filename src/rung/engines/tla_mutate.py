"""Fail-ability: show that a TLA+ spec's invariants can catch something.

A spec that passes TLC proves its invariants over every reachable state, but an invariant that holds
no matter what the actions do proves nothing. Rung tests the invariants the way mutation testing tests
a test suite: it removes one guard at a time from the actions (a conjunct with no primed variable, such
as `pc[c] = "idle"` or `status # "paid"`) and runs TLC on each mutant. A guard is there to prevent
something; if no invariant notices any guard disappearing, the invariants are too weak to catch a bug.

A mutant counts as *caught* when TLC reports an invariant or temporal property violation other than
`TypeOK` (a type invariant says nothing about the promise). A mutant that times out or does not parse
is *inconclusive* and is not counted either way. Conjunction is commutative, so reordering conjuncts
changes nothing; only guards are removed.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rung.engines import tla

MAX_MUTANTS = 16
MUTANT_TIMEOUT = 60
_DEFINITION = re.compile(r"^([A-Za-z_]\w*)\s*(\([^)]*\))?\s*==(?!=)")
_END = re.compile(r"^(?:={4,}|-{4,}|[A-Z_]+\b(?!\s*(?:\(|==)))")  # module end, a separator, or a keyword
_PRIMED = re.compile(r"[\w)\]>]'")
_LINE_COMMENT = re.compile(r"\\\*.*$", re.M)
_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)
NOT_ACTIONS = frozenset({"Init", "Next", "Spec", "vars", "TypeOK", "Fairness", "Terminated"})


@dataclass
class Mutant:
    action: str
    guard: str  # the removed conjunct, on one line
    text: str  # the whole mutated module


@dataclass
class MutantResult:
    action: str
    guard: str
    result: str  # caught | survived | inconclusive
    detail: str


@dataclass
class FailAbility:
    spec_digest: str
    results: list[MutantResult] = field(default_factory=list)

    def count(self, result: str) -> int:
        return sum(1 for r in self.results if r.result == result)

    @property
    def ok(self) -> bool:
        """At least one guard mutant was caught, or there was nothing to remove."""
        return self.count("caught") > 0 or self.count("survived") == 0

    @property
    def summary(self) -> str:
        caught, survived, unsure = self.count("caught"), self.count("survived"), self.count("inconclusive")
        if not self.results:
            return "no action guards to remove, so the invariants were not tested"
        text = f"{caught}/{caught + survived} guard mutants caught by an invariant"
        return text + (f" ({unsure} inconclusive)" if unsure else "")

    def survivors(self) -> list[str]:
        return [f"{r.action}: `{r.guard}`" for r in self.results if r.result == "survived"]

    def as_dict(self) -> dict[str, Any]:
        return {"spec_digest": self.spec_digest, "summary": self.summary, "caught": self.count("caught"),
                "survived": self.count("survived"), "inconclusive": self.count("inconclusive"),
                "mutants": [r.__dict__ for r in self.results]}  # fmt: skip


def spec_digest(spec: Path, config: Path | None) -> str:
    h = hashlib.sha256()
    for f in (spec, config):
        if f is not None and f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _code(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _definitions(lines: list[str]) -> list[tuple[str, int, int]]:
    """(name, first line, end line) of each top-level definition; a definition starts in column 0."""
    starts = [(m.group(1), i) for i, line in enumerate(lines) if (m := _DEFINITION.match(line))]
    out = []
    for n, (name, start) in enumerate(starts):
        limit = starts[n + 1][1] if n + 1 < len(starts) else len(lines)
        end = next((i for i in range(start + 1, limit) if _END.match(lines[i])), limit)
        out.append((name, start, end))
    return out


def _conjuncts(lines: list[str], start: int, end: int) -> list[tuple[int, int, int]]:
    """(first line, end line, column) of each conjunct in the first `/\\` bullet list of a definition."""
    for i in range(start, end):
        line = lines[i]
        from_col = line.index("==") + 2 if i == start else 0
        col = line.find("/\\", from_col)
        if col == -1 or (i != start and line[:col].strip()):
            if _code(line[from_col:]).strip():
                return []  # the definition starts with something other than a conjunction list
            continue
        bullets = [i]
        stop = end
        for j in range(i + 1, end):
            text = lines[j]
            if not _code(text).strip():
                continue
            indent = len(text) - len(text.lstrip())
            if indent < col:
                stop = j
                break
            if indent == col:
                if text[col : col + 2] != "/\\":
                    stop = j
                    break
                bullets.append(j)
        return [(b, bullets[k + 1] if k + 1 < len(bullets) else stop, col) for k, b in enumerate(bullets)]
    return []


def guard_mutants(text: str) -> list[Mutant]:
    """One mutant per guard conjunct of each action, with that conjunct replaced by `TRUE`."""
    lines = text.splitlines(keepends=True)
    mutants = []
    for name, start, end in _definitions(lines):
        body = _code("".join(lines[start:end]))
        if name in NOT_ACTIONS or not _PRIMED.search(body):
            continue
        for first, stop, col in _conjuncts(lines, start, end):
            conjunct = _code("".join(lines[first:stop]))
            if _PRIMED.search(conjunct) or "UNCHANGED" in conjunct:
                continue
            guard = " ".join(conjunct.split())
            guard = guard[guard.index("/\\") + 2 :].strip() if "/\\" in guard else guard
            if not guard or guard == "TRUE":
                continue
            mutated = [*lines[:first], lines[first][:col] + "/\\ TRUE\n", *lines[stop:]]
            mutants.append(Mutant(name, guard[:120], "".join(mutated)))
    return mutants[:MAX_MUTANTS]


def classify(result: tla.TLCResult) -> tuple[str, str]:
    if result.verdict == "fail" and result.violated not in ("deadlock", "TypeOK"):
        return "caught", f"{result.violated} violated"
    if result.verdict == "fail":
        return "survived", f"only {result.violated} noticed"
    if result.verdict == "pass":
        return "survived", f"no violation in {result.distinct_states} states"
    return "inconclusive", result.reason


def run(spec: Path, config: Path | None, *, timeout: int = MUTANT_TIMEOUT) -> FailAbility:
    """Run TLC on each guard mutant of `spec`, in a temporary copy of its directory."""
    spec = spec.resolve()
    outcome = FailAbility(spec_digest(spec, config))
    for mutant in guard_mutants(spec.read_text()):
        with tempfile.TemporaryDirectory(prefix="rung-mutant-") as tmp:
            for sibling in spec.parent.glob("*.tla"):  # modules the spec EXTENDS or INSTANCEs
                shutil.copyfile(sibling, Path(tmp) / sibling.name)
            target = Path(tmp) / spec.name
            target.write_text(mutant.text)
            result = tla.run(target, config.resolve() if config else None, timeout=timeout)
        verdict, detail = classify(result)
        outcome.results.append(MutantResult(mutant.action, mutant.guard, verdict, detail))
    return outcome


def repair_request(outcome: FailAbility) -> str:
    """The repair message for a spec whose invariants noticed no guard disappearing."""
    return (
        "\n\nTLC passed, but these guards can be removed without any invariant noticing:\n"
        + "\n".join(f"- {s}" for s in outcome.survivors())
        + "\nSo the invariants are too weak to catch anything. Each guard is in the code to prevent something: "
        "state that guarantee as its own named invariant (from the code and the developer's intent) and list it "
        "in the .cfg. Do not delete guards or change the actions to make this go away. Return the same fields."
    )
