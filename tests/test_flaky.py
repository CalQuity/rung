"""Real subprocess evidence for the opt-in repeated pytest diagnostic."""

import json
import sys

import pytest

from rung.analysis import flaky
from rung.core import config


def setup_project(repo, source, flags=""):
    (repo / ".gitignore").write_text(".rung/\n__pycache__/\n.pytest_cache/\n")
    (repo / "test_sample.py").write_text(source)
    (repo / "rung.toml").write_text(
        '[project]\nname="sample"\n[[check]]\nid="unit"\nkind="command"\n'
        + "run="
        + json.dumps(f"{sys.executable} -m pytest -q {flags}")
        + "\n"
    )
    cfg = config.load(repo)
    return cfg, cfg.checks[0]


def test_observed_alternation_is_advisory_and_retains_attempts(repo):
    cfg, check = setup_project(
        repo,
        """from pathlib import Path

def test_counter():
    p=Path('.rung/cache/counter'); p.parent.mkdir(parents=True, exist_ok=True)
    n=int(p.read_text()) if p.exists() else 0
    p.write_text(str(n+1))
    assert n % 2 == 0
""",
    )
    result = flaky.run(cfg, check, attempts=3)
    assert result["exit_code"] == 0
    assert result["metrics"]["flaky_cases"] == 1
    assert result["metrics"]["attempts_completed"] == 3
    assert result["cases"][0]["classification"] == "intermittent"
    assert not (repo / ".rung/verdicts/unit.json").exists()
    assert len(result["attempts"]) == 3


