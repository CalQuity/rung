"""Differential random testing: does the implementation agree with the model?

This is the step that ties a proof to the code that ships. The pattern comes
from AWS Cedar's verification-guided development: prove properties of a small
executable model (in Lean), then run the production implementation and the
model on the same random inputs and require identical outputs.

Both sides are plain commands speaking a line protocol: one input per line on
stdin, one output per line on stdout, in the same order. Inputs are sequences of
tokens drawn from an alphabet (for state machines, each token is an action). A
divergence is shrunk by deleting tokens while the two sides still disagree, so
the report shows a minimal sequence instead of a random one.
"""

from __future__ import annotations

import random
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rung.core.proc import run as run_process


@dataclass
class Side:
    command: str
    cwd: Path
    command_prefix: tuple[str, ...] = ()
    environment: dict[str, str] | None = None

    def run(self, inputs: list[str], timeout: int) -> list[str]:
        proc = run_process(
            [*self.command_prefix, *shlex.split(self.command)],
            cwd=self.cwd,
            timeout=timeout,
            stdin="".join(line + "\n" for line in inputs),
            env=self.environment,
            require_complete=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"`{self.command}` exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        outputs = proc.stdout.splitlines()
        if len(outputs) != len(inputs):
            raise RuntimeError(f"`{self.command}` printed {len(outputs)} lines for {len(inputs)} inputs")
        return [o.strip() for o in outputs]


@dataclass
class Divergence:
    input: str
    model: str
    impl: str


@dataclass
class DRTResult:
    verdict: str  # pass | fail | error
    reason: str
    cases: int = 0
    divergences: int = 0
    minimal: Divergence | None = None
    examples: list[Divergence] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    context_model: list[str] = field(default_factory=list)
    context_impl: list[str] = field(default_factory=list)


def generate(alphabet: list[str], cases: int, min_len: int, max_len: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    out = [""] if min_len == 0 else []
    while len(out) < cases:
        length = rng.randint(min_len, max_len)
        out.append(" ".join(rng.choice(alphabet) for _ in range(length)))
    return out


def shrink(case: str, model: Side, impl: Side, timeout: int, max_rounds: int = 50) -> Divergence:
    """Greedily delete tokens while the model and implementation still disagree."""
    tokens = case.split()
    current = None
    for _ in range(max_rounds):
        candidates = [" ".join(tokens[:i] + tokens[i + 1 :]) for i in range(len(tokens))]
        if not candidates:
            break
        m_out, i_out = model.run(candidates, timeout), impl.run(candidates, timeout)
        smaller = next((Divergence(c, m, i) for c, m, i in zip(candidates, m_out, i_out, strict=True) if m != i), None)
        if smaller is None:
            break
        current, tokens = smaller, smaller.input.split()
    if current is None:
        m, i = model.run([case], timeout)[0], impl.run([case], timeout)[0]
        current = Divergence(case, m, i)
    m, i = model.run([current.input], timeout)[0], impl.run([current.input], timeout)[0]
    if m == i:
        raise RuntimeError("divergence requires batch context or is nondeterministic")
    return Divergence(current.input, m, i)


def run(
    model: Side,
    impl: Side,
    *,
    alphabet: list[str],
    cases: int = 1000,
    min_len: int = 0,
    max_len: int = 8,
    seed: int = 0,
    timeout: int = 600,
) -> DRTResult:
    if not alphabet or any(not token.strip() or len(token.split()) != 1 for token in alphabet):
        return DRTResult("error", "differential test needs a non-empty `alphabet` of single non-whitespace tokens")
    if cases < 1 or min_len < 0 or max_len < min_len:
        return DRTResult("error", "invalid case count or trace length bounds")
    inputs = generate(alphabet, cases, min_len, max_len, seed)
    if not any(inputs):
        return DRTResult("error", "at least one nonempty trace is required", cases=len(inputs))
    lengths = sorted({len(line.split()) for line in inputs})
    operations = sorted({token for line in inputs for token in line.split()})
    try:
        m_out, i_out = model.run(inputs, timeout), impl.run(inputs, timeout)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return DRTResult("error", str(exc), cases=len(inputs))

    diverged = [Divergence(c, m, i) for c, m, i in zip(inputs, m_out, i_out, strict=True) if m != i]
    if not diverged:
        return DRTResult(
            "pass",
            f"{len(inputs)} random inputs, no divergence",
            cases=len(inputs),
            lengths=lengths,
            operations=operations,
        )

    shortest = min(diverged, key=lambda d: len(d.input.split()))
    context: list[str] = []
    try:
        minimal = shrink(shortest.input, model, impl, timeout)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        minimal = None
        context = inputs
    return DRTResult(
        "fail",
        f"{len(diverged)} of {len(inputs)} inputs diverge",
        cases=len(inputs),
        divergences=len(diverged),
        minimal=minimal,
        examples=diverged[:5],
        lengths=lengths,
        operations=operations,
        context=context,
        context_model=m_out if context else [],
        context_impl=i_out if context else [],
    )
