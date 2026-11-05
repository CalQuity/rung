"""Opt-in PR execution in a disposable Daytona sandbox."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from rung import __version__

BUNDLE_LIMIT = 100 * 1024 * 1024
RECEIPT_LIMIT = 1024 * 1024
# Per-stage limits, in seconds, for the calls made after the sandbox exists.
STAGE_TIMEOUTS = {"version": 15, "workspace": 15, "upload": 90, "checkout": 90, "gate": 900, "delete": 30}
CREATE_TIMEOUT = 90
# The sandbox outlives the slowest possible run by a margin, so it never expires while still in use.
TTL_MARGIN_MINUTES = 5
TTL_MINUTES = -(-(CREATE_TIMEOUT + sum(STAGE_TIMEOUTS.values())) // 60) + TTL_MARGIN_MINUTES

logger = logging.getLogger(__name__)


class SandboxError(ValueError):
    """An infrastructure error safe to print on the coordinator."""


@dataclass(frozen=True)
class GateRequest:
    repo: Path
    prefix: Path
    base: str
    head: str
    ids: tuple[str, ...]
    options: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    # Avoid handing the sandbox credential or Git tracing variables to child processes.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP"}
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=repo, env=env, check=True, capture_output=True, timeout=90,
        ).stdout.decode("utf-8", errors="replace")  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        raise SandboxError(f"Git {args[0]} failed while preparing the Daytona checkout") from None


def _receipt(response, request: GateRequest) -> dict:
    raw = response.result
    try:
        if len(raw) > RECEIPT_LIMIT:
            raise ValueError
        data = json.loads(raw)
        results = data["results"]
        stale = data["stale"]
        blocked = data["blocked"]
        if (
            data["schema_version"] != 1
            or data["base"] != request.base
            or data["head"] != request.head
            or data["selected"] != list(request.ids)
            or not isinstance(results, list)
            or not isinstance(stale, dict)
            or not isinstance(blocked, list)
            or any(not isinstance(reason, str) for reason in blocked)
            or any(
                key not in request.ids
                or not isinstance(paths, list)
                or any(not isinstance(path, str) for path in paths)
                for key, paths in stale.items()
            )
            or [row["id"] for row in results] != ([] if blocked else list(request.ids))
            or any(
                row["verdict"] not in {"pass", "fail", "error"} or not isinstance(row["summary"], str)
                for row in results
            )
        ):
            raise ValueError
        passed = not (blocked or stale) and all(row["verdict"] == "pass" for row in results)
        if data["passed"] is not passed or response.exit_code != (0 if passed else 1):
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SandboxError("Invalid Daytona gate receipt. Check the snapshot and its Rung version.") from None
    # Do not import artifacts, logs, paths to remote files, or the sandbox ledger.
    return {
        "passed": passed,
        "results": [{"id": row["id"], "verdict": row["verdict"], "summary": row["summary"][:300]} for row in results],
        "stale": {key: ["remote evidence changed during execution"] for key in stale},
        "blocked": ["remote policy preflight blocked the gate"] if blocked else [],
    }


def execute(request: GateRequest, snapshot: str) -> dict:
    key = os.environ.get("DAYTONA_API_KEY")
    if not key:
        raise SandboxError("set DAYTONA_API_KEY on the coordinator to use Daytona")
    try:
        sdk = import_module("daytona")
    except ImportError:
        raise SandboxError("install the optional SDK with `uv tool install . --with daytona==0.227.3`") from None
    if _git(request.repo, "rev-parse", "--is-shallow-repository").strip() != "false" or _git(
        request.repo, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise SandboxError("Daytona requires a clean, full checkout. Commit changes and fetch full Git history.")
    if _git(request.repo, "rev-parse", "HEAD").strip() != request.head:
        raise SandboxError("the checkout changed while preparing the Daytona gate")

    with tempfile.TemporaryDirectory(prefix="rung-daytona-") as temp:
        bundle = Path(temp) / "checkout.bundle"
        _git(request.repo, "bundle", "create", str(bundle), "HEAD")
        if bundle.stat().st_size > BUNDLE_LIMIT:
            raise SandboxError("the checkout bundle exceeds Daytona's 100 MiB transfer limit for this gate")
        stage = "resolving the snapshot"
        outcome: dict = {}
        try:
            client = sdk.Daytona(sdk.DaytonaConfig(api_key=key, api_url="https://app.daytona.io/api"))
            # Record exactly which snapshot ran the checks: a name can be re-pointed later.
            resolved = client.snapshot.get(snapshot)
            snapshot_id = str(resolved.id)
            stage = "creating the sandbox"
            sandbox = client.create(
                sdk.CreateSandboxFromSnapshotParams(
                    snapshot=snapshot,
                    ephemeral=True,
                    ttl_minutes=TTL_MINUTES,
                    public=False,
                    network_block_all=True,
                ),
                timeout=CREATE_TIMEOUT,
            )
            try:
                stage = "checking the snapshot"
                version = sandbox.process.exec("rung --version", timeout=STAGE_TIMEOUTS["version"])
                if version.exit_code or version.result.strip() != f"rung {__version__}":
                    raise SandboxError(f"the Daytona snapshot must have rung {__version__} on PATH")
                stage = "uploading Git history"
                work = f"/tmp/rung-{uuid4().hex}"
                remote_bundle = f"{work}/checkout.bundle"
                setup = sandbox.process.exec(
                    shlex.join(["mkdir", "-m", "700", work]), timeout=STAGE_TIMEOUTS["workspace"]
                )
                if setup.exit_code:
                    raise SandboxError("could not prepare the Daytona workspace")
                sandbox.fs.upload_file(str(bundle), remote_bundle, timeout=STAGE_TIMEOUTS["upload"])
                stage = "checking out the commit"
                repo = f"{work}/repo"
                clone = shlex.join(["git", "clone", "--no-checkout", "--template=", remote_bundle, repo])
                checkout = shlex.join(["git", "-C", repo, "checkout", "--detach", request.head])
                if sandbox.process.exec(f"{clone} && {checkout}", timeout=STAGE_TIMEOUTS["checkout"]).exit_code:
                    raise SandboxError("could not check out the exact PR revision in Daytona")
                stage = "running the remote gate"
                argv = [
                    "rung",
                    "-C",
                    str(Path(repo) / request.prefix),
                    "--json",
                    "gate",
                    "--base",
                    request.base,
                    *request.options,
                    "--",
                    *request.ids,
                ]
                response = sandbox.process.exec(shlex.join(argv), timeout=STAGE_TIMEOUTS["gate"])
                outcome.update(_receipt(response, request), snapshot=snapshot, snapshot_id=snapshot_id)
                return outcome
            finally:
                try:
                    client.delete(sandbox, timeout=STAGE_TIMEOUTS["delete"], wait=True)
                except Exception:
                    # A validated result stands; the sandbox still expires on its own. Never log SDK text.
                    warning = f"Daytona cleanup failed. The sandbox expires within {TTL_MINUTES} minutes."
                    logger.warning(warning)
                    outcome["cleanup_warning"] = warning
        except SandboxError:
            raise
        except Exception:
            # SDK exceptions may include headers, URLs, or captured output.
            raise SandboxError(f"Daytona failed while {stage}. Check credentials and snapshot availability.") from None
