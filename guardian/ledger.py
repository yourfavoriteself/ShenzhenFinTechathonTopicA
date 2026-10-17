"""Mock bank: a ledger of purpose-bound envelopes, payees and transactions.

The bank only moves money. It does not decide whether a payment is allowed; that is
the job of the policy engine and the autonomy engine.
"""

from __future__ import annotations

import copy
from datetime import timedelta

from .clock import SimClock
from .models import Envelope, Initiator, Payee, Rail, Transaction, TxStatus


class LedgerError(Exception):
    pass


class Bank:
    def __init__(self, clock: SimClock, owner: str = "user"):
        self.clock = clock
        self.owner = owner
        self.envelopes: dict[str, Envelope] = {}
        self.unallocated = 0
        self.quarantine = 0
        self.payees: dict[str, Payee] = {}
        self.transactions: list[Transaction] = []

    # --- setup -----------------------------------------------------------
    def add_envelope(self, env: Envelope) -> Envelope:
        self.envelopes[env.id] = env
        return env

    def register_payee(self, payee: Payee) -> Payee:
        existing = self.find_payee_by_account(payee.account)
        if existing:
            return existing
        if payee.first_seen is None:
            payee.first_seen = self.clock.now()
        self.payees[payee.id] = payee
        return payee

    def find_payee_by_account(self, account: str) -> Payee | None:
        norm = account.replace(" ", "").upper()
        for p in self.payees.values():
            if p.account.replace(" ", "").upper() == norm:
                return p
        return None

    # --- queries ---------------------------------------------------------
    @property
    def total(self) -> int:
        return self.unallocated + self.quarantine + sum(e.balance for e in self.envelopes.values())

    def env(self, env_id: str) -> Envelope:
        if env_id not in self.envelopes:
            raise LedgerError(f"unknown envelope {env_id}")
        return self.envelopes[env_id]

    def is_new_payee(self, payee: Payee) -> bool:
        known = self.find_payee_by_account(payee.account)
        if not known:
            return True
        paid_before = any(t.payee_id == known.id and t.kind == "payment" and t.status == TxStatus.EXECUTED
                          for t in self.transactions)
        recent = known.first_seen is not None and self.clock.now() - known.first_seen < timedelta(days=7)
        return recent and not paid_before

    def payments_last_24h(self) -> int:
        since = self.clock.now() - timedelta(hours=24)
        return sum(1 for t in self.transactions if t.kind == "payment" and t.ts >= since)

    def tx(self, tx_id: str) -> Transaction:
        for t in self.transactions:
            if t.id == tx_id:
                return t
        raise LedgerError(f"unknown transaction {tx_id}")

    # --- money movement --------------------------------------------------
    def _record(self, **kw) -> Transaction:
        t = Transaction(ts=self.clock.now(), **kw)
        self.transactions.append(t)
        return t

    def deposit(self, amount: int, source: str, memo: str = "") -> Transaction:
        if amount <= 0:
            raise LedgerError("deposit must be positive")
        self.unallocated += amount
        return self._record(kind="deposit", amount=amount, payee_name=source, memo=memo, initiator=Initiator.USER)

    def allocate(self, env_id: str, amount: int, initiator: Initiator = Initiator.GUARDIAN) -> Transaction:
        if amount > self.unallocated:
            raise LedgerError("not enough unallocated funds")
        self.unallocated -= amount
        self.env(env_id).balance += amount
        return self._record(kind="allocation", amount=amount, to_envelope_id=env_id, initiator=initiator)

    def move(self, src: str, dst: str, amount: int, initiator: Initiator = Initiator.USER) -> Transaction:
        if self.env(src).balance < amount:
            raise LedgerError("insufficient envelope balance")
        self.env(src).balance -= amount
        self.env(dst).balance += amount
        return self._record(kind="move", amount=amount, envelope_id=src, to_envelope_id=dst, initiator=initiator)

    def pay(self, env_id: str, payee: Payee, amount: int, rail: Rail, memo: str,
            initiator: Initiator, ref: str | None = None) -> Transaction:
        env = self.env(env_id)
        if env.balance < amount:
            raise LedgerError(f"insufficient balance in {env.name}")
        payee = self.register_payee(payee)
        env.balance -= amount
        return self._record(kind="payment", amount=amount, envelope_id=env_id, payee_id=payee.id,
                            payee_name=payee.name, rail=rail, memo=memo, initiator=initiator, ref=ref)

    def quarantine_payment(self, env_id: str, payee: Payee, amount: int, rail: Rail, memo: str,
                           initiator: Initiator, ref: str | None = None) -> Transaction:
        """Move funds out of the envelope into quarantine. Nothing leaves the bank."""
        env = self.env(env_id)
        amount = min(amount, env.balance)
        env.balance -= amount
        self.quarantine += amount
        return self._record(kind="quarantine", amount=amount, envelope_id=env_id, payee_name=payee.name,
                            rail=rail, memo=memo, initiator=initiator, status=TxStatus.QUARANTINED, ref=ref)

    def release_quarantine(self, tx_id: str) -> Transaction:
        t = self.tx(tx_id)
        if t.status != TxStatus.QUARANTINED:
            raise LedgerError("transaction is not quarantined")
        self.quarantine -= t.amount
        self.env(t.envelope_id).balance += t.amount
        t.status = TxStatus.RELEASED
        return self._record(kind="release", amount=t.amount, to_envelope_id=t.envelope_id, ref=t.id,
                            initiator=Initiator.GUARDIAN, memo="funds returned from quarantine")

    def reverse(self, tx_id: str) -> Transaction:
        t = self.tx(tx_id)
        if t.kind != "payment" or t.status != TxStatus.EXECUTED:
            raise LedgerError("only executed payments can be reversed")
        if t.rail in (Rail.CRYPTO, Rail.GIFT_CARD):
            raise LedgerError("payment rail is irreversible")
        t.status = TxStatus.REVERSED
        self.env(t.envelope_id).balance += t.amount
        return self._record(kind="reversal", amount=t.amount, to_envelope_id=t.envelope_id, ref=t.id,
                            initiator=Initiator.USER, memo="reversal requested by user")

    # --- digital twin ----------------------------------------------------
    def twin(self) -> "Bank":
        """Deep copy used for shadow execution before touching real balances."""
        return copy.deepcopy(self)

    def snapshot(self) -> dict:
        return {
            "total": self.total,
            "unallocated": self.unallocated,
            "quarantine": self.quarantine,
            "envelopes": [{"id": e.id, "name": e.name, "balance": e.balance, "purpose": e.purpose,
                           "protected": e.protected} for e in self.envelopes.values()],
        }
