"""Install CLI workflows, shared guidance, and optional agent Stop hooks.

Skills are installed for Claude Code under `.claude/skills/`; the AGENTS.md section is read by
Codex, Cursor and other agents. Codex also discovers the skills under `.agents/skills/`.
"""

from __future__ import annotations

import json
from pathlib import Path

from rung import guides
from rung.agents.hooks import CODEX_HOOK_COMMAND, HOOK_COMMAND
from rung.core.config import ConfigError, managed_path, write_atomic

BEGIN, END = "<!-- rung:begin -->", "<!-- rung:end -->"

USAGE = """\
# Working with Rung

This repository proves its behaviour instead of asserting it. Rung is a CLI; this skill only
says how to use it.

## Rules

{rules}
## Run the CLI

Use your terminal tool. Check `rung --version` first. From another directory, use
`rung -C <project>`; after setup, commands can find the kit from a project subdirectory.

1. When asked to set up a kit, start with `rung init --dry-run --json` and inspect its
   proposed checks. Apply the deterministic setup with `rung init --no-drafts --json`.
2. Before editing, use `rung plan --path <project-relative-path> --json`. For an existing
   branch, use `rung plan --changed --base <target> --json`. Read the matching checks
   and any scope gaps. Use `rung doctor --strict --json` to diagnose missing executables.
3. Run `rung check --changed --base <target> --json`. Read the exit code and each result.
   If a runtime check gives a `reproduce` command, run it to isolate the failure. A replay
   is diagnostic. Run the full check again to record a verdict.
4. For a bug fix, use `rung prove '<test command>' --base <target>`. Pass `--expect`
   to name the expected failure when the test runner reports only a whole-suite result.
5. Use `rung report --json` to report fresh checks, remaining gaps, and evidence. When
   asked to verify merge readiness, run `rung gate --base <target> --json`. Send
   protected policy changes, law approvals and waivers to a person for review.
6. When a check exposes a reproduced defect, use the rung-issue skill to prepare an actionable
   GitHub issue. Follow the user's publishing instructions; checks never publish on their own.
7. When testing failed writes, timeouts or retries, use rung-failure-path to check
   the operation's error response and state after failure with isolated fixtures.
"""

SKILLS = {
    "rung-failure-path": (
        "Use when testing failed writes, timeouts, retries, interrupted operations, "
        "or inconsistent state after an error in a state-changing operation."
    ),
    "rung-issue": (
        "Use when a Rung check exposes a reproduced defect that needs a GitHub issue, "
        "a failing scenario needs to be documented for maintainers, or a finding needs "
        "triage before reporting it to the affected repository."
    ),
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


def _write_skill(root: Path, name: str, body: str, description: str, *, directory: str = ".claude/skills") -> Path:
    path = managed_path(root, Path(directory) / name / "SKILL.md")
    write_atomic(path, _skill(name, description, body))
    return path


SKILL_DIRECTORIES = (".claude/skills", ".agents/skills")


def prepare_install(root: Path, *, hooks: bool) -> tuple[Path, tuple[Path, str] | None]:
    agents = managed_path(root, "AGENTS.md")
    for directory in SKILL_DIRECTORIES:
        for name in SKILLS:
            managed_path(root, Path(directory) / name / "SKILL.md")
    return agents, _stop_hook_settings(root) if hooks else None


def install(
    root: Path, *, hooks: bool = False, codex_hooks: bool = False, lean_version: str = "the pinned version",
) -> list[Path]:  # fmt: skip
    agents, hook = prepare_install(root, hooks=hooks)
    codex_hook = _stop_hook_settings(root, agent="codex") if codex_hooks else None
    rules = guides.load("rules")
    bodies = {
        "rung": USAGE.format(rules=rules),
        "rung-failure-path": guides.load("failure-path"),
        "rung-issue": guides.load("issue"),
        "rung-tla": guides.load("tla"),
        "rung-property": guides.load("property"),
        "rung-lean": guides.load("lean", lean_version=lean_version),
    }
    written = [
        _write_skill(root, name, bodies[name], desc, directory=directory)
        for directory in SKILL_DIRECTORIES
        for name, desc in SKILLS.items()
    ]

    block = f"{BEGIN}\n## Verification (Rung)\n\n{rules}{END}\n"
    text = agents.read_text() if agents.exists() else ""
    if BEGIN in text and END in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        text = head + block.rstrip("\n") + tail
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    write_atomic(agents, text)
    written.append(agents)

    for settings_file in (hook, codex_hook):
        if settings_file:
            path, settings = settings_file
            write_atomic(path, settings)
            written.append(path)
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


def _stop_hook_settings(root: Path, *, agent: str = "claude") -> tuple[Path, str]:
    if agent == "codex":
        path, command, label = managed_path(root, ".codex/hooks.json"), CODEX_HOOK_COMMAND, "Codex"
    else:
        path, command, label = managed_path(root, ".claude/settings.json"), HOOK_COMMAND, "Claude Code"
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
        stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
        present = any(h.get("command") == command for group in stop for h in group.get("hooks", []))
        if not present:
            stop.append({"hooks": [{"type": "command", "command": command, "timeout": 120}]})
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise ConfigError(f"{path} needs a JSON object with {label} hook settings: {exc}") from exc
    return path, json.dumps(settings, indent=2) + "\n"


def install_stop_hook(root: Path) -> Path:
    """Add the Stop hook to `.claude/settings.json`, keeping everything already there."""
    path, settings = _stop_hook_settings(root)
    write_atomic(path, settings)
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
