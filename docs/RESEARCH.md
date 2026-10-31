# Research notes

What Rung's design draws on, with sources. Claims marked *(secondary)* come from
write-ups rather than the primary source.

## Runtime verification for agents: Lauren Tan's pstack

- Thesis: agent throughput comes from trust, not from running more agents; without
  trust, more agents produce more bad pull requests. *(secondary: talk write-ups by
  [Redreamality](https://redreamality.com/blog/lauren-tan-poteto-trust-before-parallel/),
  [Flavio Copes](https://flaviocopes.com/pstack/) and
  [Atlasnote](https://atlasnote.ai/en/articles/poteto-pstack-ai-agent-verification))*
- `create-verification-skill` generates a project-local skill (launch, doctor, drive,
  evidence, cleanup) plus a feature map, and must execute end to end before handover:
  "A generated skill that was never executed is a draft, not a deliverable."
  ([source](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md))
- Her correction ladder, strongest first: codebase and architecture, static analysis,
  rules and skills, human review, verification skills. She describes formal methods
  (Lean, TLA+) as the far end and an open problem she has not taken on. *(secondary)*
- The pstack repository declares no license, so Rung uses none of its code.

## Verification-guided development: AWS Cedar

- Write a small executable model in Lean, prove properties of it, then differentially
  test the production Rust code against it on millions of random inputs; a release
  ships only with up-to-date models, proofs and tests.
  ([AWS](https://aws.amazon.com/blogs/opensource/lean-into-verified-software-development),
  [Lean case study](https://lean-lang.org/use-cases/cedar/),
  [Amazon Science](https://www.amazon.science/blog/how-we-built-cedar-with-automated-reasoning-and-differential-testing))
- Resonate uses the same pattern with a Lean protocol model.
  ([docs](https://docs.resonatehq.io/evaluate/how-resonate-is-tested))

## Lean tooling

- `#print axioms` reports which axioms a theorem depends on; `sorry` appears as
  `sorryAx` and `native_decide` as `Lean.ofReduceBool`. Rung's audit allows only
  `propext`, `Classical.choice` and `Quot.sound`.
- Lean 4.28 added `grind` extensions, a parallel `first_par` combinator and an external
  checker. ([release notes](https://lean-lang.org/doc/reference/stable/releases/v4.28.0/))
- [Plausible](https://reservoir.lean-lang.org/@leanprover-community/plausible) provides
  property testing inside Lean.
- [lean-lsp-mcp](https://github.com/project-numina/lean-lsp-mcp) (MIT) exposes Lean's
  language server to agents; [Aeneas](https://github.com/AeneasVerif/aeneas) translates
  Rust to Lean.
- Open provers exist (Goedel-Prover-V2, DeepSeek-Prover-V2), but on the vericoding
  benchmark off-the-shelf models verified 82% of Dafny tasks and 27% of Lean tasks.
  ([benchmark](https://popl26.sigplan.org/details/dafny-2026-papers/13/A-benchmark-for-vericoding-formally-verified-program-synthesis))
  Rung therefore uses Lean as the checker, not as the target of unaided model proof search.

## LLMs and TLA+

- TraceFix repairs LLM-generated PlusCal protocols with TLC counterexamples; 62.5% of
  48 tasks passed first time and none needed more than four repair rounds.
  ([paper](https://arxiv.org/abs/2605.07935))
- A June 2026 study of 30 models on 205 TLA+ specs catalogues five classes of
  hallucination. ([paper](https://arxiv.org/pdf/2606.05792))
- SysMoBench notes that a spec passing TLC does not show that it describes the system,
  the gap Rung's human approval of laws and planned trace validation address.

## Specification hacking

- Models trained with verifier rewards learn to exploit weak specifications.
  ([MIT thesis](https://arxiv.org/pdf/2605.30914))
- Lean4Agent models agent *workflows* in Lean with Hoare-style contracts; it assumes
  each LLM step meets its contract rather than verifying it, and its code is not yet
  released. ([paper](https://arxiv.org/html/2606.06523v2))

## Laws: Bend 2

- `LAWS.bend` declares rules; the compiler demands a proof in `PROOF.bend` whenever code
  changes. Proofs are explicit terms with no tactics; it applies to programs written in
  Bend. ([repository](https://github.com/bendlang/bend), Apache-2.0)

## Agent policy verification (adjacent, not Rung's focus)

Z3- and Lean-based checks of agent permissions already exist:
[NVIDIA OpenShell](https://nvidia.github.io/OpenShell-Research/dev-notes/posts/2026-09-10-learning-formal-methods-agent-policy-prover/),
[Google CEL verification](https://opensource.googleblog.com/2026/08/securing-the-agentic-era-introducing-formal-verification-for-cel.html).
Rung verifies the software agents write, not the agents' permissions.
