"""Domain models shared by all modules."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class Rail(str, Enum):
    INTERNAL = "internal"
    CARD = "card"
    DOMESTIC_TRANSFER = "domestic_transfer"
    INTERNATIONAL_TRANSFER = "international_transfer"
    CRYPTO = "crypto"
    GIFT_CARD = "gift_card"


# Share of a mistaken payment that can realistically be recovered on each rail.
REVERSIBILITY: dict[Rail, float] = {
    Rail.INTERNAL: 1.0,
    Rail.CARD: 0.8,
    Rail.DOMESTIC_TRANSFER: 0.3,
    Rail.INTERNATIONAL_TRANSFER: 0.05,
    Rail.CRYPTO: 0.0,
    Rail.GIFT_CARD: 0.0,
}


class Initiator(str, Enum):
    USER = "user"                      # the account owner, present in the app
    GUARDIAN = "guardian"              # Guardian acting autonomously (bills, budgeting)
    EXTERNAL_AGENT = "external_agent"  # a third-party AI agent holding a mandate token
    INBOUND_REQUEST = "inbound_request"  # money requested by someone else (message, call, invoice)


class Payee(BaseModel):
    id: str = Field(default_factory=lambda: new_id("payee"))
    name: str
    account: str
    country: str = "AU"
    category: str = "other"
    verified: bool = False
    first_seen: datetime | None = None


class EnvelopeRules(BaseModel):
    allowed_payees: list[str] | None = None       # payee ids; None = any payee
    allowed_categories: list[str] | None = None   # None = any category
    allowed_rails: list[Rail] | None = None
    max_per_tx: int | None = None
    day_window: tuple[int, int] | None = None     # inclusive day-of-month window
    locked_until: datetime | None = None
    allow_unverified_payees: bool = False
    new_payee_cap: int | None = None              # cap for payees seen < 7 days ago


class Envelope(BaseModel):
    id: str
    name: str
    purpose: str
    balance: int = 0
    rules: EnvelopeRules = Field(default_factory=EnvelopeRules)
    protected: bool = False  # adding payees requires out-of-band verification
    priority: int = 50       # lower = more specific, evaluated first


class TxStatus(str, Enum):
    EXECUTED = "executed"
    PENDING_CONFIRMATION = "pending_confirmation"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    REVERSED = "reversed"
    RELEASED = "released"


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tx"))
    ts: datetime
    kind: str  # deposit | allocation | payment | move | reversal | quarantine
    amount: int
    envelope_id: str | None = None
    to_envelope_id: str | None = None
    payee_id: str | None = None
    payee_name: str | None = None
    rail: Rail = Rail.INTERNAL
    status: TxStatus = TxStatus.EXECUTED
    memo: str = ""
    initiator: Initiator = Initiator.USER
    ref: str | None = None


class PaymentRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("req"))
    payee: Payee
    amount: int
    rail: Rail = Rail.DOMESTIC_TRANSFER
    category: str = "other"
    memo: str = ""
    initiator: Initiator = Initiator.USER
    source_message: str | None = None  # untrusted text that triggered the request, if any
    mandate_token: str | None = None
    envelope_hint: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
