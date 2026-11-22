# Rung

**Stop coding agents from faking green builds.**

Rung catches the shortcuts coding agents slip past CI—`@ts-ignore`, `# type: ignore`, skipped or deleted tests, hallucinated packages, and regression tests that pass even *without* the fix—and lets you climb an opt-in verification ladder up to mutation-tested properties, browser evidence recordings, TLA+, and Lean 4. Every verdict is produced by a deterministic tool, bound to the exact file bytes it checked, and expires the moment one byte changes.

![Rung catching agent shortcuts, a hallucinated package, and a fake regression test in one session](https://raw.githubusercontent.com/CalQuity/rung/main/docs/assets/first-run.gif)

<sub>Real terminal session: zero-config `rung check --changed` catches `# type: ignore`, a skipped test, and a hallucinated PyPI package without writing any files; `rung prove` rejects a weak regression test that passes on `main` and accepts a real one.</sub>

## Try it in 10 seconds (zero config, writes nothing)

Run Rung on any Git branch right now—no `rung.toml`, no account, no LLM key, no files written:

```bash
# Catch new agent shortcuts & hallucinated / <30-day packages on this branch
uvx rung-cli check --changed

# Prove your branch's bug-fix test actually fails on main and passes here
uvx rung-cli prove 'pytest tests/test_refunds.py'
```

Install permanently:

```bash
uv tool install rung-cli     # or: pipx install rung-cli
```

## Set up a repository in 2 seconds

```bash
cd your-repo
rung init                    # wires detected tests, linters, shortcuts, deps & agent hooks
rung check                   # runs checks and records byte-bound verdicts
rung kit                     # shows coverage gaps and the next command to run
```

`rung init` runs offline in under two seconds with zero LLM calls and zero external binary requirements. Add `--with-security` to also wire installed `gitleaks`, `osv-scanner`, and `zizmor`, or `--draft` to interactively draft deeper checks.

### With Claude Code or Codex

Install the Claude Code plugin to let your agent set up and drive Rung directly:

```text
/plugin marketplace add CalQuity/rung
/plugin install rung@rung
/rung:setup
```

Or install the setup skill for Codex and other `.agents/skills` clients:

```bash
mkdir -p ~/.agents/skills/rung-setup && curl -fsSL \
  https://raw.githubusercontent.com/CalQuity/rung/main/plugin/skills/setup/SKILL.md \
  | sed 's/^name: setup$/name: rung-setup/' > ~/.agents/skills/rung-setup/SKILL.md
```

`rung init` (or `rung agents --hooks` / `--codex-hooks`) installs agent skills, `AGENTS.md` rules, and a Stop hook that blocks an agent from declaring victory while checks covering its edits are stale or failing.

## Gate pull requests in CI

```yaml
# .github/workflows/rung.yml
name: rung
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
permissions:
  contents: read
  pull-requests: read
jobs:
  rung:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: CalQuity/rung@v0.6.1
```

`rung gate --base origin/main` runs the same merge gate locally or in any CI: it blocks unauthorized edits to policy files (`rung.toml`, `LAWS.md`, workflows, verifier scripts) *before* executing PR code, then reruns every required check fresh. See [docs/CI.md](docs/CI.md) for `[gate]` settings, approval labels, pre-commit, and sticky comments.

## Climb the verification ladder (opt-in)

Start on Rung 1 in seconds. Only climb higher on the files where bugs are expensive:

| Rung | Command | What it proves | Guide |
|---|---|---|---|
| **1. Anti-cheating & deps** | `rung check --changed` | Blocks new `@ts-ignore`, `# type: ignore`, `as any`, skipped/deleted tests, weakened configs, and hallucinated or <30d packages | Zero external tools |
| **2. Fix proofs** | `rung prove '<cmd>'` | Replays your branch's new/changed tests on the merge base (must fail) and branch (must pass), per test for pytest & Node | [docs/DESIGN.md](docs/DESIGN.md) |
| **3. Tests & linters** | `rung init` | Records your project's own test suites, type checkers, and linters in a byte-bound verdict ledger | [docs/CONFIG.md](docs/CONFIG.md) |
| **4. Property tests** | `rung draft property <file>` | Drafts Hypothesis or fast-check properties and keeps them only if they catch ≥50% of deliberate one-token code mutations | [docs/FORMAL.md](docs/FORMAL.md) |
| **5. Runtime evidence** | `rung map && rung draft runtime` | Drives your real UI/API through seeded scenarios and injected faults, saving screenshots, video, Playwright traces, HARs, and an HTML evidence page | [docs/RUNTIME.md](docs/RUNTIME.md) |
| **6. Formal models** | `rung draft tla\|lean <file>` | TLA+ (TLC + guard-removal mutation) for concurrency; Lean 4 (axiom-audited kernel proofs + differential testing) for core logic, with source-drift detection | [docs/FORMAL.md](docs/FORMAL.md) |

Try Rung on two worked examples with planted bugs: [`examples/acme-store`](examples/acme-store) (FastAPI + Next.js) and [`examples/lease-queue`](examples/lease-queue) (TLA+ + Lean 4 + differential testing).

## Core commands

| Command | Does |
|---|---|
| `rung check [ids] [--changed] [--base REF]` | Run checks and record byte-bound verdicts (works zero-config without `rung.toml`) |
| `rung prove '<test cmd>'` | Prove a bug-fix test fails on the merge base and passes here (works zero-config) |
| `rung init [--dry-run] [--with-security] [--draft]` | Wire up detected tests, linters, `shortcuts`, `deps`, and agent hooks |
| `rung kit` / `rung plan --changed` | Show verification gaps, check states, and what to run next |
| `rung gate --base origin/main` | Policy-checked merge gate for CI and local pre-merge checks |
| `rung status [--require-fresh]` / `rung report` | Fresh vs. stale verdicts, laws, and Markdown/JSON summary |
| `rung draft property\|runtime\|tla\|lean` | Model-drafted tests, drive scripts, or formal specs—accepted only by deterministic checkers |
| `rung maintain <check>` / `rung confirm <check>` | Keep TLA+ and Lean models synced when covered code changes |
| `rung doctor [--strict]` | Check configured executables and optional rung toolchains |

Every command accepts `-C DIR`, `--json`, and `--no-color`. Exit codes: `0` pass, `1` check/gate failed, `2` config/usage error, `3` missing prerequisite, `4` git error. Rung has **zero runtime dependencies** and collects **no telemetry**.

## Documentation

- [Configuration reference (`rung.toml`)](docs/CONFIG.md) · [Stack recipes](docs/RECIPES.md)
- [Runtime verification & feature maps](docs/RUNTIME.md) · [Property tests, TLA+, Lean & `LAWS.md`](docs/FORMAL.md)
- [CI, merge gates & pre-commit](docs/CI.md) · [Design & trust boundaries](docs/DESIGN.md) · [Contributing](CONTRIBUTING.md)

## Credits & License

Rung builds on ideas from Lauren Tan's [pstack](https://github.com/cursor/plugins/tree/main/pstack), AWS [Cedar](https://lean-lang.org/use-cases/cedar/), [Bend 2](https://github.com/bendlang/bend), [React Doctor](https://github.com/millionco/react-doctor), TraceFix, and open formal-methods skills ([tlaplus/AgentSkills](https://github.com/tlaplus/AgentSkills), [opum-ai/proof-skills](https://github.com/opum-ai/proof-skills), [leanprover/skills](https://github.com/leanprover/skills), [alok/vericoding-skill](https://github.com/alok/vericoding-skill)). Rung is not affiliated with or endorsed by these projects.

Licensed under [Apache License 2.0](LICENSE).
