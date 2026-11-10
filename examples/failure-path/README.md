# What happens when saving fails?

This example shows why checking only the successful path can miss a state bug.
It uses Python 3.11+ and the standard library; no application service is needed.

`Store` caches a value read from a file. Its example contract is explicit: if the
writer rejects a save before writing, the save raises the same error and both
the cached value and stored value remain unchanged. `FaultyStore` deliberately
updates its cache too early. Both implementations pass the successful-save test.

The failure test injects a specific `PermissionError` at the write boundary,
confirms that boundary was reached with the intended value, verifies the actual
exception, then checks memory and the file. The same assertions detect the
faulty implementation and pass for the corrected one. Tests also check that a
subsequent save works after rejection. All data lives in temporary directories;
cleanup and patch restoration run even when an assertion fails.

From Rung's repository root:

```bash
python -m unittest discover -s examples/failure-path -p 'test_*.py' -v
```

The suite reports **5 passing tests**. One test explicitly verifies that the
deliberately faulty implementation is detected by the cache-state assertion.
A green demonstration suite does not mean `FaultyStore` meets the contract.

## Record the demonstration with Rung

Copy this example directory to a disposable location first. From that copy:

```bash
rung init --no-drafts --no-agents
rung add command failure-path --run "python -m unittest discover -s . -p 'test_*.py' -v" --covers '*.py'
rung check failure-path
rung report
```

Use the Python executable available in your environment. The command check runs
the example suite; Rung records its verdict bound to `store.py` and `test_store.py`.
Changing either file makes that verdict stale. The initialization may also detect
other checks; the command above selects only `failure-path`.

To apply this technique in a real project, run `rung agents` there and ask Codex
or Claude Code to use `rung-failure-path` on a particular state-changing operation.
It is an agent skill, not a new CLI command. Review proposed tests and their scope.

## What this establishes

This is a deliberately constructed teaching example, not a discovered defect in
Rung or a historical regression proof. The agent authors a test, Python executes
it, and Rung records the configured check's outcome.

Moving the cache assignment after `_write` satisfies the rejection scenario.
`Path.write_text` is not transactional: this example makes no guarantee about
partial writes, crashes, durability or concurrent saves. A timeout in a remote
system is different again: it can occur after a successful commit. Establish
that operation's contract before requiring rollback or retrying it.
