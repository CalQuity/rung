"""A single-job lease queue, the kind found in schedulers and agent orchestrators.

A worker claims the job and holds a lease on it. Leases expire (a worker can
stall, lose its network or be paused), after which another worker may claim the
job. When a worker finishes, it completes the job and its result is recorded.

This module contains a deliberate bug that `rung check` finds three ways: see
`complete()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    owner: int | None = None  # worker holding the current lease
    lease_valid: bool = False
    done: bool = False
    result: int | None = None  # worker whose completion was recorded
    claimed_by: set[int] = field(default_factory=set)  # every worker that ever claimed


def claim(job: Job, worker: int) -> None:
    """Take the lease if the job is open and nobody holds a valid lease."""
    if not job.done and (job.owner is None or not job.lease_valid):
        job.owner = worker
        job.lease_valid = True
        job.claimed_by.add(worker)


def expire(job: Job) -> None:
    """The current lease runs out; its worker is not told."""
    job.lease_valid = False


def complete(job: Job, worker: int) -> None:
    """Record the worker's result.

    BUG: this checks that the worker *ever* claimed the job, not that it holds
    the current, valid lease. A worker whose lease expired (and whose job was
    re-claimed by someone else) can still complete it. The fix is:

        if job.owner == worker and job.lease_valid and not job.done:
    """
    if worker in job.claimed_by and not job.done:
        job.done = True
        job.result = worker
