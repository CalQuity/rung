"""The project's verification kit: every part, how it stands, and the one command to run next.

A kit is the set of checks Rung keeps for a project, grouped by what each part catches: the linters
and type checkers it already has, its tests, property tests that must catch planted bugs, security
gates, scripts that drive the running app, formal models of the parts where a missed interleaving
or edge case is expensive, the agent skills that make an agent use all of it, and the laws it
enforces. `rung kit` shows it; `rung init` builds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rung.analysis import plan
from rung.analysis.survey import Inspection
from rung.core import laws, ledger
from rung.core.config import Check, Config

# (key, title, what this part catches), in the order a project usually adds them.
PARTS = (
    ("static", "Linters and type checkers", "the mistakes a tool can see without running anything"),
    ("tests", "Tests", "regressions in the behaviour the tests pin down"),
    ("properties", "Property tests", "inputs nobody thought to write a test for; each must catch planted bugs"),
    ("security", "Security gates", "leaked secrets, vulnerable or invented packages, risky CI"),
    ("app", "Driving the app", "what a user sees, under faults and odd inputs, with the evidence to show it"),
    ("formal", "Formal models", "interleavings and edge cases tests rarely reach, checked exhaustively"),
    ("agents", "Agent skills and hook", "an agent finishing work its checks do not back"),
)
_PLAN_PART = {"tests": "tests", "security": "security", "architecture": "static", "runtime": "app",
              "protocol": "formal", "model": "formal"}  # fmt: skip
_STATIC_WORDS = ("lint", "type", "format", "check", "fmt", "vet", "clippy", "mypy", "pyright", "ruff", "eslint")
# The order in which a check's state calls for attention.
_URGENCY = {"failing": 0, "drifted": 1, "stale": 2, "never": 3}


def part_of(check: Check) -> str:
    """Which part of the kit a check belongs to."""
    if check.kind in ("tla", "lean", "drt"):
        return "formal"
    if check.kind == "evidence":
        return "app"
    if check.kind in ("scan", "deps"):
        return "security"
    if check.kind == "shortcuts":
        return "static"
    name = check.id.lower()
    if check.get("drafted_by") and name.endswith("properties"):
        return "properties"
    if "test" in name or "spec" in name:
        return "tests"
    if any(w in name for w in _STATIC_WORDS):
        return "static"
    return "tests"


@dataclass
class Entry:
    check_id: str
    kind: str
    state: str
    detail: str = ""


@dataclass
class Part:
    key: str
    title: str
    catches: str
    entries: list[Entry] = field(default_factory=list)
    missing: str = ""  # why the part is empty, when it is
    suggest: str = ""  # the command that fills it

    @property
    def state(self) -> str:
        """`ok`, `attention` (a check is failing, stale, drifted or never ran), `missing`, or `n/a`."""
        if self.entries:
            return "ok" if all(e.state in ("fresh", "installed", "approved") for e in self.entries) else "attention"
        return "missing" if self.suggest else "n/a"


@dataclass
class Kit:
    parts: list[Part]
    laws: dict[str, int]
    next_steps: list[tuple[str, str]]  # (command, why), most urgent first

    def to_dict(self) -> dict:
        return {
            "parts": [{"part": p.key, "title": p.title, "catches": p.catches, "state": p.state,
                       "checks": [e.__dict__ for e in p.entries], "missing": p.missing, "suggest": p.suggest}
                      for p in self.parts],
            "laws": self.laws,
            "next": [{"command": c, "why": w} for c, w in self.next_steps],
        }  # fmt: skip


def agent_entries(root: Path) -> list[Entry]:
    """The agent skills Rung wrote and whether the Stop hook is installed."""
    skills = root / ".claude" / "skills"
    entries: list[Entry] = []
    if (skills / "rung" / "SKILL.md").exists():
        entries.append(Entry("rung skills + AGENTS.md", "skill", "installed"))
    for path in sorted(skills.glob("verify-*/SKILL.md")) if skills.is_dir() else []:
        entries.append(Entry(path.parent.name, "skill", "installed", "project-specific verification skill"))
    settings = root / ".claude" / "settings.json"
    if settings.exists() and "rung hook stop" in settings.read_text(errors="replace"):
        entries.append(Entry("Stop hook", "hook", "installed", "blocks an agent finishing with stale checks"))
    return entries


def map_entry(root: Path) -> Entry | None:
    """The feature map, as one entry of the app part: approved, waiting for approval, or changed since."""
    from rung.drafting import feature_map  # drafting pulls in the providers; only load it when there is a map

    state = feature_map.load_state(root)
    if state is None:
        return None
    approved, proposed = state.with_status("approved"), state.with_status("proposed")
    if drift := feature_map.drifted(root, state):
        return Entry("feature map", "map", "drifted", f"changed since approval: {', '.join(drift)}")
    if proposed or not approved:
        return Entry("feature map", "map", "proposed", f"{len(proposed)} feature(s) wait for a person's decision")
    return Entry("feature map", "map", "approved", f"{len(approved)} feature(s) approved")


def _suggestions(info: Inspection, existing: set[str]) -> dict[str, tuple[str, str]]:
    """For each part, the first recommendation that fills it: (command, why)."""
    found: dict[str, tuple[str, str]] = {}
    for rec in plan.plan(info, existing):
        part = _PLAN_PART.get(rec.rung)
        if part and part not in found and rec.command:
            found[part] = (rec.command, rec.title)
    if info.model_candidates:
        target = info.model_candidates[0].path
        found["properties"] = (f"rung draft property {target}",
                               f"Property tests for {target}, kept only if they catch planted bugs")  # fmt: skip
    if info.surfaces and info.surfaces != ["library"]:
        surface = info.surfaces[0]
        seen = "screenshots and video" if surface in ("web UI", "desktop app") else "transcripts"
        found["app"] = ("rung map", f"Map the {surface}'s features, approve them, then drive them through scenarios "
                        f"and faults, recording {seen}")  # fmt: skip
    return found


def build(config: Config, info: Inspection) -> Kit:
    from rung.checks import runner  # the runner imports the checkers; keep `rung kit --help` light

    view = ledger.View(config.root)
    statuses = {c.id: runner.status(config, c, view=view) for c in config.checks}
    suggestions = _suggestions(info, {c.id for c in config.checks})
    parts = [Part(key, title, catches) for key, title, catches in PARTS]
    by_key = {p.key: p for p in parts}
    for check in config.checks:
        s = statuses[check.id]
        detail = s.changed[0] if s.state == "drifted" and s.changed else ""
        by_key[part_of(check)].entries.append(Entry(check.id, check.kind, s.state, detail))
    by_key["agents"].entries = agent_entries(config.root)
    if (entry := map_entry(config.root)) is not None:
        by_key["app"].entries.insert(0, entry)
    for part in parts:
        if part.entries:
            continue
        if part.key == "agents":
            part.suggest, part.missing = "rung agents --hooks", "Agents are not told to use the kit"
        elif part.key in suggestions:
            part.suggest, part.missing = suggestions[part.key]
        else:
            part.missing = {
                "app": "No app, API or CLI found to drive",
                "formal": "No stateful or pure core found that a model would pay off for",
                "properties": "No small pure functions found",
            }.get(part.key, "Nothing found to add")

    law_states: dict[str, int] = {}
    for law in laws.load(config.root):
        state, _ = laws.state(law, statuses)
        law_states[state] = law_states.get(state, 0) + 1
    return Kit(parts, law_states, _next_steps(parts, law_states))


def _next_steps(parts: list[Part], law_states: dict[str, int]) -> list[tuple[str, str]]:
    steps: list[tuple[int, str, str]] = []
    for part in parts:
        for e in part.entries:
            if e.kind == "map":
                if e.state == "drifted":
                    steps.append((1, "rung map --redraft", f"the feature map is out of date: {e.detail}"))
                elif e.state == "proposed":
                    steps.append((3, "rung map", f"feature map: {e.detail}"))
                elif not any(x.kind == "evidence" for x in part.entries):
                    steps.append((3, "rung draft runtime", "write the drive script for the approved features"))
            elif e.state == "failing":
                steps.append((0, f"rung check {e.check_id}", f"{e.check_id} is failing: fix the code, not the check"))
            elif e.state == "drifted":
                steps.append(
                    (1, f"rung maintain {e.check_id}", f"the code {e.check_id} models changed since it was confirmed")
                )
    unrun = [e.check_id for p in parts for e in p.entries if e.state in ("stale", "never")]
    if unrun:
        steps.append((2, "rung check", f"{len(unrun)} check(s) stale or never run: {', '.join(unrun[:4])}"
                      + (" ..." if len(unrun) > 4 else "")))  # fmt: skip
    for part in parts:
        if part.state == "missing":
            steps.append((3, part.suggest, f"{part.title}: {part.missing}"))
    if law_states.get("proposed"):
        steps.append((4, "rung laws", f"{law_states['proposed']} proposed law(s) wait for a person to approve them"))
    steps.sort(key=lambda s: s[0])
    return [(cmd, why) for _, cmd, why in steps]
