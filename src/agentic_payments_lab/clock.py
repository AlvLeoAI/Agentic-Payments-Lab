"""Explicit clock injection makes fixtures and expiry tests reproducible."""

from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    def now(self) -> int: ...


@dataclass
class FixedClock:
    timestamp: int = 1_767_225_600  # 2026-01-01 UTC, simulated time.

    def now(self) -> int:
        return self.timestamp

    def advance(self, seconds: int) -> None:
        if seconds < 0:
            raise ValueError("Clock cannot move backwards")
        self.timestamp += seconds
