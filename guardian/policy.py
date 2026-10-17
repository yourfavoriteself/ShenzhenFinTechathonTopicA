"""Purpose-Bound Money policy engine.

Every envelope is a policy, not just a balance: who may be paid from it, for what,
when, through which rail and up to which amount. A payment is allowed only if at least
one envelope's policy covers it. This is deterministic code: no LLM output can change it.
"""

from __future__ import annotations

from pydantic import BaseModel

from .ledger import Bank
from .models import Envelope, Payee, PaymentRequest
from .money import usd


class Blocklist:
    def __init__(self):
        self._items: dict[str, dict] = {}

    @staticmethod
    def norm(value: str) -> str:
        return value.replace(" ", "").replace("-", "").upper()

    def add(self, value: str, kind: str, source: str) -> None:
        self._items[self.norm(value)] = {"value": value, "kind": kind, "source": source}

    def hit(self, value: str) -> dict | None:
        return self._items.get(self.norm(value))

    def items(self) -> list[dict]:
        return list(self._items.values())


class EnvelopeCheck(BaseModel):
    envelope_id: str
    envelope_name: str
    ok: bool
    reasons: list[str]


class PolicyDecision(BaseModel):
    allowed: bool
    envelope_id: str | None = None
    envelope_name: str | None = None
    checks: list[EnvelopeCheck] = []
    blocked_by: str | None = None
    summary: str
    payee_verified: bool = False
    payee_new: bool = True


class PolicyEngine:
    def __init__(self, bank: Bank, blocklist: Blocklist):
        self.bank = bank
        self.blocklist = blocklist

    def resolve_payee(self, payee: Payee) -> tuple[Payee, bool]:
        """Trust only the bank's own payee record. A request cannot claim `verified=True`."""
        known = self.bank.find_payee_by_account(payee.account)
        if known:
            return known, self.bank.is_new_payee(known)
        return payee.model_copy(update={"verified": False}), True

    def check_envelope(self, env: Envelope, req: PaymentRequest, payee: Payee, is_new: bool) -> EnvelopeCheck:
        r = env.rules
        now = self.bank.clock.now()
        reasons: list[str] = []
        if r.locked_until and now < r.locked_until:
            reasons.append(f"locked until {r.locked_until.date()}")
        if r.allowed_payees is not None and payee.id not in r.allowed_payees:
            reasons.append("payee is not on this envelope's allow-list")
        if r.allowed_categories is not None and req.category not in r.allowed_categories:
            reasons.append(f"category '{req.category}' is not allowed")
        if r.allowed_rails is not None and req.rail not in r.allowed_rails:
            reasons.append(f"payment rail '{req.rail.value}' is not allowed")
        if r.max_per_tx is not None and req.amount > r.max_per_tx:
            reasons.append(f"{usd(req.amount)} exceeds the per-payment cap of {usd(r.max_per_tx)}")
        if r.day_window and not (r.day_window[0] <= now.day <= r.day_window[1]):
            reasons.append(f"only payable on days {r.day_window[0]}-{r.day_window[1]} of the month")
        if not r.allow_unverified_payees and not payee.verified:
            reasons.append("payee is not verified")
        if is_new and r.new_payee_cap is not None and req.amount > r.new_payee_cap:
            reasons.append(f"new payees are capped at {usd(r.new_payee_cap)}")
        if env.balance < req.amount:
            reasons.append(f"insufficient balance ({usd(env.balance)})")
        return EnvelopeCheck(envelope_id=env.id, envelope_name=env.name, ok=not reasons, reasons=reasons)

    def evaluate(self, req: PaymentRequest) -> PolicyDecision:
        payee, is_new = self.resolve_payee(req.payee)
        hit = self.blocklist.hit(payee.account)
        if hit:
            return PolicyDecision(allowed=False, blocked_by="blocklist", payee_verified=payee.verified,
                                  payee_new=is_new,
                                  summary=f"Blocked: account {payee.account} is a known fraud account ({hit['source']}).")
        envs = sorted(self.bank.envelopes.values(), key=lambda e: e.priority)
        if req.envelope_hint and req.envelope_hint in self.bank.envelopes:
            envs.sort(key=lambda e: e.id != req.envelope_hint)
        checks = [self.check_envelope(e, req, payee, is_new) for e in envs]
        for c in checks:
            if c.ok:
                return PolicyDecision(allowed=True, envelope_id=c.envelope_id, envelope_name=c.envelope_name,
                                      checks=checks, payee_verified=payee.verified, payee_new=is_new,
                                      summary=f"Covered by the '{c.envelope_name}' envelope policy.")
        return PolicyDecision(allowed=False, checks=checks, blocked_by="no_envelope", payee_verified=payee.verified,
                              payee_new=is_new,
                              summary="Blocked: no envelope policy allows this payment.")

    def worst_case_exposure(self) -> int:
        """Most money an unknown, unverified payee could receive in a single payment right now."""
        exposure = 0
        for env in self.bank.envelopes.values():
            r = env.rules
            if r.allowed_payees is not None or not r.allow_unverified_payees:
                continue
            if r.locked_until and self.bank.clock.now() < r.locked_until:
                continue
            caps = [env.balance] + [c for c in (r.max_per_tx, r.new_payee_cap) if c is not None]
            exposure = max(exposure, min(caps))
        return exposure
