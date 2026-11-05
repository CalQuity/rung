"""Make coding agents work inside the ladder instead of around it.

Two layers. Guidance (`install`): Claude Code skills and an AGENTS.md section saying how to use
Rung, how to write TLA+ and Lean, and which shortcuts are off limits. Enforcement (`hooks`): a
optional Claude Code and Codex Stop hooks that ask for another pass when evidence is stale.
"""

from rung.agents.hooks import HOOK_COMMAND, main_stop_hook, stop_hook
from rung.agents.install import BEGIN, END, install, install_stop_hook, install_verify_skill

__all__ = [
    "BEGIN", "END", "HOOK_COMMAND", "install", "install_stop_hook", "install_verify_skill", "main_stop_hook",
    "stop_hook",
]  # fmt: skip
