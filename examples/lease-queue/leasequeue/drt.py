"""Line-protocol adapter for differential testing against the Lean model.

Each stdin line is a space-separated list of action codes:
    0 = expire, 1 + 2w = claim by worker w, 2 + 2w = complete by worker w.
Each stdout line is the final state, formatted exactly like the Lean model's
`render`: `owner=<w|-> valid=<0|1> done=<0|1> result=<w|->`.
"""

from __future__ import annotations

import sys

from leasequeue.queue import Job, claim, complete, expire


def apply(code: int, job: Job) -> None:
    if code == 0:
        expire(job)
    elif code % 2 == 1:
        claim(job, (code - 1) // 2)
    else:
        complete(job, (code - 2) // 2)


def render(job: Job) -> str:
    def opt(v: int | None) -> str:
        return "-" if v is None else str(v)

    return f"owner={opt(job.owner)} valid={int(job.lease_valid)} done={int(job.done)} result={opt(job.result)}"


def main() -> None:
    for line in sys.stdin:
        job = Job()
        for token in line.split():
            apply(int(token), job)
        print(render(job))


if __name__ == "__main__":
    main()
