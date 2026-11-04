"""Settings read from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "acme.db"


@dataclass(frozen=True)
class Settings:
    database_path: Path
    payments_latency_ms: int
    payments_fail_rate: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=Path(os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH)),
            payments_latency_ms=int(os.environ.get("PAYMENTS_LATENCY_MS", "600")),
            payments_fail_rate=float(os.environ.get("PAYMENTS_FAIL_RATE", "0")),
        )
