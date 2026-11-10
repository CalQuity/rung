"""Standalone stdlib-only pytest reporter, copied into the target environment.

Never import Rung here: the target interpreter need not have it installed.
"""

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version


def emit(event, **data):
    with open(os.environ["RUNG_DIAGNOSTIC_EVENTS"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **data}) + "\n")


def pytest_configure(config):
    problems = []
    for name in ("lf", "failedfirst", "newfirst", "stepwise", "stepwise_skip", "collectonly", "maxfail", "force_flaky"):
        if getattr(config.option, name, False):
            problems.append("unsupported pytest setting: " + name)
    for name, plugin in config.pluginmanager.list_name_plugin():
        unsupported = ("rerun", "repeat", "retry", "flaky", "randomly", "random_order")
        if plugin is not None and any(part in name.lower() for part in unsupported):
            problems.append("unsupported pytest plugin: " + name)
    if getattr(config.option, "numprocesses", None):
        problems.append("unsupported parallel pytest workers: lifecycle events are not forwarded")
    # Native retry/repeat plugins may use arbitrary registration names.
    for name in ("reruns", "count", "retries", "max_runs", "min_passes"):
        value = getattr(config.option, name, None)
        inactive = (None, False, 0) if name in {"reruns", "retries"} else (None, False, 0, 1)
        if value not in inactive:
            problems.append("unsupported pytest option: " + name)
    config._rung_diagnostic_problems = problems
    try:
        pytest_version = version("pytest")
    except PackageNotFoundError:
        pytest_version = "unknown"
    emit(
        "configured",
        errors=problems,
        pytest_version=pytest_version,
        python_version=sys.version,
        executable=sys.executable,
    )


def pytest_collection_modifyitems(session, config, items):
    problems = config._rung_diagnostic_problems
    for item in items:
        for mark in item.iter_markers():
            if mark.name in ("flaky", "repeat", "rerun", "retry"):
                problems.append("unsupported retry/repeat mark: " + item.nodeid)
    if problems:
        items[:] = []


def pytest_collection_finish(session):
    emit("collection", ids=[item.nodeid for item in session.items], errors=session.config._rung_diagnostic_problems)


def pytest_collectreport(report):
    if report.failed:
        emit("collection_error", id=report.nodeid, message=str(report.longrepr))


def pytest_runtest_logreport(report):
    data = {
        "id": report.nodeid,
        "phase": report.when,
        "outcome": report.outcome,
        "wasxfail": getattr(report, "wasxfail", None),
        "duration": report.duration,
        "message": str(report.longrepr) if report.longrepr else "",
        "strict_xpass": str(report.longrepr).startswith("[XPASS(strict)]"),
    }
    context = getattr(report, "context", None)
    if context is not None and hasattr(context, "msg") and hasattr(context, "kwargs"):
        # Type tags distinguish older raw values such as integer 1 and string
        # "1". pytest 9 safe-repr strings remain stable under the same encoding.
        def encoded(value):
            kind = type(value)
            return {"type": kind.__module__ + "." + kind.__qualname__, "repr": repr(value)}

        description = {
            "msg": encoded(context.msg),
            "kwargs": {str(key): encoded(value) for key, value in context.kwargs.items()},
        }
        identity = json.dumps(description, sort_keys=True, separators=(",", ":"))
        data.update(id=report.nodeid + "::subtest[" + identity + "]", parent_id=report.nodeid, context=description)
        emit("subtest", **data)
    else:
        emit("phase", **data)


def pytest_sessionfinish(session, exitstatus):
    problems = getattr(session.config, "_rung_diagnostic_problems", [])
    if problems:
        session.exitstatus = 4
    emit("finish", exit_code=4 if problems else int(exitstatus), errors=problems)
