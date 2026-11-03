"""`rung maintain`: bring a formal model back in line with code that changed since it was confirmed.

The model is shown the spec or model files, the diff of the covered sources (and any SQL definitions
the draft added) since the confirmed commit, the commit messages, and the sources as they are now. It
classifies each change:

- `no-effect`: nothing the model describes changed;
- `model-must-change`: the code does something new, and the model must say so;
- `promise-changed`: the guarantee itself changed on purpose, with evidence quoted from a doc comment,
  a test, a changelog entry or a commit message;
- `promise-may-be-broken`: the change may break the guarantee; the model is updated to describe the code
  faithfully and the checker decides.

It updates the model from the code, never from a checker result, which it never sees. Rung rejects a
reply that removes or rewrites an invariant, property or theorem, or adds a state constraint, unless a
change is classified `promise-changed` with cited evidence. Then Rung reruns the checker and, for TLA+,
the fail-ability check. One outcome results:

- `in-sync`: the model still describes the code and checks;
- `updated`: the model was changed to describe the code and checks;
- `bug`: a counterexample (or a divergence from the code) on a model that describes the current code;
- `blocked`: no acceptable model within the rounds; the model files are left as they were.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from rung import guides
from rung.checks import registry
from rung.checks.base import Context
from rung.core import git, modelcard
from rung.core.config import Check, Config, ConfigError
from rung.drafting.common import (
    ACTIONS,
    FILES,
    INVARIANTS,
    LABELS_FORMAT,
    SCOPE,
    SCOPE_FORMAT,
    STRINGS,
    files_of,
    machine_path_problem,
    safe_write,
    schema,
    source_block,
)
from rung.drafting.providers import Provider
from rung.engines import lean, tla, tla_mutate, tla_render

CLASSES = ("no-effect", "model-must-change", "promise-changed", "promise-may-be-broken")
OUTCOMES = ("in-sync", "updated", "bug", "blocked")
ATTEMPTS_DIR = Path(".rung") / "cache" / "maintain"
MAX_DIFF = 60_000
_STR = {"type": "string"}
CHANGES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"change": _STR, "classification": _STR, "evidence": _STR},
        "required": ["change", "classification", "evidence"],
        "additionalProperties": False,
    },
}
FORMAT = """

Return:
- `changes`: one entry per change in the diff: `change` (what changed, with `file:line`), `classification`
  (exactly one of `no-effect`, `model-must-change`, `promise-changed`, `promise-may-be-broken`) and
  `evidence` (for `promise-changed`, the quoted text and where it is; otherwise empty or a short reason).
- `files`: only the model files that change, each with its complete new contents; `[]` when none must.
- `notes`: anything the developer should know."""
WEAKENED = (
    "\n\nYou removed or rewrote {names} without citing evidence that the promise changed on purpose. Keep every "
    "invariant, property and theorem as it was and change the model so it describes the code. Only a change "
    "classified `promise-changed`, with quoted evidence (a doc comment, a test, a changelog entry or a commit "
    "message), may change a promise. Return the same fields."
)
TLA_SCHEMA = schema(
    changes=CHANGES, files=FILES, actions=ACTIONS, invariants=INVARIANTS, scope=SCOPE, functions=STRINGS, notes=_STR
)
LEAN_SCHEMA = schema(changes=CHANGES, files=FILES, theorems=STRINGS, scope=SCOPE, functions=STRINGS, notes=_STR)


@dataclass
class Verified:
    status: str  # pass | fail | error
    problem: str = ""  # for error: what to send back to the model
    story: list[str] = field(default_factory=list)  # for fail: the counterexample or divergence
    failability: dict[str, Any] = field(default_factory=dict)


@dataclass
class MaintainResult:
    outcome: str  # one of OUTCOMES
    message: str
    changes: list[dict[str, str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # model files changed, relative to the project root
    rounds: int = 0
    reply: dict[str, Any] = field(default_factory=dict)
    failability: dict[str, Any] = field(default_factory=dict)
    story: list[str] = field(default_factory=list)
    attempt: str = ""  # where a blocked attempt was kept

    @property
    def updated(self) -> bool:
        return bool(self.files)

    @property
    def confirms(self) -> bool:
        """Whether the model now describes the code: move the confirmed commit forward."""
        return self.outcome in ("in-sync", "updated") or (self.outcome == "bug" and self.updated)


def _normal(text: str) -> str:
    return " ".join(text.split())


def _conjuncts(text: str) -> set[str]:
    return {_normal(part) for part in text.split("/\\") if part.strip()}


def weakened(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Promises an update removed or loosened: a missing or rewritten one, or a new narrowing setting.

    Adding a promise, or adding conjuncts to one (a type invariant that gains a new variable), is not a
    weakening; dropping or rewriting any of its conjuncts is. `TypeOK` says nothing about the promise.
    """
    loosened = {name for name, text in before.items()
                if name != "TypeOK" and not _conjuncts(text) <= _conjuncts(after.get(name, ""))}  # fmt: skip
    return sorted(loosened | {name for name in after if name.startswith("setting:") and name not in before})


