"""The feature map: what a user can do in the app, approved by a person before any script drives it.

The shape follows Lauren Tan's pstack (`/create-verification-skill`): a README index with the
baseline preconditions, driving conventions and proof rules, and one file per user-facing feature
with four sections (sub-features with IDs, every user entry point, the driving recipe, gotchas).

Rung adds three things around it:

- **Approval.** A model drafts the map from the code; a person approves, drops, or marks each
  feature "not built yet" before a drive script is written. Only approved features are driven, so a
  script never chases a button the product has not shipped.
- **Coverage.** Each approved sub-feature ID must name a scenario in the drive script's
  `result.json`, or be listed as unreachable with the precondition it lacks.
- **Drift.** The map records a digest of the source files each feature comes from. When they
  change, the map is stale until it is refreshed and the changed features are approved again.

State lives in `.rung/verify/features/map.json`; the Markdown files stay plain, to be read and
reviewed like any other document.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rung import guides
from rung.analysis.survey import Inspection
from rung.core import git
from rung.drafting.common import FILES, STRINGS, files_of, machine_path_problem, safe_write, schema
from rung.drafting.providers import Provider

MAP_DIR = Path(".rung") / "verify" / "features"
STATE_FILE = "map.json"
INDEX = "README.md"
INDEX_SECTIONS = ("Baseline preconditions", "Driving conventions", "Proof and skip reporting", "Features")
FEATURE_SECTIONS = ("Sub-features", "How to get to it (user POV)", "Driving it with", "Gotchas")
STATUSES = ("proposed", "approved", "not-built", "dropped")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SUB = re.compile(r"^\s*[-*]\s+`([^`]+)`\s*(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.+)$")
SOURCES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"feature": {"type": "string"}, "sources": STRINGS},
        "required": ["feature", "sources"],
        "additionalProperties": False,
    },
}
SCHEMA = schema(files=FILES, sources=SOURCES, notes={"type": "string"})
FORMAT = """

Return `files` with `README.md` and one `<slug>.md` per feature, `sources` with one entry per feature
(`feature` is its slug; `sources` the repository-relative paths or globs of the code that implements
it: templates, routes, handlers, commands), and `notes` with anything a person should know before
approving the map (features you left out, anything that looks unfinished)."""


@dataclass
class Feature:
    slug: str
    title: str = ""
    summary: str = ""
    subfeatures: list[tuple[str, str]] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    harness: str = ""
    gotchas: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _sections(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(H1 title, the text before the first H2, [(H2 heading, body)])."""
    title, intro, sections = "", [], []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            sections.append((line[3:].strip(), []))
            current = sections[-1][1]
        elif current is not None:
            current.append(line)
        elif title:
            intro.append(line)
    return title, "\n".join(intro).strip(), [(h, "\n".join(b).strip()) for h, b in sections]


def parse_feature(slug: str, text: str) -> Feature:
    """A feature file, with every way it breaks the contract in `problems`."""
    title, summary, sections = _sections(text)
    f = Feature(slug, title, summary)
    where = f"{slug}.md"
    if not _SLUG.match(slug):
        f.problems.append(f"{where}: the file name must be lower-case kebab-case")
    if not title:
        f.problems.append(f"{where}: starts with an H1 title")
    if not summary:
        f.problems.append(f"{where}: needs one paragraph after the title describing the user-visible behaviour")
    headings = [h for h, _ in sections]
    expected = list(FEATURE_SECTIONS)
    if len(headings) != 4 or any(not h.startswith(e) for h, e in zip(headings, expected, strict=False)):
        f.problems.append(f"{where}: needs exactly these H2 sections in order: {', '.join(expected)} <harness>; "
                          f"found {', '.join(headings) or 'none'}")  # fmt: skip
    body = dict(sections)
    for line in body.get("Sub-features", "").splitlines():
        if m := _SUB.match(line):
            f.subfeatures.append((m.group(1).strip(), m.group(2).strip()))
        elif _BULLET.match(line):
            f.problems.append(f"{where}: sub-feature bullet without a `backticked-id`: {line.strip()}")
    if not f.subfeatures:
        f.problems.append(f"{where}: lists no sub-features (`- \\`id\\` what it does`)")
    for sub_id, _ in f.subfeatures:
        if not _SLUG.match(sub_id):
            f.problems.append(f"{where}: sub-feature ID {sub_id!r} must be kebab-case")
    f.entry_points = [m.group(1).strip() for line in body.get("How to get to it (user POV)", "").splitlines()
                      if (m := _BULLET.match(line))]  # fmt: skip
    if not f.entry_points:
        f.problems.append(f"{where}: `How to get to it (user POV)` lists no entry point")
    driving = next(((h, b) for h, b in sections if h.startswith("Driving it with")), ("", ""))
    f.harness = driving[0].removeprefix("Driving it with").strip()
    if driving[0] and "Preconditions:" not in driving[1]:
        f.problems.append(f"{where}: `{driving[0]}` must start with `Preconditions:`")
    f.gotchas = [m.group(1).strip() for line in body.get("Gotchas", "").splitlines() if (m := _BULLET.match(line))]
    return f


