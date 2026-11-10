# Test what survives a failed operation

Use this technique for failed writes, timeouts, retries, interrupted operations and
inconsistent state after an error. A useful failure test establishes the contract,
reaches the intended fault, and observes both the error and the resulting state.
This skill is agent guidance: installing it executes no experiments.

## Establish the contract

Read project instructions, the implementation, existing tests and the matching Rung
check. Use `rung plan --path <project-relative-path> --json` to identify checks and gaps.
Choose one operation and write down its preconditions, successful result, failure
response and permitted state after failure. Cite the contract's source. Ask the owner
when it is ambiguous; preserve approved laws and mark new laws as proposed.

## Build an isolated probe

Use the project's existing test tools and synthetic data in temporary storage, with
cleanup registered before the operation. Replace a specific dependency boundary with
a deterministic failure; avoid real outages, real credentials, production endpoints,
machine-wide permission changes and arbitrary sleeps. Explain missing tooling rather
than installing dependencies silently.

Every probe needs these observations:

1. **Passing control:** the same operation succeeds with a functioning dependency.
2. **Reached boundary:** a spy or mock confirms the intended call and arguments.
3. **Expected error:** assert the specific exception or error result; when appropriate,
   verify the injected exception's identity. An unrelated setup exception is not a reproduction.
4. **State afterward:** compare relevant memory, persisted data and externally visible
   state with the contract, including allowed pending states and subsequent recovery.
5. **Cleanup:** release patches, files and local services even if an assertion fails.

For a save whose contract preserves state on a rejected write, start with stored and
cached value `old`. First save `new` successfully and check both values. In a fresh
fixture, inject `PermissionError` at the writer, prove it was called with `new`, then
check the error and both values remain `old`. Updating the cache before the rejected
write exposes a mismatch. This models rejection before writing, not partial writes,
crash durability or concurrent access. Rung's source example `examples/failure-path/`
demonstrates this pattern with Python's standard library.

| Scenario | Observation needed |
| --- | --- |
| Request timeout | Exercise failure before acceptance and commit followed by a lost response; remote status may remain unknown. |
| Duplicate request or retry | Establish the documented idempotency contract, then assert its permitted effects and returned state. |
| Interrupted operation | Actually exercise the interruption boundary; an ordinary exception alone does not establish crash recovery. |

## Record evidence and prove fixes

Run the focused test and inspect its assertions and output. If no configured check
executes it, inspect `rung add command --help` and register a narrowly scoped command
without replacing existing checks. Run `rung check <check-id>` and read the verdict;
use `rung report --json` for the recorded result. A standalone test run is not a Rung
verdict. A setup failure or unavailable environment remains an unverified scenario.

For a fix, retain the regression test and use `rung prove '<test command>' --base <target>`
against an appropriate pre-fix revision. Verify the intended failure on the base and
success with the fix; missing dependencies on the base do not prove the defect.
Then run broader relevant checks and `rung check --changed`. Never weaken a test,
invariant or law, add a skip or suppression, or create a waiver to obtain a pass.

## Report precisely

Return: contract and source, source revision/local changes, safe fixture, injection
boundary, passing control, expected/actual error and state, exact commands, verdict
and limitations. The agent authors the probe; the runner executes it; Rung records
the result. A timeout alone does not justify rollback or immediate retry. Characterize
ambiguous behaviour as unresolved rather than inventing a failing requirement.

For a reproduced contract violation, use `rung-issue` to prepare the report. Follow its
privacy, destination, duplicate-check and publishing-authorization steps. Keep uncertain
observations local; installing skills and failing checks grant no publication permission.

Common mistakes: asserting only an exception; failing before the injected boundary;
assuming every failure requires rollback; presenting a local simulation as proof of
production atomicity; or reporting a defect before reproducing a contract violation.