class Model:
    """The files of one formal check, how to verify them, and the promises they make."""

    kind = ""
    name = ""
    schema: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: Config, check: Check, card: dict[str, Any]) -> None:
        self.root, self.check, self.card = config.root, check, card
        self.config = config
        self.directory = self.root

    def files(self) -> list[Path]:
        return []

    def allowed(self, name: str) -> bool:
        return False

    def promises(self) -> dict[str, str]:
        return {}

    def verify(self, card: dict[str, Any]) -> Verified:
        raise NotImplementedError

    def snapshot(self) -> dict[Path, bytes]:
        return {f: f.read_bytes() for f in self.files()}

    def restore(self, saved: dict[Path, bytes]) -> None:
        for f in self.files():
            if f not in saved:
                f.unlink()
        for f, body in saved.items():
            f.write_bytes(body)


class TLAModel(Model):
    kind, name, schema = "tla", "TLA+ spec", TLA_SCHEMA

    def __init__(self, config: Config, check: Check, card: dict[str, Any]) -> None:
        super().__init__(config, check, card)
        self.spec = self.root / check.require("spec")
        cfg = check.get("config")
        if not cfg:
            raise ConfigError(f"check {check.id!r}: a TLA+ check needs a `config` (.cfg)")
        self.cfg = self.root / cfg
        self.directory = self.spec.parent

    def files(self) -> list[Path]:
        return [f.resolve() for f in (self.spec, self.cfg) if f.exists()]

    def allowed(self, name: str) -> bool:
        return (self.directory / name).resolve() in (self.spec.resolve(), self.cfg.resolve())

    def promises(self) -> dict[str, str]:
        declared = tla.declared_properties(self.cfg)
        text = self.spec.read_text() if self.spec.exists() else ""
        out = {f"setting: {n}": n for n in declared["narrowing"]}
        for name in declared["invariants"] + declared["properties"]:
            m = re.search(rf"^{re.escape(name)}\s*==(.*?)(?=^\S|\Z)", text, re.M | re.S)
            out[name] = _normal(m.group(1)) if m else "(not defined)"
        return out

    def verify(self, card: dict[str, Any]) -> Verified:
        result = tla.run(self.spec, self.cfg, timeout=self.check.get_int("timeout", 600))
        if result.verdict == "error":
            return Verified("error", (result.reason + "\n" + result.output[-2000:]).strip())
        if result.verdict == "fail":
            story = tla_render.story(result.trace, result.violated, card) or [result.reason]
            return Verified("fail", story=[f"TLC: {result.reason}", *story])
        if result.distinct_states <= 1:
            return Verified("error", "TLC explored at most one state, so the invariants are vacuous.")
        failability = tla_mutate.run(self.spec, self.cfg)
        if not failability.ok:
            return Verified("error", tla_mutate.repair_request(failability).strip())
        return Verified("pass", failability=failability.as_dict())


