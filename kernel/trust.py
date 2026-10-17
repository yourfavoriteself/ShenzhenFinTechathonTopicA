"""Earned trust per kind of task, granted only by the kernel from verified facts.

Level 0: everything goes through a confirmation card. Level 1: the agent may act alone within
standing mandates up to a small limit for known payees. Level 2: up to the mandate limit.
Demotion is immediate, promotion is slow. Elevated protection (elderly, minors) pins external
payments to level 0.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from .clock import SimClock
from .mandate import Action


class Category(str, Enum):
    SUBSCRIPTIONS = "subscriptions"
    BILLS = "bills"
    OWN_TRANSFERS = "own_transfers"
    EXTERNAL_PAYMENTS = "external_payments"
    WEBSITES = "websites"


class TrustEvent(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    KERNEL_DENY = "kernel_deny"
    CRITIC_DENY = "critic_deny"
    FABRICATED_NUMBER = "fabricated_number"
    CLIENT_REVERSAL = "client_reversal"
    SCOPE_VIOLATION = "scope_violation"


LEVEL1_LIMIT = 100_000
PROMOTE_AFTER = 5
MAX_LEVEL = 2


class TrustChange(BaseModel):
    at: datetime
    category: Category
    event: TrustEvent
    level_before: int
    level_after: int


class CategoryTrust(BaseModel):
    level: int = 1
    streak: int = 0


class TrustLedger:
    def __init__(self, clock: SimClock, elevated_protection: bool = False):
        self._clock = clock
        self.elevated_protection = elevated_protection
        self.categories: dict[Category, CategoryTrust] = {c: CategoryTrust() for c in Category}
        self.history: list[TrustChange] = []

    def level(self, category: Category) -> int:
        if self.elevated_protection and category == Category.EXTERNAL_PAYMENTS:
            return 0
        return self.categories[category].level

    def auto_limit(self, category: Category, mandate_max: int) -> int:
        """Largest amount the agent may move alone in this category right now."""
        lvl = self.level(category)
        return 0 if lvl == 0 else (min(LEVEL1_LIMIT, mandate_max) if lvl == 1 else mandate_max)

    def record(self, category: Category, event: TrustEvent) -> int:
        ct = self.categories[category]
        before = ct.level
        if event == TrustEvent.VERIFIED_SUCCESS:
            ct.streak += 1
            if ct.streak >= PROMOTE_AFTER and ct.level < MAX_LEVEL:
                ct.level += 1
                ct.streak = 0
        elif event == TrustEvent.SCOPE_VIOLATION:
            ct.level, ct.streak = 0, 0
        else:
            ct.level, ct.streak = max(0, ct.level - 1), 0
        self.history.append(TrustChange(at=self._clock.now(), category=category, event=event,
                                        level_before=before, level_after=ct.level))
        return ct.level


def category_for(action: Action, payee_kind: str | None) -> Category:
    if action == Action.TRANSFER_OWN:
        return Category.OWN_TRANSFERS
    if action in (Action.CANCEL_SUBSCRIPTION, Action.BLOCK_MERCHANT):
        return Category.SUBSCRIPTIONS
    if action == Action.BROWSE:
        return Category.WEBSITES
    return {"subscription": Category.SUBSCRIPTIONS, "biller": Category.BILLS}.get(payee_kind or "",
                                                                                 Category.EXTERNAL_PAYMENTS)
