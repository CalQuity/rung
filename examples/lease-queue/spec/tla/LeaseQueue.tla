---------------------------- MODULE LeaseQueue ----------------------------
(***************************************************************************)
(* A model of leasequeue/queue.py. Each action mirrors the guard the code  *)
(* actually checks, so TLC explores what the code can do, not what it      *)
(* should do. The invariants state what it should guarantee.               *)
(***************************************************************************)
EXTENDS FiniteSets

CONSTANTS Workers, NoOwner

VARIABLES owner, valid, done, result, claimedBy

vars == <<owner, valid, done, result, claimedBy>>

TypeOK ==
    /\ owner \in Workers \cup {NoOwner}
    /\ valid \in BOOLEAN
    /\ done \in BOOLEAN
    /\ result \in Workers \cup {NoOwner}
    /\ claimedBy \subseteq Workers

Init ==
    /\ owner = NoOwner
    /\ valid = FALSE
    /\ done = FALSE
    /\ result = NoOwner
    /\ claimedBy = {}

\* queue.claim(): `not job.done and (job.owner is None or not job.lease_valid)`
Claim(w) ==
    /\ ~done
    /\ owner = NoOwner \/ ~valid
    /\ owner' = w
    /\ valid' = TRUE
    /\ claimedBy' = claimedBy \cup {w}
    /\ UNCHANGED <<done, result>>

\* queue.expire(): the lease runs out; the worker is not told.
Expire ==
    /\ valid
    /\ valid' = FALSE
    /\ UNCHANGED <<owner, done, result, claimedBy>>

\* queue.complete(): `worker in job.claimed_by and not job.done`
Complete(w) ==
    /\ w \in claimedBy
    /\ ~done
    /\ done' = TRUE
    /\ result' = w
    /\ UNCHANGED <<owner, valid, claimedBy>>

\* Once the job is done nothing is enabled; stutter so TLC does not call it a deadlock.
Terminated ==
    /\ done
    /\ UNCHANGED vars

Next ==
    \/ Expire
    \/ \E w \in Workers : Claim(w) \/ Complete(w)
    \/ Terminated

Spec == Init /\ [][Next]_vars

\* The recorded result must come from the worker holding the lease.
ResultFromLeaseHolder == done => result = owner

=============================================================================
