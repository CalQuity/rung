"""Deps checker: every newly added dependency must exist and must not be brand new.

Coding agents sometimes add packages that do not exist; attackers register those
names (slopsquatting) or publish look-alikes shortly before they are used. This
check reads the dependency manifests, finds packages that are new relative to
the merge base (or to a recorded baseline), and asks the public registry about
each one. A package that is missing, or younger than `min_age_days`, blocks
until a person adds it to `allow`.
"""

from __future__ import annotations

import json
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rung.checks.base import Checker, Context, Outcome
from rung.core import git
from rung.core.config import Check
from rung.core.errors import RungError

MANIFESTS = ["**/requirements*.txt", "**/pyproject.toml", "**/package.json"]
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Package:
    ecosystem: str  # pypi | npm
    name: str


def _normalize_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pep508_names(specs: list[str]) -> set[str]:
    names = set()
    for spec in specs:
        if m := _REQ_NAME.match(spec):
            names.add(_normalize_pypi(m.group(1)))
    return names


def parse_manifest(path: str, text: str) -> set[Package]:
    """Direct dependencies declared in one manifest file."""
    name = Path(path).name
    if name.startswith("requirements") and name.endswith(".txt"):
        lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
        specs = [ln for ln in lines if ln and not ln.startswith(("-", "git+", "http:", "https:", "."))]
        return {Package("pypi", n) for n in _pep508_names(specs)}
    if name == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        specs = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            specs += group
        for group in data.get("dependency-groups", {}).values():
            specs += [s for s in group if isinstance(s, str)]
        poetry = data.get("tool", {}).get("poetry", {})
        specs += [k for k in poetry.get("dependencies", {}) if k.lower() != "python"]
        own = _normalize_pypi(project.get("name", "")) if project.get("name") else None
        return {Package("pypi", n) for n in _pep508_names(specs) if n != own}
    if name == "package.json":
        data = json.loads(text)
        names: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for dep, version in (data.get(key) or {}).items():
                if not str(version).startswith(("file:", "link:", "workspace:", "git", "http")):
                    names.add(dep)
        return {Package("npm", n) for n in names}
    return set()


def fetch_json(url: str, timeout: int = 20) -> dict | None:
    """GET a registry document; None means the package does not exist."""
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "rung"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RungError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RungError(f"cannot reach {url}: {exc.reason}") from exc


def first_published(pkg: Package) -> datetime | None:
    """When the package first appeared on its registry; None if it does not exist."""
    if pkg.ecosystem == "pypi":
        doc = fetch_json(f"https://pypi.org/pypi/{urllib.parse.quote(pkg.name)}/json")
        if doc is None:
            return None
        times = [
            f["upload_time_iso_8601"]
            for files in (doc.get("releases") or {}).values()
            for f in files
            if f.get("upload_time_iso_8601")
        ]
        return min((datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times), default=datetime.now(UTC))
    doc = fetch_json(f"https://registry.npmjs.org/{urllib.parse.quote(pkg.name, safe='@')}")
    if doc is None:
        return None
    created = (doc.get("time") or {}).get("created")
    return datetime.fromisoformat(created.replace("Z", "+00:00")) if created else datetime.now(UTC)


def _manifest_files(root: Path) -> list[str]:
    return [f for f in git.list_files(root) if any(git.glob_match(p, f) for p in MANIFESTS)]


def declared(root: Path) -> set[Package]:
    out: set[Package] = set()
    for rel in _manifest_files(root):
        out |= parse_manifest(rel, (root / rel).read_text(errors="ignore"))
    return out


def declared_at(root: Path, commit: str) -> set[Package]:
    out: set[Package] = set()
    for rel in _manifest_files(root):
        try:
            text = git.show(root, commit, rel)
        except git.GitError:
            continue  # the manifest did not exist at the base
        out |= parse_manifest(rel, text)
    return out


class DepsChecker(Checker):
    kind = "deps"
    summary = "New dependencies must exist on their registry and not be brand new"

    def default_covers(self, check: Check) -> list[str]:
        return list(MANIFESTS)

    def run(self, ctx: Context, check: Check) -> Outcome:
        min_age = check.get_int("min_age_days", 30)
        allow = {a.lower() for a in check.get_list("allow")}
        head = declared(ctx.root)
        base = declared_at(ctx.root, ctx.base) if ctx.base else set()
        added = sorted((p for p in head - base if p.name.lower() not in allow), key=lambda p: (p.ecosystem, p.name))
        if not added:
            return Outcome("pass", f"no new dependencies to vet ({len(head)} declared)")

        with ThreadPoolExecutor(max_workers=8) as pool:
            published = dict(zip(added, pool.map(first_published, added), strict=True))
        now = datetime.now(UTC)
        missing = [p for p, t in published.items() if t is None]
        young = [(p, (now - t).days) for p, t in published.items() if t is not None and (now - t).days < min_age]

        scope = f"new since {ctx.base[:10]}" if ctx.base else "declared"
        lines = [f"{len(added)} {scope} package(s) vetted against their registries"]
        lines += [f"MISSING {p.ecosystem}:{p.name}: not on the registry (hallucinated or private?)" for p in missing]
        lines += [f"NEW     {p.ecosystem}:{p.name}: first published {d} day(s) ago" for p, d in young]
        if missing or young:
            lines.append("If a package is intended, add it to `allow` for this check after reviewing it.")
            return Outcome("fail", f"{len(missing)} missing, {len(young)} younger than {min_age} days", lines)
        return Outcome("pass", f"{len(added)} {scope} package(s) exist and are at least {min_age} days old", lines)
