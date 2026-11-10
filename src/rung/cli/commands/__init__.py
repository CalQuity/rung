"""Command implementations. Each takes parsed args and an Output, and returns an exit code."""

from rung.cli.commands.common import GATE_FAILED, OK
from rung.cli.commands.diagnostics import cmd_coverage, cmd_flaky
from rung.cli.commands.draft import cmd_draft
from rung.cli.commands.featuremap import cmd_map
from rung.cli.commands.formal import cmd_confirm, cmd_maintain
from rung.cli.commands.manage import cmd_add, cmd_attest, cmd_baseline, cmd_laws, cmd_waive
from rung.cli.commands.run import cmd_check, cmd_evidence, cmd_gate, cmd_ledger, cmd_prove, cmd_report, cmd_status
from rung.cli.commands.score import cmd_score_my_code
from rung.cli.commands.setup import cmd_agents, cmd_doctor, cmd_hook, cmd_init, cmd_kit, cmd_plan

__all__ = [
    "GATE_FAILED",
    "OK",
    "cmd_add",
    "cmd_agents",
    "cmd_attest",
    "cmd_baseline",
    "cmd_check",
    "cmd_confirm",
    "cmd_coverage",
    "cmd_doctor",
    "cmd_draft",
    "cmd_evidence",
    "cmd_flaky",
    "cmd_gate",
    "cmd_hook",
    "cmd_init",
    "cmd_kit",
    "cmd_laws",
    "cmd_ledger",
    "cmd_maintain",
    "cmd_map",
    "cmd_plan",
    "cmd_prove",
    "cmd_report",
    "cmd_score_my_code",
    "cmd_status",
    "cmd_waive",
]  # fmt: skip
