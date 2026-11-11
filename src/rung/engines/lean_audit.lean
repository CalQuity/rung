import Lean

open Lean

-- Load declaration data only. Never enable imported initializers or extensions.
partial def axiomsOf (env : Environment) (name : Name)
    (seen : NameSet := {}) : Except String (NameSet × NameSet) := do
  if seen.contains name then return ({}, seen)
  let seen := seen.insert name
  let some info := env.find? name | throw s!"unknown declaration {name}"
  let mut axioms : NameSet := {}
  let mut visited := seen
  if let .axiomInfo _ := info then axioms := axioms.insert name
  let exprs := #[info.type] ++ (info.value?.toArray)
  for expr in exprs do
    for dep in expr.getUsedConstants do
      let (found, next) ← axiomsOf env dep visited
      axioms := found.foldl (fun acc n => acc.insert n) axioms
      visited := next
  return (axioms, visited)

def strings (s : String) : IO (Array String) := do
  let parsed ← IO.ofExcept (Json.parse s)
  IO.ofExcept (fromJson? parsed)

def main (args : List String) : IO Unit := do
  let [moduleArg, theoremArg, libraryArg] := args | throw (IO.userError "expected module and theorem lists")
  initSearchPath (← findSysroot)
  let libraries ← strings libraryArg
  searchPathRef.modify (fun paths => (libraries.toList.map System.FilePath.mk) ++ paths)
  let modules ← strings moduleArg
  let names ← strings theoremArg
  let env ← importModules (modules.map fun s => { module := s.toName }) {} (loadExts := false)
  let mut records := #[]
  for s in names do
    let name := s.toName
    let some (.thmInfo info) := env.find? name
      | throw (IO.userError s!"{s} is not a theorem")
    let (isProp, _, _) ← (Meta.isProp info.type).toIO
      { fileName := "rung-audit", fileMap := default } { env := env }
    let (axs, _) ← IO.ofExcept (axiomsOf env name)
    records := records.push (Json.mkObj [
      ("name", toJson s), ("kind", toJson "theorem"), ("isProp", toJson isProp),
      ("axioms", toJson (axs.toArray.map toString))])
  IO.println (Json.compress (toJson records))
