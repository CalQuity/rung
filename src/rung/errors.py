"""Errors shared across Rung."""


class RungError(Exception):
    """Base class for errors Rung reports to the user without a traceback."""


class ToolMissing(RungError):
    """A required external tool (java, tla2tools.jar, lake, a scanner, a model CLI) is not installed."""
