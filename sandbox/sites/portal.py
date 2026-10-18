"""Sandbox merchant portal: the subscription accounts that live on the merchants' side.

Each subscription merchant with a website has an account per card on file: a login, a password
and a phone for one-time SMS codes. Signing in returns an SMS challenge; the code goes to the
client's phone (the demo shows it as a phone notification), never to the bank or an agent. A
cancellation deactivates the merchant's card on file in the sandbox bank and emails a
confirmation from the merchant's official domain.
"""

from __future__ import annotations

import random
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Callable

from pydantic import BaseModel, Field

from kernel.clock import SimClock
from kernel.mandate import new_id

SMS_TTL = timedelta(minutes=5)
CA = "Sandbox Trust CA"


class PortalError(Exception):
    pass


class PortalAccount(BaseModel):
    merchant: str
    name: str
    domain: str
    login: str
    password: str
    phone: str
    card_id: str
    card_number: str            # merchants keep the full number; the bank never passes it on
    price: int
    status: str = "active"
    cancelled_at: datetime | None = None


class Challenge(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sms"))
    domain: str
    login: str
    code: str
    exp: datetime
    used: bool = False


class Sms(BaseModel):
    ts: datetime
    to: str
    sender: str
    text: str


class MerchantPortal:
    def __init__(self, bank: Any, clock: SimClock, client_id: str, deliver: Callable[[dict], None] | None = None,
                 seed: int = 22):
        self.bank, self.clock, self.client_id = bank, clock, client_id
        self.deliver = deliver or (lambda doc: None)
        self.rnd = random.Random(seed)
        self.accounts: dict[tuple[str, str], PortalAccount] = {}
        self.challenges: dict[str, Challenge] = {}
        self.sessions: dict[str, tuple[str, str]] = {}
        self.sms: list[Sms] = []

    def add(self, acc: PortalAccount) -> PortalAccount:
        self.accounts[(acc.domain, acc.login)] = acc
        return acc

    def certificate(self, domain: str) -> dict[str, Any] | None:
        if not any(d == domain for d, _ in self.accounts):
            return None
        return {"subject": domain, "issuer": CA, "valid": True}

    # ------------------------------------------------------------------ sign-in

    def login(self, domain: str, login: str, password: str) -> dict[str, str]:
        acc = self.accounts.get((domain, login))
        if acc is None or not secrets.compare_digest(acc.password, password):
            raise PortalError("wrong login or password")
        ch = Challenge(domain=domain, login=login, code=f"{self.rnd.randrange(10**4):04d}",
                       exp=self.clock.now() + SMS_TTL)
        self.challenges[ch.id] = ch
        self.sms.append(Sms(ts=self.clock.now(), to=acc.phone, sender=acc.name,
                            text=f"{acc.name} sign-in code: {ch.code}. Do not share it."))
        return {"challenge": ch.id, "phone": acc.phone}

    def verify_sms(self, challenge: str, code: str) -> str:
        ch = self.challenges.get(challenge)
        if ch is None or ch.used or self.clock.now() > ch.exp:
            raise PortalError("the code has expired, sign in again")
        if not secrets.compare_digest(ch.code, code.strip()):
            raise PortalError("wrong code")
        ch.used = True
        token = secrets.token_urlsafe(16)
        self.sessions[token] = (ch.domain, ch.login)
        return token

    def _acc(self, session: str) -> PortalAccount:
        key = self.sessions.get(session)
        if key is None:
            raise PortalError("not signed in")
        return self.accounts[key]

    # ------------------------------------------------------------------ account

    def account(self, session: str) -> dict[str, Any]:
        a = self._acc(session)
        return {"merchant": a.merchant, "login": a.login, "status": a.status, "price": a.price,
                "card_number": a.card_number}

    def cancel(self, session: str) -> dict[str, Any]:
        a = self._acc(session)
        if a.status == "cancelled":
            return {"status": "cancelled", "already": True}
        a.status, a.cancelled_at = "cancelled", self.clock.now()
        self.bank.cancel_card_on_file(self.client_id, a.merchant, a.card_id)
        now = self.clock.now()
        until = _month_end(now.date())
        self.deliver({"id": new_id("doc"), "ts": now, "kind": "email", "sender": f"no-reply@{a.domain}",
                      "sender_domain": a.domain, "subject": f"Your {a.name} subscription is cancelled",
                      "body": f"Hello,\n\nYour {a.name} subscription ({a.login}) is cancelled. You will not be "
                              f"charged again. Access stays open until {until:%B %d, %Y}.\n\n{a.name} team"})
        return {"status": "cancelled", "access_until": until.isoformat()}


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


DEMO_LOGINS = {("tok_streambox", "card_1234"): ("anna@mail.example", "Sb-anna-2026"),
               ("tok_streambox", "card_5678"): ("anna.work@mail.example", "Sb-work-2026"),
               ("tok_melodify", "card_1234"): ("anna@mail.example", "Mel-anna-2026"),
               ("tok_cloudnest", "card_1234"): ("anna@mail.example", "Cn-anna-2026")}
DEMO_PHONE = "+7 *** ***-12-34"
CARD_NUMBERS = {"card_1234": "2200381000041234", "card_5678": "4427631000095678"}


def build_portal(world: Any, deliver: Callable[[dict], None] | None = None) -> MerchantPortal:
    """Portal accounts for the demo world's subscriptions that have a website and a demo login."""
    bank, client = world.bank, world.profile.client_id
    p = MerchantPortal(bank, world.clock, client, deliver)
    for c in bank.cards_on_file(client):
        creds = DEMO_LOGINS.get((c.merchant, c.card_id))
        m = world.merchants.get(c.merchant)
        if creds and m and m.domain:
            p.add(PortalAccount(merchant=c.merchant, name=m.name, domain=m.domain, login=creds[0],
                                password=creds[1], phone=DEMO_PHONE, card_id=c.card_id,
                                card_number=CARD_NUMBERS.get(c.card_id, ""), price=c.amount,
                                status="active" if c.active else "cancelled"))
    return p
