"""Interfaces the kernel uses to reach the bank, data and checks. Implementations are plugged in
by the sandbox bank and later pipeline stages; defaults here are minimal and in-memory."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class PayeeInfo(BaseModel):
    token: str
    name: str
    kind: str = "merchant"          # subscription | biller | merchant | person | own
    verified: bool = False          # present in the bank's merchant directory
    known_to_client: bool = False   # client paid this payee before
    usual_amount: int | None = None
    details_changed: bool = False
    domain: str | None = None


class Directory(Protocol):
    def payee(self, client_id: str, token: str) -> PayeeInfo | None: ...
    def is_blocked(self, token: str) -> bool: ...


class Funds(Protocol):
    def available(self, client_id: str, funding_source: str) -> int | None: ...
    def label(self, client_id: str, funding_source: str) -> str: ...


class Receipt(BaseModel):
    ok: bool
    reference: str = ""
    details: dict[str, Any] = {}


class Executor(Protocol):
    def execute(self, grant: Any, intent: Any) -> Receipt: ...


class InMemoryDirectory:
    def __init__(self, payees: list[PayeeInfo] | None = None, blocked: set[str] | None = None):
        self.payees = {p.token: p for p in payees or []}
        self.blocked = blocked or set()

    def payee(self, client_id: str, token: str) -> PayeeInfo | None:
        return self.payees.get(token)

    def is_blocked(self, token: str) -> bool:
        return token in self.blocked


class InMemoryFunds:
    def __init__(self, balances: dict[str, int] | None = None, labels: dict[str, str] | None = None):
        self.balances = balances or {}
        self.labels = labels or {}

    def available(self, client_id: str, funding_source: str) -> int | None:
        return self.balances.get(funding_source)

    def label(self, client_id: str, funding_source: str) -> str:
        return self.labels.get(funding_source, funding_source)
