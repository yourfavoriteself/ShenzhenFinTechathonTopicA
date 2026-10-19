"""Sandbox bank: accounts, cards, envelopes, cards on file, merchant blocks, disputes, deferred actions.

It only moves money and keeps records; whether a movement is allowed is decided by the kernel.
Time is simulated: `run_until` replays salaries, merchant charges, dispute outcomes and deferred
actions day by day, so scenarios can jump weeks ahead.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from pydantic import BaseModel, Field

from kernel.clock import SimClock
from kernel.mandate import new_id
from kernel.merchants import MerchantDirectory
from kernel.money import usd
from kernel.statements import Balance, CardOnFile, Category, Direction, Tx, TxKind


class BankError(Exception):
    pass


class Declined(BankError):
    pass


class Account(BaseModel):
    id: str
    client_id: str
    kind: str                       # current | savings
    name: str
    balance: int = 0


class Card(BaseModel):
    id: str
    client_id: str
    account_id: str
    last4: str


class Envelope(BaseModel):
    id: str
    client_id: str
    account_id: str
    name: str
    categories: list[Category] = []
    balance: int = 0


class IncomeRule(BaseModel):
    client_id: str
    account_id: str
    payer: str
    payer_name: str
    day: int
    amount: int
    memo: str = "salary"


class Block(BaseModel):
    client_id: str
    merchant: str
    card_id: str | None = None      # None: all cards of the client
    since: datetime


class Dispute(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dsp"))
    client_id: str
    tx_id: str
    reason: str
    opened: datetime
    resolve_on: date
    status: str = "open"            # open | won | rejected
    credit_tx: str | None = None


class Scheduled(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sch"))
    client_id: str
    run_on: date
    kind: str                       # pay | transfer_own | block_merchant
    params: dict[str, Any]
    status: str = "pending"         # pending | done | failed | cancelled
    result: str | None = None


class DeclinedCharge(BaseModel):
    ts: datetime
    client_id: str
    merchant: str
    card_id: str
    amount: int
    reason: str


DAY_START = time(9, 0)
DUPLICATE_WINDOW = timedelta(minutes=10)
DISPUTE_DAYS = 3


class SandboxBank:
    def __init__(self, clock: SimClock, merchants: MerchantDirectory):
        self.clock = clock
        self.merchants = merchants
        self.accounts: dict[str, Account] = {}
        self.cards: dict[str, Card] = {}
        self.envelopes: dict[str, Envelope] = {}
        self.txs: list[Tx] = []
        self.cof: dict[str, CardOnFile] = {}
        self.incomes: list[IncomeRule] = []
        self.blocks: list[Block] = []
        self.disputes: dict[str, Dispute] = {}
        self.scheduled: dict[str, Scheduled] = {}
        self.declines: list[DeclinedCharge] = []
        self.processed_through: date = clock.now().date()
        self.listeners: list[Callable[[Tx], None]] = []

    # ------------------------------------------------------------------ setup

    def open_account(self, client_id: str, account_id: str, kind: str, name: str) -> Account:
        self.accounts[account_id] = Account(id=account_id, client_id=client_id, kind=kind, name=name)
        return self.accounts[account_id]

    def issue_card(self, client_id: str, card_id: str, account_id: str, last4: str) -> Card:
        self.cards[card_id] = Card(id=card_id, client_id=client_id, account_id=account_id, last4=last4)
        return self.cards[card_id]

    def add_card_on_file(self, client_id: str, merchant: str, card_id: str, amount: int, first_charge: date,
                         trial_until: date | None = None) -> CardOnFile:
        c = CardOnFile(client_id=client_id, merchant=merchant, card_id=card_id, amount=amount, day=first_charge.day,
                       next_date=first_charge, started=self.clock.now().date(), trial_until=trial_until)
        self.cof[c.id] = c
        return c

    def set_envelopes(self, client_id: str, account_id: str, envelopes: list[Envelope]) -> None:
        for e in [e for e in self.envelopes.values() if e.client_id == client_id]:
            del self.envelopes[e.id]
        for e in envelopes:
            self.envelopes[e.id] = e.model_copy(update={"client_id": client_id, "account_id": account_id})

    # ------------------------------------------------------------------ queries (kernel DataSource)

    def transactions(self, client_id: str, since: datetime | None = None, until: datetime | None = None) -> list[Tx]:
        return [t for t in self.txs if t.client_id == client_id and (since is None or t.ts >= since)
                and (until is None or t.ts < until)]

    def cards_on_file(self, client_id: str) -> list[CardOnFile]:
        return [c for c in self.cof.values() if c.client_id == client_id]

    def balances(self, client_id: str) -> list[Balance]:
        out = [Balance(id=a.id, kind=a.kind, name=a.name, balance=a.balance)
               for a in self.accounts.values() if a.client_id == client_id]
        out += [Balance(id=e.id, kind="envelope", name=e.name, balance=e.balance)
                for e in self.envelopes.values() if e.client_id == client_id]
        return out

    def tx(self, tx_id: str) -> Tx:
        for t in self.txs:
            if t.id == tx_id:
                return t
        raise BankError("unknown transaction")

    def unallocated(self, account_id: str) -> int:
        return self.accounts[account_id].balance - sum(e.balance for e in self.envelopes.values()
                                                       if e.account_id == account_id)

    def is_blocked(self, client_id: str, merchant: str, card_id: str | None) -> bool:
        return any(b.client_id == client_id and b.merchant == merchant and (b.card_id in (None, card_id))
                   for b in self.blocks)

    # kernel Funds port

    def available(self, client_id: str, funding_source: str) -> int | None:
        if (e := self.envelopes.get(funding_source)) and e.client_id == client_id:
            return e.balance
        if (c := self.cards.get(funding_source)) and c.client_id == client_id:
            return self.accounts[c.account_id].balance
        if (a := self.accounts.get(funding_source)) and a.client_id == client_id:
            return a.balance
        return None

    def owns(self, client_id: str, token: str) -> bool:
        for store in (self.accounts, self.envelopes, self.cards):
            if (x := store.get(token)) is not None and x.client_id == client_id:
                return True
        return False

    def label(self, client_id: str, funding_source: str) -> str:
        if e := self.envelopes.get(funding_source):
            return f'envelope "{e.name}"'
        if c := self.cards.get(funding_source):
            return f"card *{c.last4}"
        if a := self.accounts.get(funding_source):
            return a.name
        return funding_source

    # ------------------------------------------------------------------ money movement

    def _envelope_for(self, client_id: str, account_id: str, category: Category) -> Envelope | None:
        return next((e for e in self.envelopes.values() if e.client_id == client_id and e.account_id == account_id
                     and category in e.categories), None)

    def _post(self, tx: Tx) -> Tx:
        acc = self.accounts[tx.account_id]
        if tx.direction == Direction.DEBIT:
            if acc.balance < tx.amount:
                raise Declined(f"insufficient funds on {acc.name}")
            acc.balance -= tx.amount
            env = self.envelopes.get(tx.envelope_id) if tx.envelope_id else \
                self._envelope_for(tx.client_id, tx.account_id, tx.category)
            if env:
                env.balance -= tx.amount
                tx.envelope_id = env.id
        else:
            acc.balance += tx.amount
            if tx.envelope_id:
                self.envelopes[tx.envelope_id].balance += tx.amount
        self.txs.append(tx)
        for fn in self.listeners:
            fn(tx)
        return tx

    def _name(self, token: str) -> tuple[str, Category]:
        if m := self.merchants.get(token):
            return m.name, m.category
        prev = next((t for t in reversed(self.txs) if t.counterparty == token), None)
        return (prev.counterparty_name, prev.category) if prev else (token, Category.OTHER)

    def card_purchase(self, card_id: str, merchant: str, amount: int, *, name: str | None = None,
                      category: Category | None = None, memo: str = "") -> Tx:
        card = self.cards[card_id]
        n, c = self._name(merchant)
        return self._post(Tx(ts=self.clock.now(), client_id=card.client_id, account_id=card.account_id,
                             card_id=card_id, kind=TxKind.CARD, direction=Direction.DEBIT, amount=amount,
                             counterparty=merchant, counterparty_name=name or n, category=category or c, memo=memo))

    def merchant_charge(self, cof_id: str) -> Tx:
        c = self.cof[cof_id]
        card = self.cards[c.card_id]
        reason = None
        if not c.active:
            reason = "card on file cancelled"
        elif self.is_blocked(c.client_id, c.merchant, c.card_id):
            reason = "merchant blocked by the client"
        elif self.accounts[card.account_id].balance < c.amount:
            reason = "insufficient funds"
        if reason:
            self.declines.append(DeclinedCharge(ts=self.clock.now(), client_id=c.client_id, merchant=c.merchant,
                                                card_id=c.card_id, amount=c.amount, reason=reason))
            raise Declined(reason)
        n, cat = self._name(c.merchant)
        return self._post(Tx(ts=self.clock.now(), client_id=c.client_id, account_id=card.account_id,
                             card_id=c.card_id, kind=TxKind.RECURRING, direction=Direction.DEBIT, amount=c.amount,
                             counterparty=c.merchant, counterparty_name=n, category=cat, initiator="merchant",
                             memo=f"{n} subscription"))

    def credit(self, client_id: str, account_id: str, amount: int, payer: str, payer_name: str,
               kind: TxKind = TxKind.INCOMING, memo: str = "") -> Tx:
        return self._post(Tx(ts=self.clock.now(), client_id=client_id, account_id=account_id, kind=kind,
                             direction=Direction.CREDIT, amount=amount, counterparty=payer,
                             counterparty_name=payer_name, category=Category.INCOME, memo=memo, initiator="bank"))

    def _source(self, client_id: str, funding_source: str) -> tuple[Account, Envelope | None, str | None]:
        if (e := self.envelopes.get(funding_source)) and e.client_id == client_id:
            return self.accounts[e.account_id], e, None
        if (c := self.cards.get(funding_source)) and c.client_id == client_id:
            return self.accounts[c.account_id], None, c.id
        if (a := self.accounts.get(funding_source)) and a.client_id == client_id:
            return a, None, None
        raise BankError("unknown funding source")

    def pay(self, client_id: str, payee: str, amount: int, funding_source: str, *, memo: str = "",
            ref: str | None = None, initiator: str = "client", kind: TxKind | None = None,
            category: Category | None = None, payee_name: str | None = None) -> Tx:
        if amount <= 0:
            raise BankError("amount must be positive")
        acc, env, card_id = self._source(client_id, funding_source)
        if env is not None and env.balance < amount:
            raise Declined(f'not enough in envelope "{env.name}": {usd(env.balance)}')
        name, cat = self._name(payee)
        m = self.merchants.get(payee)
        if kind is None:
            kind = TxKind.BILL if m else TxKind.TRANSFER
        return self._post(Tx(ts=self.clock.now(), client_id=client_id, account_id=acc.id, card_id=card_id,
                             kind=kind, direction=Direction.DEBIT, amount=amount, counterparty=payee,
                             counterparty_name=payee_name or name, category=category or cat, memo=memo, ref=ref,
                             initiator=initiator, envelope_id=env.id if env else None))

    def transfer_own(self, client_id: str, src: str, dst: str, amount: int, *, initiator: str = "client",
                     memo: str = "") -> list[Tx]:
        s_acc, s_env, _ = self._source(client_id, src)
        d_acc, d_env, _ = self._source(client_id, dst)
        if s_env is not None and s_env.balance < amount:
            raise Declined(f'not enough in envelope "{s_env.name}"')
        if s_acc.id == d_acc.id:
            if s_env is None and self.unallocated(s_acc.id) < amount:
                raise Declined("not enough unallocated money")
            if s_env:
                s_env.balance -= amount
            if d_env:
                d_env.balance += amount
            return []
        now = self.clock.now()
        out = Tx(ts=now, client_id=client_id, account_id=s_acc.id, kind=TxKind.OWN, direction=Direction.DEBIT,
                 amount=amount, counterparty=d_acc.id, counterparty_name=d_acc.name, category=Category.SAVINGS,
                 memo=memo, initiator=initiator, envelope_id=s_env.id if s_env else None)
        if s_env is None and self.unallocated(s_acc.id) < amount and self._has_envelopes(s_acc.id):
            raise Declined("not enough unallocated money")
        self._post(out)
        inc = Tx(ts=now, client_id=client_id, account_id=d_acc.id, kind=TxKind.OWN, direction=Direction.CREDIT,
                 amount=amount, counterparty=s_acc.id, counterparty_name=s_acc.name, category=Category.SAVINGS,
                 memo=memo, initiator=initiator, envelope_id=d_env.id if d_env else None)
        return [out, self._post(inc)]

    def _has_envelopes(self, account_id: str) -> bool:
        return any(e.account_id == account_id for e in self.envelopes.values())

    def block_merchant(self, client_id: str, merchant: str, card_id: str | None = None) -> Block:
        b = Block(client_id=client_id, merchant=merchant, card_id=card_id, since=self.clock.now())
        self.blocks.append(b)
        return b

    def cancel_card_on_file(self, client_id: str, merchant: str, card_id: str | None = None) -> list[str]:
        """Merchant-side cancellation (what a successful cancel on the merchant's website does)."""
        done = []
        for c in self.cof.values():
            if c.client_id == client_id and c.merchant == merchant and c.active and card_id in (None, c.card_id):
                c.active = False
                done.append(c.id)
        return done

    def open_dispute(self, client_id: str, tx_id: str, reason: str) -> Dispute:
        t = self.tx(tx_id)
        if t.client_id != client_id or t.direction != Direction.DEBIT:
            raise BankError("only the client's own debits can be disputed")
        if t.status != "posted":
            raise BankError(f"transaction is already {t.status}")
        t.status = "disputed"
        d = Dispute(client_id=client_id, tx_id=tx_id, reason=reason, opened=self.clock.now(),
                    resolve_on=self.clock.now().date() + timedelta(days=DISPUTE_DAYS))
        self.disputes[d.id] = d
        return d

    def schedule(self, client_id: str, run_on: date, kind: str, params: dict[str, Any]) -> Scheduled:
        s = Scheduled(client_id=client_id, run_on=run_on, kind=kind, params=params)
        self.scheduled[s.id] = s
        return s

    # ------------------------------------------------------------------ time

    def run_until(self, t: datetime) -> None:
        day = self.processed_through + timedelta(days=1)
        while day <= t.date():
            self.clock.set(max(self.clock.now(), datetime.combine(day, DAY_START, tzinfo=t.tzinfo)))
            self._process_day(day)
            self.processed_through = day
            day += timedelta(days=1)
        if t > self.clock.now():
            self.clock.set(t)

    def advance(self, **kw) -> datetime:
        self.run_until(self.clock.now() + timedelta(**kw))
        return self.clock.now()

    def _process_day(self, day: date) -> None:
        for r in self.incomes:
            if r.day == day.day:
                self.credit(r.client_id, r.account_id, r.amount, r.payer, r.payer_name, TxKind.SALARY, r.memo)
        for c in list(self.cof.values()):
            if c.next_date == day:
                c.next_date = _next_month(day, c.day)
                if c.trial_until and day <= c.trial_until:
                    continue
                try:
                    self.merchant_charge(c.id)
                except Declined:
                    pass
        for d in self.disputes.values():
            if d.status == "open" and d.resolve_on <= day:
                self._resolve(d)
        for s in self.scheduled.values():
            if s.status == "pending" and s.run_on <= day:
                self._run_scheduled(s)

    def _resolve(self, d: Dispute) -> None:
        t = self.tx(d.tx_id)
        twin = any(o.id != t.id and o.counterparty == t.counterparty and o.amount == t.amount
                   and o.card_id == t.card_id and o.direction == Direction.DEBIT and o.status == "posted"
                   and abs(o.ts - t.ts) <= DUPLICATE_WINDOW for o in self.txs)
        if twin:
            credit = self._post(Tx(ts=self.clock.now(), client_id=t.client_id, account_id=t.account_id,
                                   card_id=t.card_id, kind=TxKind.DISPUTE_CREDIT, direction=Direction.CREDIT,
                                   amount=t.amount, counterparty=t.counterparty, counterparty_name=t.counterparty_name,
                                   category=t.category, memo=f"dispute {d.id}: duplicate charge refunded",
                                   initiator="bank", ref=t.id, envelope_id=t.envelope_id))
            d.status, d.credit_tx, t.status = "won", credit.id, "refunded"
        else:
            d.status, t.status = "rejected", "posted"

    def _run_scheduled(self, s: Scheduled) -> None:
        p = s.params
        try:
            if s.kind == "pay":
                r = self.pay(s.client_id, p["payee"], p["amount"], p["funding_source"], memo=p.get("memo", ""),
                             initiator="agent").id
            elif s.kind == "transfer_own":
                r = ",".join(t.id for t in self.transfer_own(s.client_id, p["src"], p["dst"], p["amount"],
                                                             initiator="agent"))
            elif s.kind == "block_merchant":
                self.block_merchant(s.client_id, p["merchant"], p.get("card_id"))
                r = "blocked"
            else:
                raise BankError(f"unknown scheduled action {s.kind}")
            s.status, s.result = "done", r
        except BankError as e:
            s.status, s.result = "failed", str(e)

    # ------------------------------------------------------------------ twin

    def twin(self) -> "SandboxBank":
        """Independent copy for shadow runs; listeners are not copied."""
        listeners, self.listeners = self.listeners, []
        try:
            t = copy.deepcopy(self)
        finally:
            self.listeners = listeners
        t.clock = SimClock(self.clock.now())
        return t


def _next_month(d: date, day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    for dd in (day, 30, 29, 28):
        try:
            return date(y, m, dd)
        except ValueError:
            continue
    raise AssertionError
