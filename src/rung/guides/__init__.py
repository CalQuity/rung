"""Guidance shared by model prompts and agent skills, so both follow the same rules.

`tla.md` and `lean.md` are distilled, in our own words, from MIT and Apache-2.0 sources:
the TLA+ VS Code extension's knowledge base and tlaplus/AgentSkills (MIT),
opum-ai/proof-skills (MIT), leanprover/skills (Apache-2.0) and alok/vericoding-skill (MIT),
plus what Rung's own drafts got wrong in practice.
"""

from __future__ import annotations

from importlib.resources import files


def load(name: str, **values: str) -> str:
    """The text of `guides/<name>.md`, with `{placeholders}` filled from `values`."""
    text = (files(__name__) / f"{name}.md").read_text()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