def check_map(files: dict[str, str]) -> tuple[list[Feature], list[str]]:
    """Parse a drafted map; every problem is phrased as a repair request for the model."""
    problems: list[str] = []
    index = files.get(INDEX, "")
    if not index:
        problems.append("no README.md index")
    else:
        _, _, sections = _sections(index)
        headings = [h for h, _ in sections]
        if headings != list(INDEX_SECTIONS):
            problems.append(f"README.md needs exactly these H2 sections in order: {', '.join(INDEX_SECTIONS)}; "
                            f"found {', '.join(headings) or 'none'}")  # fmt: skip
    features = [parse_feature(Path(name).stem, text) for name, text in sorted(files.items())
                if name != INDEX and name.endswith(".md")]  # fmt: skip
    if not features:
        problems.append("no feature files; write one `<slug>.md` per user-facing feature")
    for name in files:
        if name != INDEX and (not name.endswith(".md") or "/" in name):
            problems.append(f"unexpected file {name!r}: only README.md and <slug>.md in one directory")
    for f in features:
        problems += f.problems
        if index and f"({f.slug}.md)" not in index:
            problems.append(f"README.md `Features` does not link {f.slug}.md")
    seen: dict[str, str] = {}
    for f in features:
        for sub_id, _ in f.subfeatures:
            if sub_id in seen:
                problems.append(f"sub-feature ID {sub_id!r} is used by both {seen[sub_id]} and {f.slug}")
            seen[sub_id] = f.slug
    return features, problems


# --- state: approval and drift ------------------------------------------------------------------------


def _digest(root: Path, patterns: list[str]) -> dict[str, str]:
    files = git.list_files(root)
    out = {}
    for rel in sorted({f for f in files for p in patterns if git.glob_match(p, f)}):
        path = root / rel
        if path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _feature_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class MapState:
    features: dict[str, dict] = field(default_factory=dict)  # slug -> {status, sources, digests, text, by, at}
    drafted_by: str = ""
    notes: str = ""

    def save(self, root: Path) -> Path:
        path = root / MAP_DIR / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
        return path

    def with_status(self, *statuses: str) -> list[str]:
        return [slug for slug, f in sorted(self.features.items()) if f.get("status") in statuses]


def load_state(root: Path) -> MapState | None:
    path = root / MAP_DIR / STATE_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return MapState(data.get("features", {}), data.get("drafted_by", ""), data.get("notes", ""))


def load_features(root: Path) -> dict[str, Feature]:
    base = root / MAP_DIR
    if not base.is_dir():
        return {}
    return {p.stem: parse_feature(p.stem, p.read_text()) for p in sorted(base.glob("*.md")) if p.name != INDEX}


def drifted(root: Path, state: MapState) -> dict[str, list[str]]:
    """Approved features whose source files, or whose own file, changed since they were approved."""
    out: dict[str, list[str]] = {}
    for slug, f in state.features.items():
        if f.get("status") != "approved":
            continue
        now = _digest(root, f.get("sources", []))
        then = f.get("digests", {})
        changed = sorted(p for p in set(now) | set(then) if now.get(p) != then.get(p))
        path = root / MAP_DIR / f"{slug}.md"
        if not path.exists() or _feature_digest(path.read_text()) != f.get("text"):
            changed.insert(0, (MAP_DIR / f"{slug}.md").as_posix())
        if changed:
            out[slug] = changed
    return out


