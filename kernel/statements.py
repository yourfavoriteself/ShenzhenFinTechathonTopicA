"""What the kernel sees of the bank: typed statements of one client, never raw account numbers.

The sandbox bank stores its records in this format; analytics and budget code read only these types.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from .mandate import new_id


class Direction(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TxKind(str, Enum):
    CARD = "card"                  # client's card purchase
    RECURRING = "recurring"        # merchant-initiated charge on a card on file
    BILL = "bill"                  # bill payment from the bank app
    TRANSFER = "transfer"          # to another person
    OWN = "own"                    # between the client's own accounts
    SALARY = "salary"
    INCOMING = "incoming"
    REFUND = "refund"
    DISPUTE_CREDIT = "dispute_credit"


class Category(str, Enum):
    RENT = "rent"
    UTILITIES = "utilities"
    TELECOM = "telecom"
    SUBSCRIPTIONS = "subscriptions"
    GROCERIES = "groceries"
    CAFES = "cafes"
    ENTERTAINMENT = "entertainment"
    TRANSPORT = "transport"
    SHOPPING = "shopping"
    HEALTH = "health"
    SPORT = "sport"
    TRAVEL = "travel"
    INCOME = "income"
    SAVINGS = "savings"
    OTHER = "other"


MANDATORY = {Category.RENT, Category.UTILITIES, Category.TELECOM}
FLEXIBLE = {Category.GROCERIES, Category.CAFES, Category.ENTERTAINMENT, Category.TRANSPORT, Category.SHOPPING,
            Category.HEALTH, Category.SPORT}


class Tx(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tx"))
    ts: datetime
    client_id: str
    account_id: str
    card_id: str | None = None
    kind: TxKind
    direction: Direction
    amount: int                     # kopecks, always positive
    counterparty: str               # payee token
    counterparty_name: str
    category: Category
    memo: str = ""
    envelope_id: str | None = None
    status: str = "posted"          # posted | disputed | refunded
    initiator: str = "client"       # client | merchant | agent | bank
    ref: str | None = None

    @property
    def signed(self) -> int:
        return self.amount if self.direction == Direction.CREDIT else -self.amount


class CardOnFile(BaseModel):
    """A merchant's standing permission to charge a card (subscriptions, trials)."""
    id: str = Field(default_factory=lambda: new_id("cof"))
    client_id: str
    merchant: str
    card_id: str
    amount: int
    next_date: date
    day: int
    started: date
    trial_until: date | None = None
    active: bool = True


class Balance(BaseModel):
    id: str
    kind: str                       # current | savings | envelope
    name: str
    balance: int


class DataSource(Protocol):
    def transactions(self, client_id: str, since: datetime | None = None,
                     until: datetime | None = None) -> list[Tx]: ...
    def cards_on_file(self, client_id: str) -> list[CardOnFile]: ...
    def balances(self, client_id: str) -> list[Balance]: ...
