"""Earned trust, granted only by the kernel from verified facts, on two axes.

Per kind of task: level 0 is a card for everything; level 1 lets an agent act alone inside a
standing mandate up to a small limit; level 2 up to the mandate limit. Every kind starts at
level 1. Promotion is slow (5 verified results in a row), demotion is immediate.

Per agent (role, model and instruction hash, so a new prompt or model is a new agent): every
mistake is a strike, a scope violation is two. Three strikes put the agent in quarantine for
24 hours: its live passports are revoked and everything it proposes needs the client's card.
After that it is on probation (autonomy capped at level 1) until 5 verified results in a row.
Every 5 verified results in a row also clear one strike.

Elevated protection (elderly, minors) pins external payments to level 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from .clock import SimClock
from .mandate import Action
from .money import from_rub

if TYPE_CHECKING:
    from .passport import Passport


class Category(str, Enum):
    SUBSCRIPTIONS = "subscriptions"
    BILLS = "bills"
    OWN_TRANSFERS = "own_transfers"
    EXTERNAL_PAYMENTS = "external_payments"
    WEBSITES = "websites"
    INVESTMENTS = "investments"


class TrustEvent(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    KERNEL_DENY = "kernel_deny"
    CRITIC_DENY = "critic_deny"
    FABRICATED_NUMBER = "fabricated_number"
    CLIENT_REVERSAL = "client_reversal"
    SCOPE_VIOLATION = "scope_violation"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PROBATION = "probation"
    QUARANTINED = "quarantined"


LEVEL1_LIMIT = from_rub(1_000)
START_LEVEL = 1
PROMOTE_AFTER = 5
MAX_LEVEL = 2
STRIKE_LIMIT = 3
QUARANTINE = timedelta(hours=24)
CLEAN_RUN = 5
AGENT_CAP = {AgentStatus.ACTIVE: MAX_LEVEL, AgentStatus.PROBATION: 1, AgentStatus.QUARANTINED: 0}
EVENT_TEXT = {   # what the agent did, as the client reads it
    TrustEvent.VERIFIED_SUCCESS: "had its result verified by the bank",
    TrustEvent.KERNEL_DENY: "reported a result the bank could not confirm",
    TrustEvent.CRITIC_DENY: "proposed an action the critic rejected",
    TrustEvent.FABRICATED_NUMBER: "cited data that does not exist",
    TrustEvent.CLIENT_REVERSAL: "made a payment the client reversed",
    TrustEvent.SCOPE_VIOLATION: "tried to act outside the signed permission",
}


def agent_key(p: "Passport") -> str:
    return f"{p.role.value}:{p.model}:{p.instructions_hash[:8]}"


class TrustChange(BaseModel):
    at: datetime
    category: Category | None
    event: str
    level_before: int | None = None
    level_after: int | None = None
    agent: str | None = None
    passport_id: str | None = None
    strikes: int | None = None
    status_before: AgentStatus | None = None
    status_after: AgentStatus | None = None
    reason: str = ""


class CategoryTrust(BaseModel):
    level: int = START_LEVEL
    streak: int = 0


class AgentRecord(BaseModel):
    agent: str
    role: str
    model: str
    verified: int = 0
    penalties: int = 0
    strikes: int = 0
    clean: int = 0
    status: AgentStatus = AgentStatus.ACTIVE
    until: datetime | None = None


class TrustLedger:
    def __init__(self, clock: SimClock, elevated_protection: bool = False):
        self._clock = clock
        self.elevated_protection = elevated_protection
        self.categories: dict[Category, CategoryTrust] = {c: CategoryTrust() for c in Category}
        self.agents: dict[str, AgentRecord] = {}
        self.history: list[TrustChange] = []
        self.listeners: list[Callable[[TrustChange], None]] = []

    # ------------------------------------------------------------------ reading

    def agent(self, p: "Passport") -> AgentRecord:
        key = agent_key(p)
        rec = self.agents.get(key)
        if rec is None:
            rec = self.agents[key] = AgentRecord(agent=key, role=p.role.value, model=p.model)
        self._expire(rec)
        return rec

    def refresh(self) -> None:
        """Move agents whose quarantine is over to probation."""
        for rec in self.agents.values():
            self._expire(rec)

    def _expire(self, rec: AgentRecord) -> None:
        if rec.status == AgentStatus.QUARANTINED and rec.until and self._clock.now() >= rec.until:
            self._status(rec, AgentStatus.PROBATION, f"quarantine ended, probation until {CLEAN_RUN} verified "
                                                     "results in a row", None)

    def level(self, category: Category, agent: "Passport | None" = None) -> int:
        if self.elevated_protection and category == Category.EXTERNAL_PAYMENTS:
            return 0
        lvl = self.categories[category].level
        return min(lvl, AGENT_CAP[self.agent(agent).status]) if agent is not None else lvl

    def auto_limit(self, category: Category, mandate_max: int, agent: "Passport | None" = None) -> int:
        """Largest amount the agent may move alone in this category right now."""
        lvl = self.level(category, agent)
        return 0 if lvl == 0 else (min(LEVEL1_LIMIT, mandate_max) if lvl == 1 else mandate_max)

    # ------------------------------------------------------------------ writing

    def record(self, category: Category, event: TrustEvent, agent: "Passport | None" = None,
               reason: str = "") -> int:
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
        rec = self.agent(agent) if agent is not None else None
        pid = agent.id if agent is not None else None
        move = self._agent_event(rec, event) if rec is not None else None
        self._emit(TrustChange(at=self._clock.now(), category=category, event=event.value, level_before=before,
                               level_after=ct.level, agent=rec.agent if rec else None, passport_id=pid,
                               strikes=rec.strikes if rec else None, reason=reason or EVENT_TEXT[event]))
        if move is not None:
            self._status(rec, *move, pid)
        return ct.level

    def _agent_event(self, rec: AgentRecord, event: TrustEvent) -> tuple[AgentStatus, str] | None:
        """Update the agent's counters; return the status it should move to, if any."""
        if event == TrustEvent.VERIFIED_SUCCESS:
            rec.verified += 1
            rec.clean += 1
            if rec.clean % CLEAN_RUN == 0:
                if rec.strikes:
                    rec.strikes -= 1
                if rec.status == AgentStatus.PROBATION:
                    return AgentStatus.ACTIVE, f"{CLEAN_RUN} verified results in a row"
            return None
        rec.penalties += 1
        rec.clean = 0
        rec.strikes += 2 if event == TrustEvent.SCOPE_VIOLATION else 1
        if rec.status == AgentStatus.PROBATION:
            return AgentStatus.QUARANTINED, "a mistake on probation"
        if rec.status == AgentStatus.ACTIVE and rec.strikes >= STRIKE_LIMIT:
            return AgentStatus.QUARANTINED, f"{rec.strikes} strikes"
        return None

    def _status(self, rec: AgentRecord, status: AgentStatus, reason: str, passport_id: str | None) -> None:
        before, rec.status = rec.status, status
        if status == AgentStatus.PROBATION:
            rec.strikes = 0
        rec.until = self._clock.now() + QUARANTINE if status == AgentStatus.QUARANTINED else None
        self._emit(TrustChange(at=self._clock.now(), category=None, event=f"agent.{status.value}",
                               agent=rec.agent, passport_id=passport_id, strikes=rec.strikes,
                               status_before=before, status_after=status, reason=reason))

    def _emit(self, change: TrustChange) -> None:
        self.history.append(change)
        for fn in self.listeners:
            fn(change)


def category_for(action: Action, payee_kind: str | None) -> Category:
    if action == Action.TRANSFER_OWN:
        return Category.OWN_TRANSFERS
    if action in (Action.CANCEL_SUBSCRIPTION, Action.BLOCK_MERCHANT):
        return Category.SUBSCRIPTIONS
    if action == Action.BROWSE:
        return Category.WEBSITES
    if action == Action.INVEST:
        return Category.INVESTMENTS
    return {"subscription": Category.SUBSCRIPTIONS, "biller": Category.BILLS}.get(payee_kind or "",
                                                                                 Category.EXTERNAL_PAYMENTS)
