"""Make TLC's results readable by people: plain-language steps and Mermaid state diagrams.

A raw TLC trace lists every variable of every state under the spec's own action names. The drafting
model also returns a plain-language label for each action ("a customer clicks Pay") and a one-sentence
statement and harm for each invariant, so a counterexample can be told as a short story: what broke,
why it matters, and the numbered steps that got there, each with only the variables it changed.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from rung.engines.tla import TraceState

GRAPH_LIMIT = 200  # explored graphs with more distinct states than this are not drawn


def _base(action: str) -> str:
    """`Charge(c1)` -> `Charge`."""
    return action.split("(", 1)[0].strip()


def label(action: str, actions: dict[str, str]) -> str:
    """The plain-language label for a trace action, followed by its spec name; the spec name alone without one."""
    plain = actions.get(_base(action), "")
    return f"{plain} ({action})" if plain else action


def story(trace: list[TraceState], violated: str, card: dict[str, Any] | None) -> list[str]:
    """A counterexample as numbered plain-language steps, each with only the variables it changed."""
    card = card or {}
    actions: dict[str, str] = card.get("actions") or {}
    invariant = (card.get("invariants") or {}).get(violated) or {}
    out = []
    if invariant.get("statement"):
        out.append(f"broken: {invariant['statement']} ({violated})")
    if invariant.get("harm"):
        out.append(f"harm: {invariant['harm']}")
    previous: dict[str, str] = {}
    for state in trace:
        if state.loop_to is not None:
            out.append(f"  {state.index}. {state.action}: loop to state {state.loop_to}")
            continue
        if state.index == 1:
            out.append("  1. start: " + ", ".join(f"{k} = {v}" for k, v in state.values.items()))
        else:
            out.append(f"  {state.index}. {label(state.action, actions)}")
            changed = {k: v for k, v in state.values.items() if previous.get(k) != v}
            out.append(
                "       "
                + (
                    ", ".join(f"{k} = {v}" for k, v in changed.items())
                    or ("(nothing changed)" if state.values else "(state values unavailable; see raw TLC output)")
                )
            )
        previous = state.values
    return out


def _text(value: str) -> str:
    """Text that is safe inside a Mermaid label: no quotes, statement separators or colons."""
    value = value.replace(":>", "|->")  # TLC prints a function as `(c1 :> v @@ c2 :> w)`; keep the arrow
    return " ".join(value.replace('"', "'").replace(";", ",").replace(":", " -").split())


def counterexample_mermaid(trace: list[TraceState], violated: str, card: dict[str, Any] | None) -> str:
    """A `stateDiagram-v2` of the counterexample path, edges labelled in plain language."""
    actions: dict[str, str] = (card or {}).get("actions") or {}
    statement = (((card or {}).get("invariants") or {}).get(violated) or {}).get("statement", "")
    lines = ["stateDiagram-v2", "    direction TB"]
    previous: dict[str, str] = {}
    for state in trace:
        if state.loop_to is not None:
            continue
        shown = state.values if state.index == 1 else {k: v for k, v in state.values.items() if previous.get(k) != v}
        body = "<br/>".join(_text(f"{k} = {v}") for k, v in shown.items()) or (
            "(nothing changed)" if state.values else "(state values unavailable; see raw TLC output)"
        )
        title = "start" if state.index == 1 else f"step {state.index}"
        lines.append(f'    state "{title}<br/>{body}" as s{state.index}')
        previous = state.values
    if trace:
        lines.append(f"    [*] --> s{trace[0].index}")
    for before, after in itertools.pairwise(trace):
        plain = actions.get(_base(after.action), "")
        target = after.loop_to if after.loop_to is not None else after.index
        lines.append(f"    s{before.index} --> s{target} : {_text(plain or after.action)}")
    if trace:
        note = f"{violated} broken" + (f" - {statement}" if statement else "")
        lines.append(f"    note right of s{trace[-1].loop_to or trace[-1].index} : {_text(note)}")
    return "\n".join(lines) + "\n"


@dataclass
class DotGraph:
    nodes: dict[str, list[str]] = field(default_factory=dict)  # id -> the state's `x = 1` conjuncts
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, action)
    initial: list[str] = field(default_factory=list)


_DOT_NODE = re.compile(r'^(-?\d+) \[label="((?:\\.|[^"\\])*)"(.*)\]\s*;?\s*$')
_DOT_EDGE = re.compile(r'^(-?\d+) -> (-?\d+) \[label="((?:\\.|[^"\\])*)"')


def parse_dot(text: str) -> DotGraph:
    """The states and transitions in TLC's `-dump dot,actionlabels` output."""
    graph = DotGraph()
    for raw in text.splitlines():
        line = raw.strip()
        if m := _DOT_EDGE.match(line):
            graph.edges.append((m.group(1), m.group(2), m.group(3)))
        elif m := _DOT_NODE.match(line):
            body = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
            graph.nodes[m.group(1)] = [c.strip().removeprefix("/\\").strip() for c in body.split("\\n") if c.strip()]
            if "filled" in m.group(3):
                graph.initial.append(m.group(1))
    return graph


def graph_mermaid(graph: DotGraph, card: dict[str, Any] | None) -> str:
    """A `stateDiagram-v2` of every state TLC explored, edges labelled in plain language."""
    actions: dict[str, str] = (card or {}).get("actions") or {}
    names = {node: f"n{i}" for i, node in enumerate(graph.nodes, 1)}
    lines = ["stateDiagram-v2"]
    for node, values in graph.nodes.items():
        lines.append(f'    state "{"<br/>".join(_text(v) for v in values) or "(empty)"}" as {names[node]}')
    for node in graph.initial:
        lines.append(f"    [*] --> {names[node]}")
    for src, dst, action in graph.edges:
        if src in names and dst in names:
            lines.append(f"    {names[src]} --> {names[dst]} : {_text(actions.get(action, action))}")
    return "\n".join(lines) + "\n"
