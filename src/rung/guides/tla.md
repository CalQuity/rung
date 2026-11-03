# Writing TLA+ that mirrors code

The goal is a spec that TLC can check in seconds and that fails exactly when the code is
wrong. A spec that passes because it says too little is worse than no spec.

## Syntax hygiene

- Plain ASCII operators only: `/\` `\/` `~` `\in` `\notin` `=>` `<=>` `#` `\cup` `\cap` `\subseteq`.
  Never `∧ ∨ ∈ →` and never `;`, Markdown fences or prose inside the module.
- Exactly one `---- MODULE Name ----` header and a closing `====`. The file name equals `Name.tla`.
- Write TLA+, not PlusCal, unless asked.
- Parenthesize an `IF` or `CASE` that sits inside a conjunction list.

## Structure

- `VARIABLES` plus `vars == << ... >>`, `Init`, a `Next` that is a disjunction of named actions,
  and `Spec == Init /\ [][Next]_vars`, plus fairness only if you check liveness.
- One action per operation in the code, each preceded by a comment naming the function and line:
  `\* Source: services/posting.py:250 post_queued`.
- Every action mentions every variable, either primed or in `UNCHANGED << ... >>`. Group variables
  per actor so `UNCHANGED` stays short. Never write `UNCHANGED <<>>` or a `Skip` action.
- Define `TypeOK` next to the variables, using `[S -> T]` for functions.

## Mirroring the code

- Model what the code **does**, including its bugs. Copy each guard from the code's real condition.
  The invariants say what it **should** do.
- One action per atomic region. In async or threaded code an `await`, a lock release, a network
  call or a database round trip ends a region, so a check-then-act across an `await` is **two**
  actions with a `pc` (program counter) per process. Merging them hides exactly the races TLC is for.
- A database function is code too. When the request includes its SQL (`create function ...`),
  model what it does: each statement it runs, the row locks (`select ... for update`) and advisory
  locks it takes, and where its transaction commits. The newest of several definitions is the one
  that runs. When no SQL is given for a call, model it as one atomic step and say so in `notes`.
- Model what the environment can do as nondeterministic actions: a service going offline, a
  timeout whose request actually succeeded, a retry, a duplicate delivery, a crash and restart.
- Use model values for identities (`Workers = {w1, w2}` in the config), not `1..N`.
- Keep it small: 2 or 3 processes, 2 or 3 items, counters bounded by a constant. Most bugs show up
  in short traces.

## The config file

- `SPECIFICATION Spec` (not `INIT`/`NEXT`), every `CONSTANT` assigned, and each invariant listed
  separately under `INVARIANTS` so TLC names the one that broke.
- If the system can legitimately stop, add a `Terminated` disjunct to `Next` that holds when the
  work is done and leaves `vars` unchanged. Otherwise TLC reports the end as a deadlock. Never
  silence deadlock checking blindly.
- Bound unbounded state with a constant or a `CONSTRAINT`, and say so in a comment.

## Properties

- Take invariants from the code's names, docstrings and comments, and from what the developer
  says it must guarantee. Write each as its own named definition.
- State predicates are `INVARIANT`s, not `PROPERTY []P`.
- Liveness (`<>`, `~>`) fails without fairness; add `WF_vars(A)` only for what the real scheduler
  guarantees.

## Plain words and scope

- Give every action a label a non-specialist understands ("a customer clicks Pay"), and every
  invariant a one-sentence statement and the harm if it breaks. Rung prints counterexamples in
  those words.
- State the scope: each bound (`Clicks = {c1, c2}`), each assumption ("each database call is
  atomic"), and what the spec leaves out. A pass holds only inside it.

## A pass must mean something

- A spec that reaches one or two states checks nothing. Rung fails a spec that explores at most one.
- Rung removes each action guard (a conjunct with no primed variable) in turn and reruns TLC. If no
  invariant notices any guard missing, the invariants are too weak: add the guarantee each guard
  exists for as an invariant. Never delete a guard to get past this.
- A counterexample is a finding. Report it; do not weaken the invariant, add a guard, or remove an
  action to make it go away. Only a person decides whether the code or the spec is wrong.

## When the code changes

- Rung records the commit the spec was confirmed against. When the covered code changes, the check
  is an error until `rung maintain <check>` updates the spec from the diff (or a person runs
  `rung confirm <check> --by NAME --reason TEXT`). Update the actions from the code; never touch an
  invariant unless the promise changed on purpose and you can quote where.
