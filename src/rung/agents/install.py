"""Install guidance for coding agents: Claude Code skills, an AGENTS.md section, and the Stop hook.

Skills are installed for Claude Code under `.claude/skills/`; the AGENTS.md section is read by
Codex, Cursor and other agents. Every skill body comes from `rung.guides`, the same text Rung
sends to the model when it drafts specs and proofs itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from rung import guides
from rung.agents.hooks import HOOK_COMMAND
from rung.core.config import ConfigError

BEGIN, END = "<!-- rung:begin -->", "<!-- rung:end -->"

USAGE = """\
# Working with Rung

This repository proves its behaviour instead of asserting it. Rung is a CLI; this skill only
says how to use it.

## Rules

{rules}
## Commands

- `rung check --changed`: run the checks your change touches and record verdicts.
- `rung status`: `stale` means covered code or a spec changed since the last green run.
- `rung laws`: which laws hold.
- `rung maintain <check>`: update a TLA+ or Lean model after the code it describes changed.
- `rung prove '<test command>'`: show your new test fails without the fix and passes with it.
- `rung report`: everything at once; add `--json` to read it programmatically.
"""

SKILLS = {
    "rung": (
        "Verify changes with Rung (laws, scans, TLA+, Lean, differential tests, evidence ledger). Use before "
        "declaring any change done, when a check fails or goes stale, when fixing a bug, or when adding a "
        "dependency, law, spec or proof."
    ),
    "rung-tla": (
        "Write or fix a TLA+ spec that mirrors real code and model-checks with TLC. Use when creating or "
        "editing a .tla/.cfg file, or when a TLC counterexample needs reading."
    ),
    "rung-property": (
        "Write or strengthen Hypothesis or fast-check property-based tests for a law. Use when adding a "
        "property test, when `rung draft property` reports surviving mutants, or when a property fails."
    ),
    "rung-lean": (
        "Write or fix a core-only Lean 4 model with kernel-checked proofs and a differential-testing "
        "program. Use when creating or editing .lean files or when a Lean proof or build fails."
    ),
}


def _skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: >-\n  {description}\n---\n\n{body}"


def _write_skill(root: Path, name: str, body: str, description: str) -> Path:
    path = root / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_skill(name, description, body))
    return path


def install(root: Path, *, hooks: bool = False, lean_version: str = "the pinned version") -> list[Path]:
    rules = guides.load("rules")
    bodies = {
        "rung": USAGE.format(rules=rules),
        "rung-tla": guides.load("tla"),
        "rung-property": guides.load("property"),
        "rung-lean": guides.load("lean", lean_version=lean_version),
    }
    written = [_write_skill(root, name, bodies[name], desc) for name, desc in SKILLS.items()]

    agents = root / "AGENTS.md"
    block = f"{BEGIN}\n## Verification (Rung)\n\n{rules}{END}\n"
    text = agents.read_text() if agents.exists() else ""
    if BEGIN in text and END in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        text = head + block.rstrip("\n") + tail
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    agents.write_text(text)
    written.append(agents)

    if hooks:
        written.append(install_stop_hook(root))
    return written


def install_verify_skill(root: Path, project: str, check_id: str) -> Path:
    """A project-specific skill pointing agents at the proven drive script and its feature map."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in project.lower()).strip("-") or "app"
    description = (
        f"Drive {project} the way a user does and record evidence. Use after any user-facing change "
        "(UI, API, CLI), and before saying such a change works."
    )
    body = guides.load("verify", project=project, check_id=check_id)
    return _write_skill(root, f"verify-{slug}", body, description)


