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

## A pass must mean something

- A spec that reaches one or two states checks nothing. Rung fails a spec that explores at most one.
- A counterexample is a finding. Report it; do not weaken the invariant, add a guard, or remove an
  action to make it go away. Only a person decides whether the code or the spec is wrong.
