"""Merchant directory: verified payment details and official domains, plus look-alike detection.

`ClientDirectory` answers the pipeline's question "who is this payee for this client": directory
facts (verified, official domain, details changed) joined with the client's own payment history.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime

from pydantic import BaseModel

from .ports import PayeeInfo
from .statements import Category, DataSource, Direction


class Merchant(BaseModel):
    token: str
    name: str
    kind: str                         # subscription | biller | merchant | employer | bank
    category: Category
    domain: str | None = None
    service: str | None = None        # music | video | cloud | news | games: overlap detection
    account: str = ""                 # settlement details, never shown to agents
    details_changed_at: datetime | None = None
    cancel_path: str | None = None
    aliases: list[str] = []


_CONFUSABLE = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("3", "e"), ("5", "s"), ("@", "a"), ("$", "s")]


def skeleton(s: str) -> str:
    s = s.lower()
    for a, b in _CONFUSABLE:
        s = s.replace(a, b)
    return re.sub(r"[^a-z]", "", s)


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def domain_stem(domain: str) -> str:
    return domain.lower().split(".")[0]


class MerchantDirectory:
    def __init__(self, merchants: list[Merchant], blocked: set[str] | None = None):
        self.by_token = {m.token: m for m in merchants}
        self.blocked = set(blocked or ())

    def get(self, token: str) -> Merchant | None:
        return self.by_token.get(token)

    def find_by_name(self, name: str) -> Merchant | None:
        n = name.strip().lower()
        for m in self.by_token.values():
            if m.name.lower() == n or n in (a.lower() for a in m.aliases):
                return m
        return None

    def find_by_account(self, account: str) -> Merchant | None:
        acc = re.sub(r"\s", "", account)
        return next((m for m in self.by_token.values() if m.account and m.account == acc), None)

    def official_domain(self, token: str) -> str | None:
        m = self.get(token)
        return m.domain if m else None

    def lookalike_of(self, name_or_domain: str) -> Merchant | None:
        """A directory merchant this text imitates (`Mosenerg0`, `streambox-cancel.help`), if any."""
        raw = name_or_domain.strip().lower()
        is_domain = "." in raw and " " not in raw
        for m in self.by_token.values():
            if is_domain:
                if m.domain and raw == m.domain:
                    return None
                if m.domain and skeleton(domain_stem(m.domain)) in skeleton(raw):
                    return m
                continue
            if raw == m.name.lower():
                return None
            a, b = skeleton(raw), skeleton(m.name)
            if a == b or (len(b) >= 6 and _distance(a, b) <= 1):
                return m
        return None


class ClientDirectory:
    """Implements the kernel's `Directory` port for one bank."""

    def __init__(self, merchants: MerchantDirectory, data: DataSource):
        self.merchants = merchants
        self.data = data

    def payee(self, client_id: str, token: str) -> PayeeInfo | None:
        m = self.merchants.get(token)
        paid = [t for t in self.data.transactions(client_id)
                if t.counterparty == token and t.direction == Direction.DEBIT]
        if m is None and not paid:
            return None
        usual = int(statistics.median(t.amount for t in paid[-3:])) if paid else None
        last = max((t.ts for t in paid), default=None)
        if m is None:
            return PayeeInfo(token=token, name=paid[-1].counterparty_name, kind="person", verified=False,
                             known_to_client=True, usual_amount=usual)
        changed = bool(m.details_changed_at and last and m.details_changed_at > last)
        return PayeeInfo(token=token, name=m.name, kind=m.kind, verified=True, known_to_client=bool(paid),
                         usual_amount=usual, details_changed=changed, domain=m.domain)

    def is_blocked(self, token: str) -> bool:
        return token in self.merchants.blocked
