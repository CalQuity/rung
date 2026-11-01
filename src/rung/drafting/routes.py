"""The HTTP routes an app really serves, found in its code, so a drive script never guesses a URL.

Text matching, not an import of the app: decorators in FastAPI, Starlette and Flask, method calls in
Express, Hono and Koa, and the file layout of the Next.js app and pages routers. A prefix declared on
a router in the same file (`APIRouter(prefix=...)`, `Blueprint(url_prefix=...)`) is applied; where a
router is mounted elsewhere (`include_router(..., prefix=...)`, `app.use("/api", router)`), the mount
is listed too, so the model can combine them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rung.checks.prove import is_test_file

PY = (".py",)
JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
MAX_FILE_BYTES = 500_000
MAX_ROUTES = 300
_VERBS = "get|post|put|patch|delete|head|options"

# `@app.get("/x"`, `@router.post("/x"`, `@app.route("/x", methods=[...])`, `@app.websocket("/ws"`.
_PY_ROUTE = re.compile(
    rf"""^\s*@(\w+(?:\.\w+)*)\.({_VERBS}|route|api_route|websocket)\(\s*[rbu]?["']([^"']*)["']""", re.M
)
_NEXT_DECORATOR = re.compile(r"^\s*(?:@|def |async def )", re.M)
_PY_METHODS = re.compile(r"""methods\s*=\s*[\[(]([^\])]*)""")
_PY_PREFIX = re.compile(r"""^\s*(\w+)\s*(?::\s*\w+\s*)?=\s*(?:\w+\.)*(?:APIRouter|Blueprint)\(([^\n]*)""", re.M)
_PREFIX_ARG = re.compile(r"""\b(?:url_)?prefix\s*=\s*[rbu]?["']([^"']*)["']""")
_PY_MOUNT = re.compile(r"""\b(\w+(?:\.\w+)*)\.(include_router|register_blueprint|mount)\(([^\n]*)""")
# `app.get('/x', handler`, `router.post("/x", auth, handler`. A handler must follow the path, so client
# calls such as `axios.get('/x')` or `api.get('/x', {params})` are left out.
_JS_ROUTE = re.compile(rf"""\b(\w+)\.({_VERBS}|all)\(\s*["'`](/[^"'`]*)["'`]\s*,(?!\s*\{{)""")
_JS_MOUNT = re.compile(r"""\b(\w+)\.(use|route)\(\s*["'`](/[^"'`]*)["'`]\s*,""")
_JS_CLIENTS = frozenset({"axios", "http", "https", "client", "ky", "got", "superagent", "request", "supertest",
                         "fetcher", "instance", "cy", "map", "cache", "store", "redis", "cookies"})  # fmt: skip
_NEXT_HANDLER = re.compile(rf"""export\s+(?:async\s+)?(?:function|const)\s+({_VERBS.upper()})\b""")
_NEXT_CONFIGS = ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs")
_PAGE = ("page.tsx", "page.jsx", "page.ts", "page.js", "page.mdx")
_ROUTE = ("route.ts", "route.js", "route.tsx", "route.jsx", "route.mts", "route.mjs")


@dataclass(frozen=True)
class Route:
    method: str  # GET, POST, ..., ANY (every method), PAGE (a page a browser loads), MOUNT (a prefix)
    path: str
    file: str
    line: int

    def render(self) -> str:
        return f"{self.method:7} {self.path:40} {self.file}:{self.line}"


def _join(prefix: str, path: str) -> str:
    """A router prefix and a route path as one URL path, keeping the route's trailing slash."""
    joined = "/" + "/".join(p for p in (prefix.strip("/"), path.strip("/")) if p)
    return joined + "/" if path.endswith("/") and joined != "/" else joined


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def python_routes(rel: str, text: str) -> list[Route]:
    prefixes = {}
    for m in _PY_PREFIX.finditer(text):
        if arg := _PREFIX_ARG.search(m.group(2)):
            prefixes[m.group(1)] = arg.group(1)
    routes = []
    for m in _PY_ROUTE.finditer(text):
        owner, verb, path = m.groups()
        stop = _NEXT_DECORATOR.search(text, m.end())
        rest = text[m.end() : stop.start() if stop else len(text)]  # the decorator's other arguments
        full = _join(prefixes.get(owner.split(".")[-1], ""), path)
        line = _line_of(text, m.start(3))
        if verb in ("route", "api_route"):
            listed = _PY_METHODS.search(rest)
            methods = re.findall(r"""["'](\w+)["']""", listed.group(1)) if listed else []
            methods = [x.upper() for x in methods] or (["GET"] if verb == "route" else ["ANY"])
        else:
            methods = ["WS" if verb == "websocket" else verb.upper()]
        routes += [Route(method, full, rel, line) for method in methods]
    for m in _PY_MOUNT.finditer(text):
        if arg := _PREFIX_ARG.search(m.group(3)):
            routes.append(Route("MOUNT", arg.group(1), rel, _line_of(text, m.start())))
        elif m.group(2) == "mount" and (path := re.match(r"""\s*["']([^"']+)["']""", m.group(3))):
            routes.append(Route("MOUNT", path.group(1), rel, _line_of(text, m.start())))
    return routes


def js_routes(rel: str, text: str) -> list[Route]:
    routes = []
    for m in _JS_ROUTE.finditer(text):
        if m.group(1) in _JS_CLIENTS:
            continue
        method = "ANY" if m.group(2) == "all" else m.group(2).upper()
        routes.append(Route(method, m.group(3), rel, _line_of(text, m.start())))
    for m in _JS_MOUNT.finditer(text):
        if m.group(1) not in _JS_CLIENTS:
            routes.append(Route("MOUNT", m.group(3), rel, _line_of(text, m.start())))
    return routes


def _next_base(parts: tuple[str, ...], next_dirs: set[str], marker: str) -> int:
    """The index of a Next.js project's `app` or `pages` directory in `parts` (`src/` allowed), or -1."""
    for i, part in enumerate(parts[:-1]):
        if part != marker:
            continue
        project = parts[: i - 1] if i and parts[i - 1] == "src" else parts[:i]
        if "/".join(project) in next_dirs:
            return i
    return -1


def _segments(parts: tuple[str, ...]) -> str:
    """URL path for Next.js directory segments: route groups `(x)` and parallel-route slots `@x` vanish."""
    kept = [p for p in parts if not (p.startswith("(") and p.endswith(")")) and not p.startswith("@")]
    return "/" + "/".join(kept)


def next_routes(rel: str, text: str, next_dirs: set[str]) -> list[Route]:
    """App router `page.*` and `route.*` files, and pages-router files, as URL paths."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if name in _PAGE or name in _ROUTE:
        i = _next_base(parts, next_dirs, "app")
        if i < 0 or any(p.startswith("_") for p in parts[i + 1 : -1]):  # `_x` folders are private
            return []
        path = _segments(parts[i + 1 : -1])
        if name in _PAGE:
            return [Route("PAGE", path, rel, 1)]
        found = [(m.group(1), _line_of(text, m.start())) for m in _NEXT_HANDLER.finditer(text)]
        return [Route(method, path, rel, line) for method, line in found] or [Route("ANY", path, rel, 1)]
    i = _next_base(parts, next_dirs, "pages")
    if i < 0 or PurePosixPath(name).stem in ("_app", "_document", "_error"):
        return []
    stem = (*parts[i + 1 : -1], PurePosixPath(name).stem)
    if stem[-1] == "index":
        stem = stem[:-1]
    return [Route("ANY" if stem[:1] == ("api",) else "PAGE", _segments(stem), rel, 1)]


def _next_dirs(files: dict[str, str]) -> set[str]:
    """Project directories that use Next.js: a `next.config.*`, or `next` in package.json."""
    dirs = set()
    for rel, text in files.items():
        where, _, name = rel.rpartition("/")
        if name in _NEXT_CONFIGS or (name == "package.json" and '"next"' in text):
            dirs.add(where)
    return dirs


def discover(files: dict[str, str]) -> list[Route]:
    """Routes in `files` (repository path -> text), with test files and Rung's own files left out."""
    next_dirs = _next_dirs(files)
    routes: list[Route] = []
    for rel, text in files.items():
        if is_test_file(rel) or rel.startswith(".rung/"):
            continue
        if rel.endswith(PY):
            routes += python_routes(rel, text)
        elif rel.endswith(JS):
            routes += next_routes(rel, text, next_dirs) or js_routes(rel, text)
    return list(dict.fromkeys(routes))[:MAX_ROUTES]


def scan(root: Path, listed: list[str]) -> list[Route]:
    """Read the candidate files under `root` (Python, JavaScript/TypeScript, package.json) and discover routes."""
    files = {}
    for rel in listed:
        if not rel.endswith((*PY, *JS, "package.json")) and PurePosixPath(rel).name not in _NEXT_CONFIGS:
            continue
        path = root / rel
        try:
            if path.stat().st_size <= MAX_FILE_BYTES:
                files[rel] = path.read_text(errors="ignore")
        except OSError:
            continue
    return discover(files)


def prompt_block(routes: list[Route]) -> str:
    """The routes for the prompt, with the rule that only these may be driven; empty when none were found."""
    if not routes:
        return ""
    return "\n".join([
        "Routes the app serves, found in its code (method, path, file:line). MOUNT lines are prefixes a router",
        "is mounted under; combine them with the routes in that router's file. PAGE is a page a browser loads.",
        "Drive only these routes. If the feature you were asked to drive is not among them, do not guess a URL:",
        "say in `notes` that the app has no such feature, and drive the closest listed feature instead.",
        *(r.render() for r in routes),
    ])  # fmt: skip
