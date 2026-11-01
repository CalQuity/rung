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
import os
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rung.checks.base import Checker, Context, Outcome
from rung.core import git
from rung.core.config import Check
from rung.core.errors import RungError

MANIFESTS = ["**/requirements*.txt", "**/requirements/*.txt", "**/pyproject.toml", "**/package.json"]
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Package:
    ecosystem: str  # pypi | npm
    name: str


def _normalize_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(spec: str) -> str:
    """The normalized package name in a requirement such as `playwright>=1.40`, or empty."""
    m = _REQ_NAME.match(spec)
    return _normalize_pypi(m.group(1)) if m else ""


def _pep508_names(specs: list[str]) -> set[str]:
    names = set()
    for spec in specs:
        if m := _REQ_NAME.match(spec):
            names.add(_normalize_pypi(m.group(1)))
    return names


def parse_manifest(
    path: str, text: str, read: Callable[[str], str | None] | None = None, seen: frozenset[str] = frozenset()
) -> set[Package]:
    """Direct dependencies declared in one manifest file.

    `read(relative_path)` returns another file's text (or None), used to follow `-r` includes
    in requirements files at the same revision. Includes outside the repository, and cycles, are
    not followed. A manifest that cannot be parsed raises RungError.
    """
    try:
        return _parse_manifest(path, text, read, seen | {path})
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError, TypeError) as exc:
        raise RungError(f"{path}: cannot read its dependencies ({exc})") from exc


def _parse_manifest(
    path: str, text: str, read: Callable[[str], str | None] | None, seen: frozenset[str]
) -> set[Package]:
    name = Path(path).name
    if name.endswith(".txt"):
        names: set[str] = set()
        specs = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")) and read:
                included = os.path.normpath(Path(path).parent / line.split(None, 1)[1].strip())
                if included.startswith(("..", "/")) or included in seen:
                    continue
                if (inner := read(included)) is not None:
                    names |= {p.name for p in parse_manifest(included, inner, read, seen)}
            elif line and not line.startswith(("-", "git+", "http:", "https:", ".", "/")):
                specs.append(line)
        return {Package("pypi", n) for n in names | _pep508_names(specs)}
    if name == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        specs = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            specs += group
        for group in data.get("dependency-groups", {}).values():
            specs += [s for s in group if isinstance(s, str)]
        poetry = data.get("tool", {}).get("poetry", {})
        poetry_tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
        poetry_tables += [g.get("dependencies", {}) for g in poetry.get("group", {}).values()]
        for table in poetry_tables:
            specs += [k for k, v in table.items() if k.lower() != "python" and not _poetry_local(v)]
        own = _normalize_pypi(project.get("name", "")) if project.get("name") else None
        return {Package("pypi", n) for n in _pep508_names(specs) if n != own}
    if name == "package.json":
        data = json.loads(text)
        packages: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for dep, version in (data.get(key) or {}).items():
                version = str(version)
                if version.startswith("npm:"):  # an alias: vet the package actually installed
                    target = version[4:]
                    packages.add(target.rsplit("@", 1)[0] if target.count("@") > (1 if target.startswith("@") else 0)
                                 else target)  # fmt: skip
                elif not version.startswith(("file:", "link:", "workspace:", "git", "http", "github:")):
                    packages.add(dep)
        return {Package("npm", n) for n in packages}
    return set()


def _poetry_local(spec: object) -> bool:
    return isinstance(spec, dict) and any(k in spec for k in ("path", "git", "url"))


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
    except (TimeoutError, OSError) as exc:
        raise RungError(f"{url} did not answer: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RungError(f"{url} returned malformed JSON") from exc


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


def _is_manifest(path: str) -> bool:
    return any(git.glob_match(p, path) for p in MANIFESTS) and "node_modules/" not in path


def declared(root: Path) -> set[Package]:
    def read(rel: str) -> str | None:
        target = root / rel
        return target.read_text(errors="ignore") if target.is_file() else None

    out: set[Package] = set()
    for rel in (f for f in git.list_files(root) if _is_manifest(f)):
        out |= parse_manifest(rel, read(rel) or "", read)
    return out


def declared_at(root: Path, commit: str) -> set[Package]:
    """Dependencies declared at `commit`, reading that revision's own manifests (renames included)."""

    def read(rel: str) -> str | None:
        try:
            return git.show(root, commit, rel)
        except git.GitError:
            return None

    out: set[Package] = set()
    for rel in (f for f in git.files_at(root, commit) if _is_manifest(f)):
        out |= parse_manifest(rel, read(rel) or "", read)
    return out


class DepsChecker(Checker):
    kind = "deps"
    summary = "New dependencies must exist on their registry and not be brand new"

    def max_age_days(self, check: Check) -> int | None:
        # A package can be published, yanked or taken over without any file here changing.
        return check.get_int("max_age_days", 1) or None

    def default_covers(self, check: Check) -> list[str]:
        return list(MANIFESTS)

    def run(self, ctx: Context, check: Check) -> Outcome:
        min_age = check.get_int("min_age_days", 30)
        allow = {_normalize_pypi(a) for a in check.get_list("allow")}
        head = declared(ctx.root)
        base = declared_at(ctx.root, ctx.base) if ctx.base else set()
        added = sorted(
            (p for p in head - base if _normalize_pypi(p.name) not in allow), key=lambda p: (p.ecosystem, p.name)
        )
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
