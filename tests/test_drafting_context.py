"""What drafts are shown besides the sources (SQL behind database calls, an app's routes), and the
machine-specific paths Rung refuses in what the model writes back."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from rung import cli
from rung.analysis import survey
from rung.drafting import common, providers, routes, runtime, sql_context
from rung.drafting import lean as lean_draft
from rung.drafting import property as property_draft
from rung.drafting import tla as tla_draft
from rung.engines import drt, lean, tla

from .test_draft import DRIVE_OK, SPEC_REPLY, ScriptedProvider, _files, _lean_ok, _lean_reply
from .test_property import STRONG, _reply

REPOSITORY = """\
class OnboardingRepository:
    def verify(self, phone, otp):
        return self._rpc("verify_stockedge_onboarding_otp", {"p_phone": phone, "p_otp": otp})

    def claim(self, cur):
        cur.execute("select count(*) from jobs")
        cur.execute("SELECT public.claim_next_job(%s)", (1,))
"""
FIRST = """\
create or replace function public.verify_stockedge_onboarding_otp(p_phone text, p_otp text)
returns boolean language sql as $$ select true $$;
"""
SECOND = """\
-- Lock the row so two verifications cannot both succeed.
create or replace function public.verify_stockedge_onboarding_otp(p_phone text, p_otp text)
returns boolean language plpgsql as $fn$
begin
  perform pg_advisory_xact_lock(hashtext(p_phone));
  select 1 from otps where phone = p_phone for update;
  return true;
