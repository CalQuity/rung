"""Teach coding agents to work inside the ladder.

`rung skill` writes a Claude Code skill and an AGENTS.md section (read by Codex,
Cursor and others). The rules exist because an agent's easiest way to make a
check pass is to weaken the check; each rule closes one of those shortcuts.
"""

from __future__ import annotations

from pathlib import Path

BEGIN, END = "<!-- rung:begin -->", "<!-- rung:end -->"

RULES = """\
- Before saying a change is done, run `rung check` and read the result. "The build
  passed" is not evidence; a fresh verdict in `.rung/ledger.jsonl` is.
- A TLC counterexample is either a bug in the code or a wrong spec. Fix the code.
  Only change a spec or an invariant when a person has agreed the spec was wrong.
- Never weaken a law, an invariant, a theorem or a property to make it pass.
  Laws in LAWS.md that are `approved` are not yours to edit.
- Never use `sorry`, `admit`, `axiom`, `native_decide`, `unsafe`, `implemented_by`
  or `extern` in Lean. Rung rejects them, and the rejection is correct.
- When behaviour changes, update the executable model (Lean) or the spec (TLA+)
  in the same change, then re-run the differential test against it.
- New invariants you discover go into LAWS.md as `status: proposed`, in one plain
  sentence a person can approve, with the checks that enforce them.
- For user-facing changes, drive the real app and keep the evidence (transcript,
  screenshot, video) under `.rung/evidence/`; cleanup must never delete it.
"""

SKILL = f"""---
name: rung
description: >-
  Verify changes with Rung's ladder (architecture rules, runtime evidence,
  properties, TLA+, Lean, evidence ledger). Use before declaring any change done,
  when a check fails or goes stale, or when adding a law, spec or proof.
---

# Working with Rung

This repository proves its behaviour instead of asserting it. `rung status` shows
which verdicts are fresh, stale or failing; `rung laws` shows which laws hold.

## Rules

{RULES}
## Commands

- `rung check` runs every check and records verdicts; `rung check <id>` runs one.
- `rung status` lists checks; `stale` means covered code or a spec changed since the
  last green run.
- `rung laws` lists laws and whether they currently hold.
- `rung draft tla <files>` / `rung draft lean <files>` draft a spec or model from code;
  the checker, not you, decides whether it stands.
"""


def install(root: Path) -> list[Path]:
    skill_path = root / ".claude" / "skills" / "rung" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(SKILL)

    agents = root / "AGENTS.md"
    block = (
        f"{BEGIN}\n## Verification (Rung)\n\n{RULES}\n"
        f"See `.claude/skills/rung/SKILL.md` for commands.\n{END}\n"
    )
    text = agents.read_text() if agents.exists() else ""
    if BEGIN in text and END in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        text = head + block.rstrip("\n") + tail
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    agents.write_text(text)
    return [skill_path, agents]
