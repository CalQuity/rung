"""Terminal output: colour when attached to a terminal, plain text otherwise, JSON on request."""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import unicodedata
from typing import Any, TextIO

from rung.core.redact import plain_text

_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "dim": "2", "bold": "1"}
VERDICT_STYLE = {"pass": ("PASS", "green"), "fail": ("FAIL", "red"), "error": ("ERROR", "yellow")}
STATE_STYLE = {
    "fresh": ("fresh", "green"),
    "stale": ("STALE", "yellow"),
    "failing": ("FAILING", "red"),
    "never": ("never run", "dim"),
    "drifted": ("DRIFTED", "yellow"),
    "held": ("HELD", "green"),
    "unproven": ("UNPROVEN", "yellow"),
    "broken": ("BROKEN", "red"),
    "proposed": ("PROPOSED", "blue"),
    "unenforced": ("UNENFORCED", "dim"),
}


def _write(text: str, stream: TextIO, *, flush: bool = False) -> None:
    encoding = stream.encoding or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        text = text.replace(" · ", " - ").encode(encoding, errors="backslashreplace").decode(encoding)
    print(text, file=stream, flush=flush)


def _cell_width(character: str) -> int:
    if unicodedata.category(character) in {"Mn", "Me", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def _width(text: str) -> int:
    return len(text) if text.isascii() else sum(map(_cell_width, text))


def _wrap(text: str, width: int, *, subsequent_indent: str = "") -> list[str]:
    if text.isascii():
        return textwrap.wrap(text, width=width, subsequent_indent=subsequent_indent, break_on_hyphens=False)
    lines = []
    current = text[: len(text) - len(text.lstrip())]
    used = len(current)
    for word in text.split():
        word_width = _width(word)
        gap = int(bool(current.strip()))
        if gap and used + gap + word_width > width:
            lines.append(current)
            current, used = subsequent_indent, len(subsequent_indent)
            gap = 0
        if gap:
            current += " "
            used += 1
        for char in word:
            cells = _cell_width(char)
            if used + cells > width and current.strip():
                lines.append(current)
                current, used = subsequent_indent, len(subsequent_indent)
            current += char
            used += cells
    if current.strip():
        lines.append(current)
    return lines


class Output:
    def __init__(self, *, json_mode: bool = False, color: bool | None = None) -> None:
        self.json_mode = json_mode
        self.interactive = sys.stdout.isatty() and not json_mode
        self.columns = shutil.get_terminal_size().columns if self.interactive else 0
        self.emitted = False
        if color is None:
            color = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.color = color

    def style(self, text: str, name: str) -> str:
        safe = plain_text(text)
        return f"\033[{_CODES[name]}m{safe}\033[0m" if self.color else safe

    def badge(self, key: str, table: dict[str, tuple[str, str]], width: int = 9) -> str:
        label, colour = table.get(key, (key, "dim"))
        return self.style(label.ljust(width), colour)

    def line(self, text: str = "") -> None:
        if not self.json_mode:
            for line in text.split("\n"):
                plain = plain_text(line)
                if self.columns and _width(plain) > self.columns:
                    indent = " " * min(len(plain) - len(plain.lstrip()), max(0, self.columns - 8))
                    for part in _wrap(plain, self.columns, subsequent_indent=indent):
                        _write(part, sys.stdout, flush=True)
                else:
                    _write(line if self.color else plain, sys.stdout, flush=self.interactive)

    def command(self, text: str, *, indent: int = 2, prefix: str = "") -> None:
        """Keep a suggested command on one logical line for copy and paste."""
        if self.json_mode:
            return
        if prefix and self.columns and indent + _width(prefix + text) > self.columns:
            self.detail(prefix.rstrip(), indent=indent)
            indent = min(indent + 2, max(0, self.columns - _width(text)))
            prefix = ""
        _write(" " * indent + prefix + self.style(text, "blue"), sys.stdout, flush=self.interactive)

    def location(self, path: str, *, label: str, indent: int = 6) -> None:
        """Wrap a displayed path between components and preserve the complete value in JSON."""
        if self.json_mode:
            return
        if not self.columns or indent + _width(label + path) <= self.columns:
            self.detail(label + path, indent=indent)
            return
        self.detail(label.rstrip(), indent=indent)
        indentation = min(indent + 2, max(0, self.columns - 8))
        width = self.columns - indentation
        parts = plain_text(path).split("/")
        line = ""
        for i, part in enumerate(parts):
            piece = part + ("/" if i < len(parts) - 1 else "")
            if line and _width(line + piece) > width:
                self.line(" " * indentation + line)
                line = ""
            if _width(piece) > width:
                left = min(indentation, max(0, self.columns - _width(piece)))
                self.line(" " * left + piece)
                continue
            line += piece
        if line:
            self.line(" " * indentation + line)

    def document(self, text: str) -> None:
        """Preserve document formatting while removing terminal commands."""
        if not self.json_mode:
            _write(plain_text(text).rstrip("\n"), sys.stdout)

    def heading(self, title: str, detail: str = "") -> None:
        self.line(self.style(title, "bold"))
        if detail:
            self.detail(detail, indent=2)
        self.line()

    def progress(self, text: str) -> None:
        if self.interactive and sys.stderr.isatty():
            for line in _wrap(text, max(1, self.columns)):
                _write(self.style(line, "dim"), sys.stderr, flush=True)

    def detail(self, text: str, indent: int = 6) -> None:
        if not self.json_mode:
            for part in plain_text(text).splitlines() or [""]:
                lines = [part]
                if self.columns:
                    width = max(1, self.columns - indent)
                    lines = _wrap(part, width) or [""]
                for line in lines:
                    self.line(" " * indent + line)

    def emit(self, payload: Any) -> None:
        """In JSON mode, print the command's result as one JSON document."""
        if self.json_mode:
            if isinstance(payload, dict):
                payload = {"schema_version": 1, **payload}
            print(json.dumps(payload, indent=2, default=str))
            self.emitted = True

    def error(self, text: str, *, code: int | None = None) -> None:
        if code is not None and not self.emitted:
            self.emit({"error": text, "exit_code": code})
        _write(f"rung: {text}", sys.stderr)