def set_status(root: Path, state: MapState, slug: str, status: str, by: str = "") -> None:
    """Record a person's decision on a feature; approval pins the digests drift is measured against."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    entry = state.features.setdefault(slug, {"sources": []})
    entry["status"] = status
    entry["by"] = by
    entry["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    if status == "approved":
        entry["digests"] = _digest(root, entry.get("sources", []))
        entry["text"] = _feature_digest((root / MAP_DIR / f"{slug}.md").read_text())


# --- drafting ---------------------------------------------------------------------------------------------


@dataclass
class MapDraft:
    ok: bool
    message: str
    features: list[Feature] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    rounds: int = 0
    notes: str = ""
    sources: dict[str, list[str]] = field(default_factory=dict)


def _existing_block(root: Path) -> str:
    base = root / MAP_DIR
    if not base.is_dir():
        return ""
    state = load_state(root) or MapState()
    parts = ["The current feature map follows, with each feature's status. Keep features whose code did not "
             "change exactly as they are; update the ones the code changed; add features the code gained; keep "
             "`not-built` and `dropped` features out unless the code now implements them. Keep the slugs and "
             "sub-feature IDs of features that still exist."]  # fmt: skip
    for p in sorted(base.glob("*.md")):
        status = state.features.get(p.stem, {}).get("status", "")
        parts += ["", f"--- {p.name}{f' (status: {status})' if status else ''} ---", p.read_text()]
    return "\n".join(parts)


def draft(provider: Provider, root: Path, info: Inspection, *, context: str, intent: str = "",
          max_rounds: int = 3) -> MapDraft:  # fmt: skip
    """Draft (or refresh) the feature map from the code, repairing it until it meets the contract.

    Nothing is approved here: approval is a person's decision, made with `rung map`.
    """
    surface = ", ".join(s for s in info.surfaces if s != "library") or "the app"
    prompt = (
        f"Write the feature map for this repository's {surface}, following the guide.\n"
        + (f"Focus on: {intent}\n" if intent else "")
        + "\n"
        + context
    )
    if existing := _existing_block(root):
        prompt += "\n\n" + existing
    system = guides.load("feature-map") + FORMAT
    files: dict[str, str] = {}
    features: list[Feature] = []
    for round_no in range(1, max_rounds + 1):
        reply = provider.complete_json(prompt, schema=SCHEMA, system=system, cwd=root)
        files = {Path(k).name if k.startswith(MAP_DIR.as_posix()) else k: v for k, v in files_of(reply).items()}
        features, problems = check_map(files)
        sources = {s["feature"]: [p for p in s["sources"] if p.strip()] for s in reply.get("sources", [])}
        problems += [f"`sources` has no entry for {f.slug}" for f in features if not sources.get(f.slug)]
        if repair := machine_path_problem(files):
            problems.append(repair.strip())
        if problems:
            prompt += (
                "\n\nThe map does not meet the contract yet:\n- " + "\n- ".join(problems[:20])
                + "\nFix these and return the same fields, with every file in full."
            )  # fmt: skip
            continue
        base = root / MAP_DIR
        base.mkdir(parents=True, exist_ok=True)
        keep = {f.slug for f in features}
        for old in base.glob("*.md"):
            if old.name != INDEX and old.stem not in keep:
                old.unlink()  # a feature the refreshed map no longer has
        written = safe_write(base, files)
        state = load_state(root) or MapState()
        for f in features:
            entry = state.features.setdefault(f.slug, {"status": "proposed"})
            entry["sources"] = sources[f.slug]
            text = _feature_digest((base / f"{f.slug}.md").read_text())
            if entry.get("status") == "approved" and entry.get("text") != text:
                entry["status"] = "proposed"  # changed since a person approved it: they approve it again
        # A person's "not built yet" or "drop" outlives a map that leaves the feature out.
        state.features = {k: v for k, v in state.features.items()
                          if k in keep or v.get("status") in ("not-built", "dropped")}  # fmt: skip
        state.notes = str(reply.get("notes", "")).strip()
        state.save(root)
        return MapDraft(True, f"{len(features)} feature(s) mapped", features, written, round_no, state.notes, sources)
    return MapDraft(False, f"no map met the contract after {max_rounds} rounds", features, [], max_rounds)


def approved_block(root: Path) -> tuple[str, list[str], list[str]]:
    """The approved features for the drive-script prompt, their sub-feature IDs, and their source globs."""
    state = load_state(root)
    if state is None:
        return "", [], []
    features = load_features(root)
    approved = [s for s in state.with_status("approved") if s in features]
    if not approved:
        return "", [], []
    parts = ["The feature map a person approved follows. Drive exactly these features with the recipes they give. "
             "Name each scenario after the sub-feature ID it proves, or list the IDs it proves under `covers`. Every "
             "sub-feature ID below must be covered by a scenario, or appear in `result.json` under `unreachable` "
             "with the precondition the run lacked."]  # fmt: skip
    index = root / MAP_DIR / INDEX
    if index.exists():
        parts += ["", f"--- {INDEX} ---", index.read_text()]
    ids: list[str] = []
    sources: list[str] = []
    for slug in approved:
        parts += ["", f"--- {slug}.md ---", (root / MAP_DIR / f"{slug}.md").read_text()]
        ids += [i for i, _ in features[slug].subfeatures]
        sources += state.features[slug].get("sources", [])
    if skipped := state.with_status("not-built"):
        parts += ["", "These features are not built yet; do not drive them: " + ", ".join(skipped)]
    return "\n".join(parts), ids, list(dict.fromkeys(sources))


def uncovered(ids: list[str], result: dict | None) -> list[str]:
    """Approved sub-feature IDs no scenario proves and the run did not report as unreachable."""
    if not ids or result is None:
        return list(ids)
    covered: set[str] = set()
    for s in result.get("scenarios") or []:
        if isinstance(s, dict):
            covered.add(str(s.get("name", "")))
            covered.update(str(c) for c in s.get("covers") or [] if isinstance(c, str))
    unreachable = result.get("unreachable")
    if isinstance(unreachable, dict):
        covered.update(k for k, v in unreachable.items() if isinstance(v, str) and v.strip())
    return [i for i in ids if i not in covered]