class LeanModel(Model):
    kind, name, schema = "lean", "Lean model", LEAN_SCHEMA

    def __init__(self, config: Config, check: Check, card: dict[str, Any]) -> None:
        super().__init__(config, check, card)
        self.directory = self.root / check.require("project")
        self.theorems = check.get_list("theorems")

    def files(self) -> list[Path]:
        base = self.directory.resolve()
        return sorted(
            f for f in base.rglob("*")
            if f.is_file() and ".lake" not in f.relative_to(base).parts and self.allowed(f.name)
        )  # fmt: skip

    def allowed(self, name: str) -> bool:
        return name.endswith(".lean") or name in ("lakefile.toml", "drt_adapter.py")

    def promises(self) -> dict[str, str]:
        return {name: _normal(statement) for name, statement in lean.theorem_statements(self.directory).items()}

    def verify(self, card: dict[str, Any]) -> Verified:
        result = lean.run(self.directory, self.theorems, timeout=self.check.get_int("timeout", 1800))
        if result.verdict != "pass":
            return Verified("error", "\n".join(result.findings) or result.output[-3000:])
        drt_id = f"{self.check.id}-drt"
        if any(c.id == drt_id for c in self.config.checks):
            drt_check = self.config.check(drt_id)
            outcome = registry.get("drt").run(Context(root=self.root), drt_check)
            if outcome.verdict == "error":
                return Verified("error", f"the differential test could not run: {outcome.summary}")
            if outcome.verdict == "fail":
                return Verified("fail", story=[outcome.summary, *outcome.lines])
        return Verified("pass")


def model_for(config: Config, check: Check, card: dict[str, Any]) -> Model:
    if check.kind == "tla":
        return TLAModel(config, check, card)
    if check.kind == "lean":
        return LeanModel(config, check, card)
    raise ConfigError(f"`rung maintain` works on tla and lean checks; {check.id!r} is a {check.kind} check")


def _said(card: dict[str, Any]) -> str:
    said = {k: card.get(k) for k in ("actions", "invariants", "scope", "functions") if card.get(k)}
    return json.dumps(said, indent=1) if said else "(nothing recorded)"


def build_prompt(root: Path, check: Check, card: dict[str, Any], model: Model, changed: list[str]) -> str:
    confirmed = card.get("confirmed") or {}
    commit = confirmed.get("commit", "")
    parts = [
        f"The code that the {model.name} for check `{check.id}` describes has changed since the model was "
        f"confirmed to describe it at {commit[:7] or 'an uncommitted state'} "
        f"({confirmed.get('by', '?')}: {confirmed.get('reason', '')}). Bring the model in line with the code as it "
        "is now. Classify every change first.",
        "",
        "Changed files: " + ", ".join(changed),
    ]
    for f in model.files():
        parts += [
            "",
            f"--- model file {f.relative_to(model.directory.resolve()).as_posix()} ---",
            f.read_text().rstrip(),
        ]
    parts += ["", "--- what the model says, in plain words ---", _said(card)]
    if commit and git.is_repo(root):
        log = git.log_since(root, commit, changed)
        parts += ["", f"--- commits since {commit[:7]} that touch the changed files ---", log or "(none: uncommitted)"]
        diff = git.diff_since(root, commit, changed)
        if len(diff) > MAX_DIFF:
            diff = diff[:MAX_DIFF] + "\n... (diff truncated)"
        parts += ["", f"--- diff since {commit[:7]} ---", diff or "(no diff)"]
    sources = [root / f for f in git.expand(root, card.get("sources", [])) if (root / f).is_file()]
    if sources:
        parts += ["", "--- the covered sources as they are now ---", source_block(root, sources)]
    return "\n".join(parts)


def _keep_attempt(root: Path, check_id: str, model: Model) -> str:
    target = root / ATTEMPTS_DIR / check_id
    shutil.rmtree(target, ignore_errors=True)
    for f in model.files():
        dest = target / f.relative_to(model.directory.resolve())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
    return target.relative_to(root).as_posix()


