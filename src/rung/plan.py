"""The verification ladder and the planner that picks rungs for a repository.

Rungs are ordered from cheapest to strongest. Cheap rungs apply everywhere;
the formal rungs (TLA+, Lean) are reserved for the few places where a mistake
is expensive and the behaviour can be stated precisely. A plan is a ranked
list of recommendations, each tied to concrete files and a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rung.survey import Inspection

LADDER = (
    (
        "architecture",
        "Make the wrong shortcut impossible: import boundaries and lint rules, enforced in CI.",
    ),
    (
        "runtime",
        "Drive the real app the way a user does and keep the evidence: transcripts, screenshots, video.",
    ),
    (
        "properties",
        "State invariants as property-based tests and kill weak tests with mutation testing.",
    ),
    ("protocol", "Model stateful protocols in TLA+ and model-check every interleaving with TLC."),
    (
        "model",
        "Write a small executable Lean model, prove its laws, and differentially test the code against it.",
    ),
    (
        "ledger",
        "Bind every verdict to the exact code and spec it checked; it expires when either changes.",
    ),
)


@dataclass
class Recommendation:
    rung: str
    title: str
    why: str
    targets: list[str] = field(default_factory=list)
    effort: str = "low"
    command: str = ""


def plan(info: Inspection) -> list[Recommendation]:
    recs: list[Recommendation] = []
    lang = info.primary_language

    if not info.lint:
        recs.append(
            Recommendation(
                "architecture",
                "Add a linter and import-boundary rules",
                f"No lint configuration found. Agents copy the patterns they see; for {lang}, "
                "a linter plus layer rules (e.g. import-linter, eslint no-restricted-imports) "
                "stops the shortcut before review.",
                effort="low",
            )
        )

    if info.surfaces and info.surfaces != ["library"]:
        recs.append(
            Recommendation(
                "runtime",
                f"Generate a runtime verification skill for the {info.surfaces[0]}",
                "A passing build is not proof of behaviour. Give your agent a project-local skill "
                "that launches the app, drives one feature end to end and keeps the evidence, and "
                "require it to run once before it counts. Record its evidence script as a check.",
                effort="medium",
                command="rung add runtime --run '<your evidence script>'",
            )
        )

    if info.test_commands:
        recs.append(
            Recommendation(
                "properties",
                "Record the existing test suite as a check",
                "Tests become a ledger entry, so a green run is tied to the code it ran against.",
                effort="low",
                command=f"rung add tests --run '{info.test_commands[0]}'",
            )
        )

    if info.protocol_candidates:
        top = info.protocol_candidates[:3]
        recs.append(
            Recommendation(
                "protocol",
                "Model the stateful core in TLA+",
                "These files carry state, retries, leases or reconciliation: the bugs that tests "
                "rarely reach because they need a specific interleaving. "
                + "; ".join(f"{c.path} ({c.reason})" for c in top),
                targets=[c.path for c in top],
                effort="high",
                command=f"rung draft tla {top[0].path}",
            )
        )

    if info.model_candidates:
        top = info.model_candidates[:3]
        recs.append(
            Recommendation(
                "model",
                "Model the pure core in Lean and test the code against it",
                "Small, side-effect-free logic is where an executable Lean model pays off: prove "
                "its laws once, then require the real code to agree with it on random inputs. "
                + "; ".join(f"{c.path} ({c.reason})" for c in top),
                targets=[c.path for c in top],
                effort="high",
                command=f"rung draft lean {top[0].path}",
            )
        )

    if "bend" in info.formal:
        recs.append(
            Recommendation(
                "model",
                "Check the existing Bend laws",
                "This repo has Bend files; Rung can record `bend PROOF.bend` as a check.",
                targets=info.formal["bend"][:3],
                effort="low",
            )
        )

    recs.append(
        Recommendation(
            "ledger",
            "Gate merges on fresh verdicts",
            "Add `rung check --ci` to CI. It fails when any verdict is failing or stale, so an "
            "edit to covered code cannot ride on an old green run.",
            effort="low",
            command="rung check --ci",
        )
    )
    return recs
