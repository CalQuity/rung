# Example: a lease queue with a real bug

`leasequeue/queue.py` is a single-job lease queue. `complete()` checks that a
worker *ever* claimed the job instead of checking that it holds the current,
valid lease. Rung finds the bug three independent ways:

| Check | Kind | What it finds |
|---|---|---|
| `lease-protocol` | TLA+ | TLC finds the interleaving: claim(w1), expire, claim(w2), complete(w1). The result names w1 while w2 holds the lease. |
| `lease-model-proofs` | Lean | Passes. The Lean model of the *intended* behaviour proves that only the lease holder completes and that every reachable state is consistent. |
| `impl-matches-model` | Differential test | The Python code and the proven model disagree; Rung shrinks it to `3 0 4`: claim(w1), expire, complete(w1). A worker completes on its own expired lease, a case the TLA+ invariant does not cover. |

```bash
rung check            # lease-protocol and impl-matches-model fail
rung laws             # result-from-lease-holder is BROKEN
```

Apply the fix in the docstring of `complete()`:

```python
if job.owner == worker and job.lease_valid and not job.done:
```

```bash
rung check            # all three pass
rung laws             # both laws HOLD
echo "# touch" >> leasequeue/queue.py
rung status           # every verdict covering queue.py is STALE
```

Requires Java 11+, `tla2tools.jar` and Lean 4 (see `rung doctor`).
