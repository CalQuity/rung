"""`rung` command line: argument parsing and error handling. Commands live in `commands`."""

from __future__ import annotations

import argparse
import os
import sys

from rung import __version__
from rung.checks.presets import PRESETS
from rung.cli import commands as c
from rung.cli.output import Output
from rung.core.config import CHECK_KINDS, ConfigError
from rung.core.errors import RungError, ToolMissing
from rung.core.git import GitError
from rung.drafting import providers
from rung.engines.sarif import LEVELS

EXIT_USAGE, EXIT_TOOL, EXIT_GIT = 2, 3, 4

DESCRIPTION = """\
Rung builds a verification kit for your project and keeps a ledger of what it has
actually verified, against which exact code. The kit takes the best of linters,
tests, property tests, scripts that drive the running app, and formal models (TLA+,
Lean); laws in LAWS.md say what must never break.

start here: `rung init` builds the kit, `rung kit` shows it and the next command.

exit codes: 0 ok · 1 a check or gate failed · 2 configuration or usage error ·
3 a required tool is missing · 4 git error"""


def _common_options(*, defaults: bool) -> argparse.ArgumentParser:
    """Options accepted before or after the subcommand.

    The subcommand copies use SUPPRESS defaults so `rung -C dir check` is not
    overwritten by the subcommand's own default.
    """
    parser = argparse.ArgumentParser(add_help=False)
    suppress = not defaults
    parser.add_argument("-C", "--dir", help="project directory (default: current)",
                        default=argparse.SUPPRESS if suppress else ".")  # fmt: skip
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON",
                        default=argparse.SUPPRESS if suppress else False)  # fmt: skip
    parser.add_argument("--no-color", action="store_true", help="disable colour",
                        default=argparse.SUPPRESS if suppress else False)  # fmt: skip
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_options(defaults=False)
    p = argparse.ArgumentParser(
        prog="rung",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_common_options(defaults=True)],
    )
    p.add_argument("--version", action="version", version=f"rung {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help_text, description=help_text, parents=[common])
        s.set_defaults(func=func)
        return s

    s = add(
        "init", c.cmd_init, "build the project's verification kit: checks, agent skills, drafted scenarios and models"
    )
    s.add_argument("--provider", choices=list(providers.PROVIDERS))
    s.add_argument("--model", help="model for drafting, passed to the provider (e.g. sonnet, opus, gpt-6.1-sol); "
                   "empty uses the CLI's own default")  # fmt: skip
    s.add_argument(
        "--yes",
        action="store_true",
        help="draft every part a model can draft without asking (app scenarios, property tests, models)",
    )
    s.add_argument("--no-drafts", action="store_true", help="set up the checks and agent skills only; draft nothing")
    s.add_argument("--no-agents", action="store_true", help="do not install the agent skills and Stop hook")
    add("kit", c.cmd_kit, "show every part of the verification kit, how it stands, and the next command")
    add("plan", c.cmd_plan, "recommend checks for this repo")
    add("doctor", c.cmd_doctor, "show which checkers, scanners and model CLIs are available")
    s = add("agents", c.cmd_agents, "install the agent skill and AGENTS.md rules (and enforcement hooks)")
    s.add_argument("--hooks", action="store_true", help="also install a Claude Code Stop hook that blocks stale work")
    s = add("hook", c.cmd_hook, "entry point for agent hooks (reads the hook payload on stdin)")
    s.add_argument("event", choices=["stop"])

    s = add("check", c.cmd_check, "run checks and record verdicts")
    s.add_argument("ids", nargs="*", help="check ids (default: all)")
    s.add_argument("--changed", action="store_true", help="only checks touched by this branch's changes")
    s.add_argument("--base", help="branch to compare with (default: the repository's default branch)")
    s.add_argument("--ci", action="store_true", help="skip checks that are already fresh")
    s.add_argument("--seed", type=int, help="(evidence) run one seed only, to reproduce a failure; not recorded")
    s.add_argument("--scenario", action="append", metavar="NAME",
                   help="(evidence) run only this scenario of the drive script (repeatable); not recorded")  # fmt: skip
    s = add("status", c.cmd_status, "show which verdicts are fresh, stale or failing")
    s.add_argument("--ci", action="store_true", help="exit 1 unless every verdict is fresh")
    s = add("report", c.cmd_report, "summarize checks, laws, new findings and fix proofs")
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.add_argument("--evidence-url", help="where the evidence files can be downloaded (e.g. a CI artifact)")
    s = add("evidence", c.cmd_evidence, "write the HTML page of an evidence check's latest run")
    s.add_argument("check")
    s.add_argument("--seed", type=int, help="only this seed's run")
    s.add_argument("--out", help="directory to write index.html to (default: next to the run's files)")
    s = add("prove", c.cmd_prove, "show a new test fails without the fix and passes with it")
    s.add_argument("test_command", metavar="command", help="test command, e.g. 'pytest tests/test_refunds.py'")
    s.add_argument("--base", help="branch without the fix (default: the repository's default branch)")
    s.add_argument("--tests", nargs="*", help="test files to carry over (default: test files changed on this branch)")
    s.add_argument("--expect", help="text the failing run must contain, e.g. the assertion")
    s.add_argument("--cwd", default=".", help="directory to run the test command in (e.g. backend)")
    s = add("ledger", c.cmd_ledger, "check the verdict files for corruption")
    s.add_argument("--prune", action="store_true", help="remove verdicts of checks no longer in rung.toml")

    s = add("laws", c.cmd_laws, "list laws and whether each one holds")
    s.add_argument("--approve", metavar="LAW_ID", help="mark a proposed law as approved")
    s = add("add", c.cmd_add, "declare a check in rung.toml")
    s.add_argument("kind", choices=[k for k in CHECK_KINDS if k not in ("tla", "lean", "drt")])
    s.add_argument("id")
    s.add_argument("--run", help="command to run (command, custom scan; for evidence, omit it to attest instead)")
    s.add_argument("--cwd", help="directory to run the command in, relative to the project root")
    s.add_argument("--tool", choices=list(PRESETS), help="scanner preset (scan)")
    s.add_argument("--fail-on", choices=LEVELS, help="lowest SARIF level that blocks (scan)")
    s.add_argument("--require", nargs="*", help="artifact suffixes the evidence must include, e.g. .webm")
    s.add_argument("--min-age-days", type=int, default=30, help="youngest acceptable new package (deps)")
    s.add_argument("--covers", nargs="*", help="files whose change stales the verdict (globs)")
    s = add("attest", c.cmd_attest, "record evidence an agent or person captured (evidence checks without `run`)")
    s.add_argument("check")
    s.add_argument("files", nargs="+", help="recordings, screenshots, transcripts")
    s.add_argument("--by", required=True, help="who or what recorded it, e.g. claude-in-chrome, computer-use, codex")
    s.add_argument("--note", help="what was driven and what was seen")
    s = add("baseline", c.cmd_baseline, "accept a scan check's existing findings; only new ones will block")
    s.add_argument("check")
    s = add("waive", c.cmd_waive, "accept one finding, with a reason and an expiry")
    s.add_argument("check")
    s.add_argument("fingerprint")
    s.add_argument("--reason", required=True)
    s.add_argument("--days", type=int, default=30, help="days until the waiver expires (default 30)")
    s.add_argument("--until", help="expiry date, YYYY-MM-DD")
    s.add_argument("--approved-by", help="who accepts the risk (default: git user.name)")
    s = add("draft", c.cmd_draft, "draft a TLA+ spec, Lean model, property tests or drive script; a checker decides")
    s.add_argument("kind", choices=["tla", "lean", "property", "runtime"])
    s.add_argument("files", nargs="*", help="source files to model (tla, lean) or to focus on (runtime)")
    s.add_argument("--intent", help="what the code must guarantee, or (runtime) which feature to drive")
    s.add_argument("--require", nargs="*", help="(runtime) evidence the script must leave, e.g. .webm for a video")
    s.add_argument("--id")
    s.add_argument("--out", help="where to write the result: a directory (tla, lean) or a test file (property)")
    s.add_argument("--model", help="model for this draft only (default: [llm] model in rung.toml, else the CLI's)")

    # Formal models: keep a TLA+ or Lean model describing the code after the code changes.
    s = add("maintain", c.cmd_maintain, "update a TLA+ or Lean model after the code it describes changed")
    s.add_argument("check")
    s.add_argument("--model", help="model for this run only (default: [llm] model in rung.toml, else the CLI's)")
    s = add("confirm", c.cmd_confirm, "record that a person checked a TLA+ or Lean model still describes the code")
    s.add_argument("check")
    s.add_argument("--by", required=True, help="who reviewed the model")
    s.add_argument("--reason", required=True, help="what they compared, e.g. the change that was reviewed")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Output(json_mode=args.json, color=False if args.no_color else None)
    try:
        return args.func(args, out)
    except ConfigError as exc:
        out.error(str(exc))
        return EXIT_USAGE
    except ToolMissing as exc:
        out.error(str(exc))
        return EXIT_TOOL
    except GitError as exc:
        out.error(str(exc))
        return EXIT_GIT
    except RungError as exc:
        out.error(str(exc))
        return c.GATE_FAILED
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130
    except Exception as exc:  # a bug or an unexpected input: say so briefly, not with a traceback
        if os.environ.get("RUNG_DEBUG"):
            raise
        out.error(f"unexpected error: {type(exc).__name__}: {exc} (set RUNG_DEBUG=1 for the traceback)")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