end;
$fn$;
"""


def _commit(root: Path, message: str, when: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True, capture_output=True, env=env)


@pytest.fixture
def supabase_repo(repo: Path) -> Path:
    """A repository whose Python calls Postgres functions, one defined twice and one not at all.

    The newer definition sits in the file whose name sorts first, so only commit dates order them.
    """
    (repo / "repository.py").write_text(REPOSITORY)
    (repo / "db").mkdir()
    (repo / "db" / "200_otp.sql").write_text(FIRST)
    (repo / "db" / "300_unrelated.sql").write_text("create function other() returns int as $$ select 1 $$;\n")
    _commit(repo, "first otp function", "2026-01-01T00:00:00")
    (repo / "db" / "100_otp_locks.sql").write_text(SECOND)
    _commit(repo, "lock the otp row", "2026-02-01T00:00:00")
    return repo


# --- SQL behind database calls ------------------------------------------------------------------


def test_database_calls_are_found_in_rpc_calls_and_sql_strings() -> None:
    assert sql_context.calls_in(REPOSITORY) == ["verify_stockedge_onboarding_otp", "public.claim_next_job"]
    assert sql_context.calls_in('supabase.rpc("get_balance", {}).execute()\nq = "call settle(1)"') == [
        "get_balance",
        "settle",
    ]


def test_every_definition_is_found_oldest_first_and_a_missing_one_is_a_warning(supabase_repo: Path) -> None:
    ctx = sql_context.gather(supabase_repo, [supabase_repo / "repository.py"])
    assert ctx.files == ["db/200_otp.sql", "db/100_otp_locks.sql"]  # by commit date, not by name
    assert ctx.defined == {"verify_stockedge_onboarding_otp": ["db/200_otp.sql", "db/100_otp_locks.sql"]}
    assert ctx.missing == ["public.claim_next_job"]
    (warning,) = ctx.warnings
    assert "public.claim_next_job" in warning and "atomic" in warning and "weaker" in warning
    block = ctx.block(supabase_repo)
    assert "newest definition is the one that runs" in block
    assert block.index("--- db/200_otp.sql ---") < block.index("--- db/100_otp_locks.sql ---")
    assert "for update" in block and "300_unrelated" not in block


def test_uncommitted_definitions_count_as_newest_and_names_break_ties(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text('db.rpc("f")\n')
    (tmp_path / "b.sql").write_text("create function f() returns int as $$ select 1 $$;\n")
    (tmp_path / "a.sql").write_text("CREATE OR REPLACE FUNCTION f() returns int as $$ select 2 $$;\n")
    assert sql_context.gather(tmp_path, [tmp_path / "a.py"]).files == ["a.sql", "b.sql"]


def test_a_large_sql_file_contributes_only_the_called_definitions(tmp_path: Path) -> None:
    filler = "".join(f"insert into t values ({i});\n" for i in range(3000))
    (tmp_path / "dump.sql").write_text(filler + SECOND + filler)
    (tmp_path / "a.py").write_text('db.rpc("verify_stockedge_onboarding_otp")\n')
    block = sql_context.gather(tmp_path, [tmp_path / "a.py"]).block(tmp_path)
    assert "insert into t" not in block
    assert "3002 create or replace function public.verify_stockedge_onboarding_otp" in block
    assert "$fn$;" in block


def test_tla_draft_sees_the_sql_and_reports_what_it_had_to_assume(supabase_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("pass", "ok", distinct_states=9))
    provider = ScriptedProvider([SPEC_REPLY])
    result = tla_draft.draft(provider, supabase_repo, [supabase_repo / "repository.py"], supabase_repo / "spec")
    assert "pg_advisory_xact_lock" in provider.prompts[0] and "--- db/100_otp_locks.sql ---" in provider.prompts[0]
    assert "Model each of those calls as one atomic step" in provider.prompts[0]
    assert result.context == ["db/200_otp.sql", "db/100_otp_locks.sql"]
    assert any("public.claim_next_job" in w for w in result.warnings)


def test_lean_draft_sees_the_sql_too(supabase_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(lean, "toolchain", lambda: "leanprover/lean4:v4.35.0")
    monkeypatch.setattr(lean, "run", lambda *a, **k: _lean_ok([]))
    monkeypatch.setattr(lean_draft, "_drt_check", lambda *a, **k: drt.DRTResult("pass", "ok", cases=300))
    provider = ScriptedProvider([_lean_reply(["M.u"])])
    result = lean_draft.draft(provider, supabase_repo, [supabase_repo / "repository.py"], supabase_repo / "model")
    assert "--- db/100_otp_locks.sql ---" in provider.prompts[0]
    assert result.context == ["db/200_otp.sql", "db/100_otp_locks.sql"] and result.warnings


def test_draft_command_prints_the_sql_it_added_and_covers_it(supabase_repo: Path, monkeypatch, capsys) -> None:
    (supabase_repo / "rung.toml").write_text('[project]\nname = "p"\n\n[llm]\nprovider = "none"\n')
    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("pass", "ok", distinct_states=9))
    monkeypatch.setattr(providers, "get", lambda llm: ScriptedProvider([SPEC_REPLY]))
    assert cli.main(["-C", str(supabase_repo), "draft", "tla", "repository.py"]) == 0
    out = capsys.readouterr().out
    assert "added db/200_otp.sql" in out and "added db/100_otp_locks.sql" in out
    assert "WARNING" in out and "public.claim_next_job" in out and "atomic" in out
    (check,) = tomllib.loads((supabase_repo / "rung.toml").read_text())["check"]
    assert check["covers"] == ["repository.py", "db/200_otp.sql", "db/100_otp_locks.sql"]


# --- routes -------------------------------------------------------------------------------------

APP = {
    "api/users.py": (
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/users")\n\n'
        '@router.get("/{user_id}")\n'
        "async def get_user(user_id: int): ...\n\n"
        "@router.post(\n"
        '    "/{user_id}/otp")\n'
        "def verify(user_id: int): ...\n"
    ),
    "api/main.py": 'app.include_router(users.router, prefix="/api")\n',
    "legacy.py": '@app.route("/login", methods=["GET", "POST"])\ndef login(): ...\n',
    "server/index.ts": (
        "app.get('/health', (req, res) => res.send('ok'))\n"
        "router.delete(`/items/:id`, auth, remove)\n"
        "app.use('/v1', router)\n"
        "axios.get('/not-a-route')\n"
        "api.get('/also-a-client-call', { params })\n"
    ),
    "web/package.json": '{"dependencies": {"next": "16.0.0"}}',
    "web/src/app/(shop)/cart/[id]/page.tsx": "export default function Cart() {}",
    "web/src/app/api/agents/route.ts": "export async function GET() {}\nexport async function POST() {}\n",
    "web/src/app/_lib/page.tsx": "private",
    "web/pages/api/hello.ts": "export default function handler() {}",
    "web/pages/_app.tsx": "app",
    "tests/test_api.py": '@app.get("/from-a-test")\ndef t(): ...\n',
}


def test_routes_are_found_across_frameworks() -> None:
    found = {(r.method, r.path, f"{r.file}:{r.line}") for r in routes.discover(APP)}
    assert found == {
        ("GET", "/users/{user_id}", "api/users.py:4"),
        ("POST", "/users/{user_id}/otp", "api/users.py:8"),
        ("MOUNT", "/api", "api/main.py:1"),
        ("GET", "/login", "legacy.py:1"),
        ("POST", "/login", "legacy.py:1"),
        ("GET", "/health", "server/index.ts:1"),
        ("DELETE", "/items/:id", "server/index.ts:2"),
        ("MOUNT", "/v1", "server/index.ts:3"),
        ("PAGE", "/cart/[id]", "web/src/app/(shop)/cart/[id]/page.tsx:1"),
        ("GET", "/api/agents", "web/src/app/api/agents/route.ts:1"),
        ("POST", "/api/agents", "web/src/app/api/agents/route.ts:2"),
        ("ANY", "/api/hello", "web/pages/api/hello.ts:1"),
    }


def test_runtime_context_lists_the_routes_and_says_to_use_only_them(tmp_path: Path) -> None:
    for rel, text in APP.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    text = runtime.gather_context(tmp_path, survey.survey(tmp_path), [])
    assert "Drive only these routes" in text and "say in `notes` that the app has no such feature" in text
    assert "POST    /users/{user_id}/otp" in text and "api/users.py:8" in text
    assert "/from-a-test" not in text


def test_runtime_context_has_no_route_section_without_routes(tmp_path: Path) -> None:
    (tmp_path / "cli.py").write_text("print('hi')\n")
    assert "Routes the app serves" not in runtime.gather_context(tmp_path, survey.survey(tmp_path), [])


# --- machine-specific paths ---------------------------------------------------------------------


def test_home_and_temp_paths_are_found_but_relative_ones_are_not() -> None:
    found = common.machine_paths({
        "drt_adapter.py": 'ROOT = Path("/Users/pratham/Desktop/app")\nB = "/home/ci/x"\nC = r"C:\\Users\\dev\\x"',
        "drive.py": 'out = "/tmp/run.log"\nd = "/private/var/folders/x/T/a"\nok = "src/tmp/x"\nhome = "~/x"',
        "FEATURES.md": "Logs go to /tmp while it runs.",
    })  # fmt: skip
    assert found == [
        "drt_adapter.py:1: /Users/pratham/",
        "drt_adapter.py:2: /home/ci/",
        "drt_adapter.py:3: C:\\Users\\",
        "drive.py:1: /tmp",
        "drive.py:2: /private/var/folders/",
    ]
    assert common.machine_paths({"Q.tla": "Next == A \\/ B\nInv == x /\\ y"}) == []


def test_tla_draft_sends_a_machine_path_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tla, "run", lambda *a, **k: tla.TLCResult("pass", "ok", distinct_states=9))
    spec = "---- MODULE Q ----\n\\* traced from /Users/alice/src/q.py\n===="
    local = {**SPEC_REPLY, "files": _files(**{"Q.tla": spec, "Q.cfg": "SPECIFICATION Spec"})}
    provider = ScriptedProvider([local, SPEC_REPLY])
    _touch(tmp_path)
    result = tla_draft.draft(provider, tmp_path, [tmp_path / "q.py"], tmp_path / "spec")
    assert result.ok and result.rounds == 2
    assert "Q.tla:2: /Users/alice/" in provider.prompts[1]
    assert "use paths relative to the script or the repository root" in provider.prompts[1].lower()


def test_lean_adapter_with_a_home_directory_fallback_is_sent_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lean, "toolchain", lambda: "leanprover/lean4:v4.35.0")
    built: list[Any] = []
    monkeypatch.setattr(lean, "run", lambda *a, **k: built.append(a) or _lean_ok([]))
    monkeypatch.setattr(lean_draft, "_drt_check", lambda *a, **k: drt.DRTResult("pass", "ok", cases=300))
    local = _lean_reply(["M.u"])
    local["files"] = [
        {**f, "content": 'import sys\nsys.path.insert(0, "/Users/pratham/Desktop/app")\n'}
        if f["path"] == "drt_adapter.py" else f
        for f in local["files"]
    ]  # fmt: skip
    provider = ScriptedProvider([local, _lean_reply(["M.u"])])
    _touch(tmp_path)
    result = lean_draft.draft(provider, tmp_path, [tmp_path / "q.py"], tmp_path / "model")
    assert result.ok and result.rounds == 2 and len(built) == 1  # never built with the bad adapter
    assert "drt_adapter.py:2: /Users/pratham/" in provider.prompts[1]
    assert "/Users/pratham" not in (tmp_path / "model" / "drt_adapter.py").read_text()


def test_runtime_drive_script_with_a_temp_path_is_sent_back(tmp_path: Path) -> None:
    local = {**DRIVE_OK, "files": _files(**{"drive.py": 'LOG = "/tmp/server.log"\n', "FEATURES.md": "# F"})}
    provider = ScriptedProvider([local, DRIVE_OK])
    result = runtime.draft_runtime(provider, tmp_path, survey.survey(tmp_path), focus=[], max_rounds=2)
    assert result.ok and result.rounds == 2
    assert "drive.py:1: /tmp" in provider.prompts[1]


def test_property_test_with_a_home_path_is_sent_back(tmp_path: Path) -> None:
    from .test_property import PRICING

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "s"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n')
    (tmp_path / "pricing.py").write_text(PRICING)
    fallback = 'import sys\nsys.path.insert(0, "/home/dev/shop")\nfrom pricing import total'
    local = STRONG.replace("from pricing import total", fallback)
    provider = ScriptedProvider([_reply(local), _reply(STRONG)])
    result, _ = property_draft.draft(provider, tmp_path, [tmp_path / "pricing.py"], "pricing")
    assert result.ok and result.rounds == 2
    assert "/home/dev/" in provider.prompts[1] and "relative to the script" in provider.prompts[1]


def _touch(root: Path) -> None:
    (root / "q.py").write_text("def claim():\n    pass\n")


def test_a_requests_transport_adapter_is_not_a_route() -> None:
    from rung.drafting import routes

    files = {
        "scraper.py": 'session = requests.Session()\nsession.mount("https://", HTTPAdapter(max_retries=3))\n',
        "app.py": 'app.mount("/static", StaticFiles(directory="static"))\n',
    }
    assert [(r.method, r.path) for r in routes.discover(files)] == [("MOUNT", "/static")]