def test_exact_parameters_and_consistent_failure(repo):
    cfg, check = setup_project(
        repo, 'import pytest\n@pytest.mark.parametrize("x", [0,1])\ndef test_p(x): assert x == 0\n'
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 1
    assert result["metrics"]["flaky_cases"] == 0
    assert result["metrics"]["consistent_failures"] == 1
    assert {c["id"] for c in result["cases"]} == {"test_sample.py::test_p[0]", "test_sample.py::test_p[1]"}


def test_lifecycle_skips_xfail_xpass_are_not_votes(repo):
    cfg, check = setup_project(
        repo,
        """import pytest
@pytest.fixture
def broken(): raise RuntimeError('fixture')
def test_error(broken): pass
@pytest.mark.skip(reason='skip')
def test_skip(): pass
@pytest.mark.xfail(reason='known')
def test_xfail(): assert False
@pytest.mark.xfail(reason='known')
def test_xpass(): pass
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert result["metrics"]["flaky_cases"] == 0
    outcomes = {c["id"]: c["outcomes"] for c in result["cases"]}
    assert outcomes["test_sample.py::test_error"] == ["error", "error"]
    assert outcomes["test_sample.py::test_xpass"] == ["xpass", "xpass"]


@pytest.mark.parametrize("flags", ["--lf", "-x", "--maxfail=2"])
def test_selection_and_early_exit_are_unsupported(repo, flags):
    cfg, check = setup_project(repo, "def test_ok(): pass\n", flags)
    result = flaky.run(cfg, check, attempts=2)
    assert result["state"] == "incomplete"
    assert result["exit_code"] == 2


def test_hidden_addopts_are_unsupported(repo):
    cfg, check = setup_project(repo, "def test_ok(): pass\n")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -x\n")
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert result["errors"]


def test_budget_timeout_retains_incomplete_attempt(repo):
    cfg, check = setup_project(repo, "import time\ndef test_slow(): time.sleep(2)\n")
    result = flaky.run(cfg, check, attempts=2, budget_seconds=0.15)
    assert result["exit_code"] == 2
    assert result["metrics"]["attempts_completed"] == 0
    assert result["attempts"][0]["state"] == "incomplete"


def test_strict_intermittent_exit_and_clean_sample(repo):
    cfg, check = setup_project(repo, "def test_ok(): pass\n")
    result = flaky.run(cfg, check, attempts=2, fail_on_flaky=True)
    assert result["exit_code"] == 0
    assert result["cases"][0]["classification"] == "no_variability"
    assert "prove" in result["summary"]


def test_missing_case_does_not_become_pass_vote(repo):
    cfg, check = setup_project(
        repo,
        """from pathlib import Path
p=Path('.rung/cache/collection'); p.parent.mkdir(parents=True, exist_ok=True)
n=int(p.read_text()) if p.exists() else 0
p.write_text(str(n+1))
def test_always(): pass
if n % 2 == 0:
    def test_sometimes(): pass
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    case = next(c for c in result["cases"] if c["id"].endswith("test_sometimes"))
    assert case["outcomes"] == ["passed", "missing"]
    assert case["classification"] == "incomplete"


def test_retry_mark_stops_execution_before_test_body(repo):
    cfg, check = setup_project(repo, "import pytest\n@pytest.mark.flaky(reruns=2)\ndef test_retry(): assert False\n")
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert len(result["attempts"]) == 1
    assert any("unsupported" in error for error in result["errors"])


def test_strict_xpass_is_not_a_consistent_ordinary_failure(repo):
    cfg, check = setup_project(repo, "import pytest\n@pytest.mark.xfail(strict=True)\ndef test_xpass(): pass\n")
    result = flaky.run(cfg, check, attempts=2)
    assert result["metrics"]["consistent_failures"] == 0
    assert result["cases"][0]["outcomes"] == ["xpass", "xpass"]
    assert result["exit_code"] == 2


def test_strict_policy_blocks_observed_intermittence(repo):
    cfg, check = setup_project(
        repo,
        """from pathlib import Path
def test_flip():
    p=Path('.rung/cache/flip'); p.parent.mkdir(parents=True, exist_ok=True)
    n=int(p.read_text()) if p.exists() else 0
    p.write_text(str(n+1))
    assert n % 2 == 0
""",
    )
    result = flaky.run(cfg, check, attempts=2, fail_on_flaky=True)
    assert result["exit_code"] == 1
    assert result["metrics"]["flaky_cases"] == 1


def test_missing_report_is_incomplete(repo, monkeypatch):
    import subprocess

    cfg, check = setup_project(repo, "def test_ok(): pass\n")
    monkeypatch.setattr(flaky.proc, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert all(a["state"] == "incomplete" for a in result["attempts"])


def test_collection_error_is_not_an_ordinary_failure(repo):
    cfg, check = setup_project(repo, 'raise RuntimeError("cannot collect")\n')
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert result["metrics"]["consistent_failures"] == 0


def test_mixed_ordinary_votes_remain_visible_with_lifecycle_error(repo):
    cfg, check = setup_project(
        repo,
        """from pathlib import Path
import pytest
@pytest.fixture
def attempt():
    p=Path('.rung/cache/three'); p.parent.mkdir(parents=True, exist_ok=True)
    n=int(p.read_text()) if p.exists() else 0
    p.write_text(str(n+1))
    if n == 2: raise RuntimeError('setup interruption')
    return n
def test_three(attempt): assert attempt == 0
""",
    )
    result = flaky.run(cfg, check, attempts=3)
    assert result["exit_code"] == 2
    assert result["metrics"]["flaky_cases"] == 1
    assert result["metrics"]["incomplete_cases"] == 1
    assert result["cases"][0]["outcomes"] == ["passed", "failed", "error"]


def test_existing_configured_junit_report_is_not_overwritten(repo):
    cfg, check = setup_project(repo, "def test_ok(): pass\n", "--junitxml=previous.xml")
    (repo / "previous.xml").write_text("original receipt")
    result = flaky.run(cfg, check, attempts=2)
    assert (repo / "previous.xml").read_text() == "original receipt"
    assert result["exit_code"] == 0


def test_phase_grouping_consumes_event_stream_once():
    class OnePass:
        def __iter__(self):
            assert not getattr(self, "visited", False), "events rescanned per case"
            self.visited = True
            for number in range(400):
                for phase in ("setup", "call", "teardown"):
                    yield {"event": "phase", "id": f"case[{number}]", "phase": phase, "outcome": "passed"}

    ids = [f"case[{number}]" for number in range(400)]
    outcomes = flaky._case_outcomes(ids, OnePass())
    assert len(outcomes) == 400
    assert set(outcomes.values()) == {"passed"}


def test_malformed_event_shape_is_retained_as_incomplete(repo, monkeypatch):
    import subprocess
    from pathlib import Path

    cfg, check = setup_project(repo, "def test_ok(): pass\n")

    def bad_events(argv, *, env, **kwargs):
        Path(env["RUNG_DIAGNOSTIC_EVENTS"]).write_text("[1]\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(flaky.proc, "run", bad_events)
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    assert "malformed" in result["errors"][0]


def test_deleted_report_is_incomplete(repo, monkeypatch):
    import subprocess
    from pathlib import Path

    cfg, check = setup_project(repo, "def test_ok(): pass\n")

    def delete_report(argv, *, env, **kwargs):
        Path(env["RUNG_DIAGNOSTIC_EVENTS"]).unlink()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(flaky.proc, "run", delete_report)
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["uv", "run", "--", "python", "-m", "pytest", "--", "test_sample.py"],
        ["poetry", "run", "--", "pytest", "--", "test_sample.py"],
        ["uv", "run", "-m", "pytest", "--", "test_sample.py"],
    ],
)
def test_wrapper_markers_stay_before_pytest_diagnostic_flags(repo, argv):
    flags = ["-p", "rung_pytest_diagnostic", "--junitxml=attempt.xml"]
    result = flaky._pytest_flags(argv, repo, flags)
    pytest_index = result.index("pytest")
    assert result[: pytest_index + 1] == argv[: argv.index("pytest") + 1]
    assert result[pytest_index + 1 :] == [*flags, "--", "test_sample.py"]


@pytest.mark.parametrize("selection", ["-k one", "-m chosen"])
def test_report_uses_final_selected_collection(repo, selection):
    cfg, check = setup_project(
        repo,
        """import pytest
@pytest.mark.chosen
def test_one(): pass
def test_two(): pass
""",
        selection,
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 0
    assert [case["id"] for case in result["cases"]] == ["test_sample.py::test_one"]


@pytest.mark.parametrize(
    "phases",
    [
        [
            {"phase": "setup", "outcome": "passed"},
            {"phase": "call", "outcome": "rerun"},
            {"phase": "teardown", "outcome": "passed"},
        ],
        [
            {"phase": "setup", "outcome": "passed"},
            {"phase": "call", "outcome": "failed"},
            {"phase": "call", "outcome": "passed"},
            {"phase": "teardown", "outcome": "passed"},
        ],
    ],
)
def test_unknown_or_duplicate_lifecycle_reports_are_not_ordinary_votes(phases):
    assert flaky._outcome(phases) in ("error", "missing")


def test_real_uv_wrapper_with_both_separator_boundaries(repo):
    import shutil

    uv = shutil.which("uv")
    assert uv, "the real wrapper integration test requires the project uv development tool"
    cfg, check = setup_project(repo, "def test_ok(): pass\n")
    check.options["run"] = f"{uv} run --offline --no-project -- {sys.executable} -m pytest -- test_sample.py"
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 0
    assert result["cases"][0]["outcomes"] == ["passed", "passed"]


@pytest.mark.parametrize(
    "outcomes",
    [
        ["failed", "skipped"],
        ["failed", "xfail"],
        ["failed", "xpass"],
    ],
)
def test_nonordinary_observations_do_not_erase_failure(outcomes):
    assert flaky._classification(outcomes) == "consistent_failure"


@pytest.mark.parametrize(
    "outcomes, expected",
    [
        (["passed", "failed", "skipped"], "intermittent"),
        (["passed", "failed", "error"], "intermittent"),
        (["passed", "skipped"], "no_variability"),
        (["skipped", "xfail"], "non_ordinary"),
    ],
)
def test_classification_uses_only_ordinary_votes(outcomes, expected):
    assert flaky._classification(outcomes) == expected


def test_failure_followed_by_skip_still_exits_failure(repo):
    cfg, check = setup_project(
        repo,
        """from pathlib import Path
import pytest
def test_failure_then_skip():
    p=Path('.rung/cache/fail-skip'); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists(): pytest.skip('cannot exercise this attempt')
    p.write_text('observed')
    assert False
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 1
    assert result["cases"][0]["outcomes"] == ["failed", "skipped"]
    assert result["cases"][0]["classification"] == "consistent_failure"
    assert "ordinary observations" in result["summary"]


@pytest.mark.parametrize("failing, expected_exit", [(False, 0), (True, 1)])
def test_unittest_subtests_have_independent_exact_observations(repo, failing, expected_exit):
    cfg, check = setup_project(
        repo,
        f"""import unittest
class TestChildren(unittest.TestCase):
    def test_children(self):
        for value in (0, 1):
            with self.subTest(value=value):
                self.assertFalse({failing!r} and value == 1)
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == expected_exit
    assert result["metrics"]["cases"] == 3
    children = [case for case in result["cases"] if "::subtest[" in case["id"]]
    assert len(children) == 2
    assert sum(case["classification"] == "consistent_failure" for case in children) == int(failing)
    assert result["metrics"]["incomplete_cases"] == 0


def test_unittest_subtest_alternation_is_visible(repo):
    cfg, check = setup_project(
        repo,
        """import unittest
from pathlib import Path
class TestChildren(unittest.TestCase):
    def test_children(self):
        p=Path('.rung/cache/subtest'); p.parent.mkdir(parents=True, exist_ok=True)
        n=int(p.read_text()) if p.exists() else 0
        p.write_text(str(n+1))
        with self.subTest(value=1):
            self.assertEqual(n % 2, 0)
""",
    )
    result = flaky.run(cfg, check, attempts=3)
    assert result["exit_code"] == 0
    assert result["metrics"]["flaky_cases"] == 1
    child = next(case for case in result["cases"] if "::subtest[" in case["id"])
    assert child["outcomes"] == ["passed", "failed", "passed"]


def test_repeated_subtest_context_is_explicit_group(repo):
    cfg, check = setup_project(
        repo,
        """import unittest
class TestChildren(unittest.TestCase):
    def test_children(self):
        for value in (0, 1):
            with self.subTest(label='same'):
                self.assertTrue(True)
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 0
    assert result["metrics"]["cases"] == 2
    child = next(case for case in result["cases"] if "::subtest[" in case["id"])
    assert child["outcomes"] == ["passed", "passed"]
    assert child["identity_scope"] == "subtest_group"
    assert child["observation_counts"] == [2, 2]
    assert any("cannot identify" in warning for warning in result["warnings"])


def test_dynamic_subtest_missing_in_later_attempt_is_incomplete(repo):
    cfg, check = setup_project(
        repo,
        """import unittest
from pathlib import Path
class TestChildren(unittest.TestCase):
    def test_children(self):
        p=Path('.rung/cache/subtest-missing'); p.parent.mkdir(parents=True, exist_ok=True)
        first=not p.exists(); p.write_text('visited')
        if first:
            with self.subTest(value=1): self.assertTrue(True)
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 2
    child = next(case for case in result["cases"] if "::subtest[" in case["id"])
    assert child["outcomes"] == ["passed", "missing"]


@pytest.mark.parametrize(
    "group_outcomes, expected",
    [
        (["passed", "passed"], "passed"),
        (["passed", "failed"], "failed"),
        (["failed", "skipped"], "failed"),
        (["passed", "rerun"], "error"),
    ],
)
def test_subtest_group_retains_failure_without_inventing_child_identity(group_outcomes, expected):
    reports = [
        {"parent_id": "parent", "phase": "call", "outcome": outcome, "context": {"msg": None, "kwargs": {}}}
        for outcome in group_outcomes
    ]
    assert flaky._subtest_outcome(reports, {"parent"}) == expected


def test_subtest_group_count_changes_are_incomplete(repo):
    cfg, check = setup_project(
        repo,
        """import unittest
from pathlib import Path
class TestChildren(unittest.TestCase):
    def test_children(self):
        p=Path('.rung/cache/group-count'); p.parent.mkdir(parents=True, exist_ok=True)
        first=not p.exists(); p.write_text('visited')
        for value in range(2 if first else 1):
            with self.subTest(): self.assertTrue(True)
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    child = next(case for case in result["cases"] if "::subtest[" in case["id"])
    assert child["identity_scope"] == "subtest_group"
    assert child["observation_counts"] == [2, 1]
    assert child["outcomes"] == ["passed", "passed"]
    assert result["exit_code"] == 2
    assert result["metrics"]["incomplete_cases"] == 1
    assert any("count changed" in error for error in result["errors"])


def test_conflicting_subtest_context_cannot_be_merged():
    reports = [
        {"parent_id": "parent", "phase": "call", "outcome": "passed", "context": {"msg": str(number)}}
        for number in (0, 1)
    ]
    assert flaky._subtest_outcome(reports, {"parent"}) == "error"


def test_mixed_subtest_group_keeps_failure_and_raw_occurrence_count(repo):
    cfg, check = setup_project(
        repo,
        """import unittest
class TestChildren(unittest.TestCase):
    def test_children(self):
        for value in (0, 1):
            with self.subTest(): self.assertEqual(value, 0)
""",
    )
    result = flaky.run(cfg, check, attempts=2)
    assert result["exit_code"] == 1
    child = next(case for case in result["cases"] if "::subtest[" in case["id"])
    assert child["identity_scope"] == "subtest_group"
    assert child["outcomes"] == ["failed", "failed"]
    assert child["observation_counts"] == [2, 2]
    assert all(next(iter(attempt["subtests"].values()))["occurrences"] == 2 for attempt in result["attempts"])


def test_skipped_group_member_is_not_an_ordinary_failure_vote():
    reports = [
        {"parent_id": "parent", "phase": "call", "outcome": outcome, "context": {"msg": None}}
        for outcome in ("passed", "skipped")
    ]
    assert flaky._subtest_outcome(reports, {"parent"}) == "passed"


@pytest.mark.parametrize("location", ["msg", "kwargs"])
def test_raw_subtest_context_values_preserve_type_in_identity(monkeypatch, location):
    from types import SimpleNamespace

    from rung.checks import pytest_diagnostic

    emitted = []
    monkeypatch.setattr(pytest_diagnostic, "emit", lambda event, **data: emitted.append(data))
    for value in (1, "1"):
        context = SimpleNamespace(
            msg=value if location == "msg" else None, kwargs={"value": value} if location == "kwargs" else {}
        )
        report = SimpleNamespace(
            nodeid="test_example.py::test_sub",
            when="call",
            outcome="passed",
            duration=0.0,
            longrepr=None,
            context=context,
        )
        pytest_diagnostic.pytest_runtest_logreport(report)
    assert emitted[0]["id"] != emitted[1]["id"]


@pytest.mark.parametrize(
    "plugins, options, expected",
    [
        ([("flaky", object())], {}, "plugin: flaky"),
        ([], {"force_flaky": True}, "force_flaky"),
        ([], {"force_flaky": True, "max_runs": 3, "min_passes": 2}, "max_runs"),
        ([], {"max_runs": 3}, "max_runs"),
        ([], {"min_passes": 2}, "min_passes"),
        ([], {"reruns": 1}, "reruns"),
        ([], {"retries": 1}, "retries"),
    ],
)
def test_unmarked_native_retry_plugins_and_options_are_rejected(monkeypatch, plugins, options, expected):
    from types import SimpleNamespace

    from rung.checks import pytest_diagnostic

    monkeypatch.setattr(pytest_diagnostic, "emit", lambda *args, **kwargs: None)
    config = SimpleNamespace(
        option=SimpleNamespace(**options), pluginmanager=SimpleNamespace(list_name_plugin=lambda: plugins)
    )
    pytest_diagnostic.pytest_configure(config)
    assert any(expected in error for error in config._rung_diagnostic_problems)


def test_inactive_native_retry_options_do_not_change_experiment(monkeypatch):
    from types import SimpleNamespace

    from rung.checks import pytest_diagnostic

    monkeypatch.setattr(pytest_diagnostic, "emit", lambda *args, **kwargs: None)
    config = SimpleNamespace(
        option=SimpleNamespace(force_flaky=False, max_runs=1, min_passes=1, count=1, reruns=0),
        pluginmanager=SimpleNamespace(list_name_plugin=lambda: [("flaky", None)]),
    )
    pytest_diagnostic.pytest_configure(config)
    assert config._rung_diagnostic_problems == []
