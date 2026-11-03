"""Test helpers for the Project Registry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)


class Clock:
    """Deterministic clock: returns ``now`` and can be moved by hand."""

    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now