def _review(reply: dict[str, Any], model: Model, produced: dict[str, str]) -> str:
    """A repair request for a reply that cannot be applied, or "" when it can."""
    changes = reply["changes"]
    if not changes:
        return "\n\nYour reply classified no changes. Classify every change in the diff."
    bad = sorted({c["classification"] for c in changes if c["classification"] not in CLASSES})
    if bad:
        return f"\n\nUnknown classification(s) {', '.join(bad)}; use exactly one of {', '.join(CLASSES)}."
    if outside := [n for n in produced if not model.allowed(n)]:
        return f"\n\nYou may only change the model's own files; not {', '.join(outside)}. Return the same fields."
    if any(c["classification"] == "model-must-change" for c in changes) and not produced:
        return "\n\nYou classified a change as `model-must-change` but returned no files. Return the updated model."
    return machine_path_problem(produced)


def maintain(
    provider: Provider, config: Config, check: Check, card: dict[str, Any], *, max_rounds: int = 4
) -> MaintainResult:
    """Update `check`'s model to describe the code as it is now, then let the checker decide."""
    root = config.root
    model = model_for(config, check, card)
    changed = modelcard.changed_files(root, card)
    if not changed:
        verified = model.verify(card)
        outcome = {"pass": "in-sync", "fail": "bug"}.get(verified.status, "blocked")
        message = {"in-sync": "the code has not changed since the model was confirmed, and it checks",
                   "bug": "the code has not changed since the model was confirmed; the checker finds a bug",
                   "blocked": f"the model no longer checks: {verified.problem[:500]}"}[outcome]  # fmt: skip
        return MaintainResult(outcome, message, failability=verified.failability, story=verified.story)

    system = (
        guides.load(model.kind, lean_version=lean.toolchain().split(":")[-1] if model.kind == "lean" else "")
        + "\n\n"
        + guides.load("maintain")
        + FORMAT
        + (LABELS_FORMAT if model.kind == "tla" else "")
        + SCOPE_FORMAT
    )
    prompt = build_prompt(root, check, card, model, changed)
    original = model.snapshot()
    promised = model.promises()
    problem = ""
    changes: list[dict[str, str]] = []
    try:
        for round_no in range(1, max_rounds + 1):
            reply = provider.complete_json(prompt, schema=model.schema, system=system, cwd=root)
            changes = reply["changes"]
            produced = files_of(reply)
            if problem := _review(reply, model, produced):
                prompt += problem
                continue
            model.restore(original)
            written = safe_write(model.directory, produced)
            loosened = weakened(promised, model.promises())
            cited = [c for c in reply["changes"] if c["classification"] == "promise-changed" and c["evidence"].strip()]
            if loosened and not cited:
                problem = WEAKENED.format(names=", ".join(loosened))
                prompt += problem
                continue
            preview = dict(card)
            modelcard.describe(preview, reply)
            verified = model.verify(preview)
            if verified.status == "error":
                problem = (
                    f"\n\nThe checker rejected the updated model:\n{verified.problem}\nFix it; return the same fields."
                )
                prompt += problem
                continue
            files = sorted(
                f.relative_to(root).as_posix() for f in written if original.get(f.resolve()) != f.read_bytes()
            )  # fmt: skip
            result = MaintainResult(
                "bug" if verified.status == "fail" else ("updated" if files else "in-sync"),
                "",
                reply["changes"],
                files,
                round_no,
                reply,
                verified.failability,
                verified.story,
            )
            result.message = {
                "bug": "the model describes the current code, and the checker finds a bug in it",
                "updated": f"the model was updated to describe the code ({', '.join(files)}) and it checks",
                "in-sync": "the changes do not affect what the model describes, and it checks",
            }[result.outcome]
            if loosened:
                result.message += f"; promises changed on purpose: {', '.join(loosened)}"
            return result
    except BaseException:
        model.restore(original)  # a provider or checker failure never leaves a half-updated model
        raise
    attempt = _keep_attempt(root, check.id, model)
    model.restore(original)
    return MaintainResult("blocked", f"no acceptable model after {max_rounds} rounds: {problem.strip()[:600]}",
                          changes, rounds=max_rounds, attempt=attempt)  # fmt: skip
