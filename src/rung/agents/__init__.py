"""Make coding agents work inside the ladder instead of around it.

Two layers. Guidance (`install`): Claude Code skills and an AGENTS.md section saying how to use
Rung, how to write TLA+ and Lean, and which shortcuts are off limits. Enforcement (`hooks`): a
Claude Code `Stop` hook that keeps an agent from finishing while its edits are unverified.
Guidance can be ignored; the hook cannot.
"""

from rung.agents.hooks import HOOK_COMMAND, main_stop_hook, stop_hook
from rung.agents.install import BEGIN, END, install, install_stop_hook, install_verify_skill

__all__ = [
    "BEGIN", "END", "HOOK_COMMAND", "install", "install_stop_hook", "install_verify_skill", "main_stop_hook",
    "stop_hook",
]  # fmt: skip
