"""Mask secret-looking values before command output is stored in the committed ledger.

Rung keeps the last lines of each check's output so a failure can be understood later. That
output can contain a token a test printed or a URL with credentials in it. This is a best-effort
filter for well-known shapes, not a guarantee: commands should not print secrets.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

# Each pattern has two groups: a prefix to keep, and the secret to mask.
_PATTERNS = [
    re.compile(r"(\b)(gh[pousr]_[A-Za-z0-9]{20,})"),  # GitHub tokens
    re.compile(r"(\b)(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(\b)(xox[abprs]-[A-Za-z0-9-]{10,})"),  # Slack
    re.compile(r"(\b)((?:AKIA|ASIA)[A-Z0-9]{16})\b"),  # AWS access key ids
    re.compile(r"(\b)(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,})"),  # OpenAI / Anthropic style keys
    re.compile(r"(\b)(AIza[0-9A-Za-z_-]{30,})"),  # Google API keys
    re.compile(r"(\b)(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),  # JWTs
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{16,})"),
    re.compile(r"(://)([^/\s:@]+:[^/\s@]+)(?=@)"),  # user:password@ in URLs
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)([^\s,;]{6,})"),
]


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + MASK, text)
    return text


def redact_lines(lines: list[str]) -> list[str]:
    return [redact(line) for line in lines]
