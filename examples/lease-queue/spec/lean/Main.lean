import LeaseModel

open LeaseModel

/-- Action codes: 0 = expire, 1 + 2w = claim by w, 2 + 2w = complete by w. -/
def decode (n : Nat) : Action :=
  if n = 0 then .expire
  else if n % 2 = 1 then .claim ((n - 1) / 2)
  else .complete ((n - 2) / 2)

def opt : Option Nat → String
  | none => "-"
  | some n => toString n

def bit (b : Bool) : String := if b then "1" else "0"

def render (s : State) : String :=
  s!"owner={opt s.owner} valid={bit s.valid} done={bit s.done} result={opt s.result}"

def parseLine (line : String) : List Action :=
  (line.splitOn " ").filterMap fun tok => tok.trimAscii.toString.toNat? |>.map decode

partial def loop (stdin : IO.FS.Stream) (stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then return
  stdout.putStrLn (render (run (parseLine line)))
  loop stdin stdout

def main : IO Unit := do
  let stdout ← IO.getStdout
  loop (← IO.getStdin) stdout
  stdout.flush
