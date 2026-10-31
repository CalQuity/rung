"""Terminal output: colour when attached to a terminal, plain text otherwise, JSON on request."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "dim": "2", "bold": "1"}
VERDICT_STYLE = {"pass": ("PASS", "green"), "fail": ("FAIL", "red"), "error": ("ERROR", "yellow")}
STATE_STYLE = {
    "fresh": ("fresh", "green"),
    "stale": ("STALE", "yellow"),
    "failing": ("FAILING", "red"),
    "never": ("never run", "dim"),
    "held": ("HELD", "green"),
    "unproven": ("UNPROVEN", "yellow"),
    "broken": ("BROKEN", "red"),
    "proposed": ("PROPOSED", "blue"),
    "unenforced": ("UNENFORCED", "dim"),
}


class Output:
    def __init__(self, *, json_mode: bool = False, color: bool | None = None) -> None:
        self.json_mode = json_mode
        if color is None:
            color = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.color = color

    def style(self, text: str, name: str) -> str:
        return f"\033[{_CODES[name]}m{text}\033[0m" if self.color else text

    def badge(self, key: str, table: dict[str, tuple[str, str]], width: int = 9) -> str:
        label, colour = table.get(key, (key, "dim"))
        return self.style(label.ljust(width), colour)

    def line(self, text: str = "") -> None:
        if not self.json_mode:
            print(text)

    def detail(self, text: str, indent: int = 6) -> None:
        if not self.json_mode:
            for part in text.splitlines() or [""]:
                print(" " * indent + part)

    def emit(self, payload: Any) -> None:
        """In JSON mode, print the command's result as one JSON document."""
        if self.json_mode:
            print(json.dumps(payload, indent=2, default=str))

    def error(self, text: str) -> None:
        print(f"rung: {text}", file=sys.stderr)
