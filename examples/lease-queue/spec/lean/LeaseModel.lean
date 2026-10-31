/-!
# Lease queue model

The intended behaviour of `leasequeue/queue.py`, small enough to prove things
about. `Main.lean` exposes `run` over a line protocol so Rung can check the
Python implementation against it on random action sequences.
-/

namespace LeaseModel

structure State where
  owner : Option Nat := none
  valid : Bool := false
  done : Bool := false
  result : Option Nat := none
  deriving Repr, DecidableEq

inductive Action where
  | expire
  | claim (w : Nat)
  | complete (w : Nat)
  deriving Repr, DecidableEq

def step (s : State) : Action → State
  | .expire => { s with valid := false }
  | .claim w =>
      if s.done = false ∧ (s.owner = none ∨ s.valid = false) then
        { s with owner := some w, valid := true }
      else s
  | .complete w =>
      if s.owner = some w ∧ s.valid = true ∧ s.done = false then
        { s with done := true, result := some w }
      else s

def run (actions : List Action) : State :=
  actions.foldl step {}

/-- Completing changes the state only for the worker holding a valid lease. -/
theorem complete_requires_lease (s : State) (w : Nat) (h : step s (.complete w) ≠ s) :
    s.owner = some w ∧ s.valid = true := by
  simp only [step] at h
  split at h
  · rename_i hg
    exact ⟨hg.1, hg.2.1⟩
  · exact absurd rfl h

/-- A recorded result always names the worker that holds the lease. -/
def Consistent (s : State) : Prop :=
  s.done = true → s.result = s.owner

theorem step_consistent (s : State) (a : Action) (h : Consistent s) :
    Consistent (step s a) := by
  unfold Consistent at *
  cases a with
  | expire => simpa [step] using h
  | claim w =>
      simp only [step]
      split
      · rename_i hg
        intro hd
        simp_all
      · exact h
  | complete w =>
      simp only [step]
      split
      · rename_i hg
        intro _
        simp [hg.1]
      · exact h

theorem run_consistent (actions : List Action) : Consistent (run actions) := by
  unfold run
  suffices ∀ s, Consistent s → Consistent (actions.foldl step s) by
    exact this {} (by simp [Consistent])
  induction actions with
  | nil => intro s h; exact h
  | cons a rest ih => intro s h; exact ih (step s a) (step_consistent s a h)

end LeaseModel
