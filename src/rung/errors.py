"""Errors shared across Rung's runners."""


class ToolMissing(RuntimeError):
    """A required external tool (java, tla2tools.jar, lake, an LLM CLI) is not installed."""
