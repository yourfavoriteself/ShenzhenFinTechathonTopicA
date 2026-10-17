"""Memory of security-relevant events per client (ShadowMem-style), kept by the kernel only.

Agents can neither read nor change it. Before every action the kernel checks whether the payee,
account or website is linked to an earlier suspicious event, which catches two-step attacks:
"we changed our bank details" this week, a bill to the new details next week.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .cards import Risk
from .mandate import new_id

ACCOUNT_PREFIX = "acct:"     # payee token for raw account details not found in the merchant directory


class SuspiciousEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sus"))
    ts: datetime
    client_id: str
    kind: str              # details_change_request | injection | lookalike | urgency | unverified_payee | fake_bill
    summary: str
    impersonates: str | None = None
    account: str | None = None
    payee_token: str | None = None
    domain: str | None = None
    source: str


class SuspiciousMemory:
    def __init__(self):
        self.events: list[SuspiciousEvent] = []

    def record(self, **kw) -> SuspiciousEvent:
        e = SuspiciousEvent(**kw)
        self.events.append(e)
        return e

    def for_client(self, client_id: str) -> list[SuspiciousEvent]:
        return [e for e in self.events if e.client_id == client_id]

    def related(self, client_id: str, *, account: str | None = None, payee_token: str | None = None,
                domain: str | None = None, exclude_source: str | None = None) -> list[SuspiciousEvent]:
        out = []
        for e in self.for_client(client_id):
            if exclude_source and e.source == exclude_source:
                continue
            if (account and e.account == account) or (payee_token and e.payee_token == payee_token) \
                    or (domain and e.domain == domain):
                out.append(e)
        return out

    def check(self, ctx):
        """Pipeline risk check: a payee or website linked to a suspicious event is denied."""
        from .pipeline import StepResult, Verdict
        i = ctx.intent
        payee = i.val("payee")
        account = payee.removeprefix(ACCOUNT_PREFIX) if isinstance(payee, str) else None
        hits = self.related(i.client_id, payee_token=payee, account=account, domain=i.val("domain"))
        if not hits:
            return StepResult(step="")
        return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                          reasons=[f"linked to a suspicious event on {e.ts.date()}: {e.summary}" for e in hits],
                data={"suspicious_events": [e.id for e in hits]})
