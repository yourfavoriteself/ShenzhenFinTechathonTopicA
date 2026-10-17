"""Simulated clock so scenarios can move through months deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


class SimClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 10, 17, 10, 0, tzinfo=MSK)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now
