"""Teaching fixture for a rejected write, not a transactional persistence layer.

The contract tested here: when the writer rejects a value before writing, save
propagates that error and preserves both the old file and the cached value.
Partial writes, crashes and concurrent access are outside this example's scope.
"""

from __future__ import annotations

from pathlib import Path


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.value = path.read_text()

    def _write(self, value: str) -> None:
        self.path.write_text(value)

    def save(self, value: str) -> None:
        self._write(value)
        self.value = value


class FaultyStore(Store):
    """Deliberately faulty: a rejected write leaves the cache ahead of the file."""

    def save(self, value: str) -> None:
        self.value = value
        self._write(value)
