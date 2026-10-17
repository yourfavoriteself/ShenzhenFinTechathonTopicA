"""Simulated clock so scenarios can move through a month deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SimClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now