def install_stop_hook(root: Path) -> Path:
    """Add the Stop hook to `.claude/settings.json`, keeping everything already there."""
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not plain JSON ({exc}); add the Stop hook by hand or remove comments") from exc
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    present = any(h.get("command") == HOOK_COMMAND for group in stop for h in group.get("hooks", []))
    if not present:
        stop.append({"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 120}]})
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


_TLA_TRIAGE = """\
`rung check {check_id}` prints a counterexample as numbered steps in plain words, with only the
variables each step changed, the raw TLC trace, and a Mermaid diagram of the path under
`.rung/evidence/{check_id}/`. Replay it against the code, one step at a time: open the function the
action's `\\* Source:` comment in `{spec}` names, and ask whether the code can really take that step
from that state (is there a guard, a lock, a transaction or an `await` the model leaves out?).

- Every step is possible in the code: it is a real bug. Fix the code, never the spec, then run
  `rung check {check_id}`.
- A step is impossible in the code: the model is wrong. Run `rung maintain {check_id}`, and tell a
  person which step the code cannot take."""

_LEAN_TRIAGE = """\
A Lean failure is either a proof the kernel rejects or a trust problem (`sorry`, a non-standard
axiom); both are problems in the model, fixed in `{project_dir}`. A failing `{check_id}-drt` check
prints the shortest input on which the model and the code disagree. Replay that input through
`{project_dir}/drt_adapter.py` and through the model by hand, one action at a time, against the
functions listed above.

- The code's answer breaks a promise above: it is a real bug. Fix the code, then run
  `rung check {check_id}-drt`.
- The model's answer is not what the code is meant to do: the model is wrong. Run
  `rung maintain {check_id}`."""


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def install_formal_skill(
    root: Path, project: str, check_id: str, kind: str, card: dict, model_files: list[str], laws: list[tuple[str, str]]
) -> Path:
    """A skill for one TLA+ or Lean check: what it promises, covers and assumes, how to run and triage it.

    `laws` are the (statement, harm) pairs of the laws the check enforces. Written when a draft is
    accepted and rewritten whenever `rung maintain` or `rung confirm` moves the confirmed commit.
    """
    checked = [f"{inv['statement']} (`{name}`)" + (f" If it breaks: {inv['harm']}" if inv.get("harm") else "")
               for name, inv in (card.get("invariants") or {}).items()]  # fmt: skip
    enforced = [text + (f" If it breaks: {harm}" if harm else "") for text, harm in laws]
    promises = "\n".join(
        part for part in (
            f"What the {'invariants' if kind == 'tla' else 'model'} state:\n{_bullets(checked, '')}" if checked else "",
            f"Laws in LAWS.md it enforces:\n{_bullets(enforced, '')}" if enforced else "",
        ) if part
    ) or "- no statements were recorded; read the model"  # fmt: skip
    scope = card.get("scope") or {}
    scope_text = "\n".join([
        "Bounds:", _bullets(scope.get("bounds", []), "none stated"),
        "", "Assumptions:", _bullets(scope.get("assumptions", []), "none stated"),
        "", "Not covered:", _bullets(scope.get("not_covered", []), "nothing stated"),
    ])  # fmt: skip
    confirmed = card.get("confirmed") or {}
    commit = confirmed.get("commit", "")
    sources = sorted(confirmed.get("files") or card.get("sources") or [])
    first_model = model_files[0] if model_files else ""
    if kind == "tla":
        what, checker, model = "a TLA+ model of the code that TLC checks over every reachable state", "TLC", "TLA+"
        triage = _TLA_TRIAGE.format(check_id=check_id, spec=first_model)
    else:
        what, checker, model = "a Lean model with kernel-checked proofs, compared with the real code", "Lean", "Lean"
        triage = _LEAN_TRIAGE.format(check_id=check_id, project_dir=str(Path(first_model).parent))
    body = guides.load(
        "formal-skill",
        check_id=check_id,
        project=project,
        what=what,
        checker=checker,
        promises=promises,
        model_files=", ".join(f"`{f}`" for f in model_files),
        source_files=", ".join(f"`{f}`" for f in sources),
        functions=", ".join(f"`{f}`" for f in card.get("functions") or []) or "not recorded",
        scope=scope_text,
        triage=triage,
        confirm_reason="reviewed the model against the changes to " + ", ".join(sources),
        confirmed=(
            f"The model was last confirmed to describe the code at commit `{commit or 'uncommitted'}`, "
            f"by {confirmed.get('by', 'unknown')} on {confirmed.get('at', 'an unknown date')}: "
            f"{confirmed.get('reason', '')}"
        ),
    )
    description = (
        f"Check and maintain the {model} model `{check_id}` of {', '.join(sources) or project}. Use when changing "
        "that code, when `rung check` reports model drift or a counterexample for it, or before saying such a "
        "change is safe."
    )
    return _write_skill(root, f"verify-{check_id}", body, description)
