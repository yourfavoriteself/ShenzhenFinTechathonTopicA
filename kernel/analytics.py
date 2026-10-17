"""Deterministic analysis of a client's statements: recurring payments, leaks, double charges.

Every finding carries the transaction ids it is based on and a one-line calculation, so any number an
agent later quotes can be traced back (reference check).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable

from pydantic import BaseModel

from .merchants import MerchantDirectory
from .money import rub
from .statements import CardOnFile, Category, Direction, Tx, TxKind

MONTHLY_GAP = (25, 36)
ACTIVE_WITHIN = timedelta(days=40)
UNUSED_AFTER = timedelta(days=60)
TRIAL_WARN = timedelta(days=7)
PRICE_CHANGE_WINDOW = timedelta(days=120)
DOUBLE_WINDOW = timedelta(minutes=10)


class Series(BaseModel):
    merchant: str
    name: str
    card_id: str | None
    category: Category
    service: str | None = None
    kind: str                         # subscription | bill | transfer
    amount: int                       # latest amount
    median: int
    variable: bool
    previous_amount: int | None = None
    changed_on: date | None = None
    first: date | None = None
    last: date | None = None
    next_expected: date
    day: int
    count: int
    tx_ids: list[str]
    active: bool
    card_on_file: str | None = None
    trial_until: date | None = None


class Leak(BaseModel):
    kind: str                         # duplicate | overlap | unused | price_increase | trial_ending
    merchant: str
    name: str
    card_id: str | None = None
    monthly: int
    yearly: int
    counted: bool                     # part of the savings total (removable money)
    evidence: list[str]
    calc: str
    note: str
    synthetic_usage: bool = False
    related: list[str] = []


class LeakReport(BaseModel):
    leaks: list[Leak]
    monthly_total: int
    yearly_total: int
    calc: str


class DoubleCharge(BaseModel):
    first: str
    second: str
    merchant: str
    name: str
    amount: int
    card_id: str | None
    seconds_apart: int


class Usage(BaseModel):
    merchant: str
    last_used: date
    sessions_30d: int
    synthetic: bool = True


def _next_month(d: date, day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    for dd in (day, 30, 29, 28):
        try:
            return date(y, m, dd)
        except ValueError:
            continue
    raise AssertionError


def debits(txs: Iterable[Tx]) -> list[Tx]:
    return [t for t in txs if t.direction == Direction.DEBIT and t.kind != TxKind.OWN]


def recurring(txs: list[Tx], cards_on_file: list[CardOnFile], merchants: MerchantDirectory,
              as_of: datetime) -> list[Series]:
    groups: dict[tuple[str, str | None], list[Tx]] = defaultdict(list)
    for t in debits(txs):
        if t.status == "refunded":
            continue
        groups[(t.counterparty, t.card_id if t.kind == TxKind.RECURRING else None)].append(t)
    cof = {(c.merchant, c.card_id): c for c in cards_on_file}
    out: list[Series] = []
    seen_cof: set[str] = set()
    for (merchant, card), ts in groups.items():
        ts.sort(key=lambda t: t.ts)
        if len(ts) < 3:
            continue
        gaps = [(b.ts - a.ts).days for a, b in zip(ts, ts[1:])]
        if not (MONTHLY_GAP[0] <= statistics.median(gaps) <= MONTHLY_GAP[1]):
            continue
        amounts = [t.amount for t in ts]
        med = int(statistics.median(amounts))
        variable = (max(amounts) - min(amounts)) > 0.1 * med and len(set(amounts[-3:])) > 1
        prev, changed = None, None
        if not variable:
            for a, b in zip(ts, ts[1:]):
                if b.amount != a.amount:
                    prev, changed = a.amount, b.ts.date()
        c = cof.get((merchant, card)) if card else None
        if c:
            seen_cof.add(c.id)
        day = c.day if c else int(statistics.median(t.ts.day for t in ts[-3:]))
        m = merchants.get(merchant)
        kind = "subscription" if ts[-1].kind == TxKind.RECURRING else ("transfer" if m is None else "bill")
        last = ts[-1].ts.date()
        nxt = c.next_date if c else _next_month(last, day)
        active = (as_of - ts[-1].ts) <= ACTIVE_WITHIN and (c is None or c.active)
        out.append(Series(merchant=merchant, name=ts[-1].counterparty_name, card_id=card, category=ts[-1].category,
                          service=m.service if m else None, kind=kind, amount=ts[-1].amount, median=med,
                          variable=variable, previous_amount=prev, changed_on=changed, first=ts[0].ts.date(),
                          last=last, next_expected=nxt, day=day, count=len(ts), tx_ids=[t.id for t in ts],
                          active=active, card_on_file=c.id if c else None))
    for c in cards_on_file:
        if c.id in seen_cof or not c.active:
            continue
        m = merchants.get(c.merchant)
        out.append(Series(merchant=c.merchant, name=m.name if m else c.merchant, card_id=c.card_id,
                          category=m.category if m else Category.SUBSCRIPTIONS, service=m.service if m else None,
                          kind="subscription", amount=c.amount, median=c.amount, variable=False,
                          next_expected=c.next_date, day=c.day, count=0, tx_ids=[], active=True,
                          card_on_file=c.id, trial_until=c.trial_until))
    return sorted(out, key=lambda s: (s.category.value, s.name, s.card_id or ""))


def leaks(series: list[Series], usage: list[Usage], as_of: datetime) -> LeakReport:
    subs = [s for s in series if s.active and s.kind == "subscription"]
    used = {u.merchant: u for u in usage}
    found: list[Leak] = []
    flagged: set[tuple[str, str | None]] = set()

    def add(kind, s: Series, monthly, counted, calc, note, synthetic=False, related=()):
        found.append(Leak(kind=kind, merchant=s.merchant, name=s.name, card_id=s.card_id, monthly=monthly,
                          yearly=monthly * 12, counted=counted, evidence=list(s.tx_ids), calc=calc, note=note,
                          synthetic_usage=synthetic, related=list(related)))
        if counted:
            flagged.add((s.merchant, s.card_id))

    by_merchant: dict[str, list[Series]] = defaultdict(list)
    for s in subs:
        by_merchant[s.merchant].append(s)
    for merchant, ss in by_merchant.items():
        if len(ss) > 1:
            ss.sort(key=lambda s: s.first or s.next_expected)
            for dup in ss[1:]:
                cards = ", ".join(f"*{x.card_id[-4:]}" for x in ss if x.card_id)
                add("duplicate", dup, dup.amount, True,
                    f"{dup.name} is charged on {len(ss)} cards ({cards}): {rub(dup.amount)} a month extra",
                    f"second {dup.name} subscription on card *{dup.card_id[-4:]} since {dup.first}",
                    related=[x for o in ss if o is not dup for x in o.tx_ids])

    by_service: dict[str, list[Series]] = defaultdict(list)
    for s in subs:
        if s.service and (s.merchant, s.card_id) not in flagged and s.count > 0:
            by_service[s.service].append(s)
    for service, ss in by_service.items():
        names = {s.merchant for s in ss}
        if len(names) < 2:
            continue
        keep = max(ss, key=lambda s: (used[s.merchant].sessions_30d if s.merchant in used else 0, -s.amount))
        for s in ss:
            if s.merchant == keep.merchant:
                continue
            u = used.get(s.merchant)
            usage_note = f", used {u.sessions_30d} times in 30 days vs {used[keep.merchant].sessions_30d}" \
                if u and keep.merchant in used else ""
            add("overlap", s, s.amount, True,
                f"two {service} services: {keep.name} {rub(keep.amount)} and {s.name} {rub(s.amount)}; "
                f"dropping {s.name} saves {rub(s.amount)} a month",
                f"overlaps with {keep.name}{usage_note}", synthetic=bool(u), related=keep.tx_ids)

    for s in subs:
        u = used.get(s.merchant)
        if (s.merchant, s.card_id) in flagged or u is None or s.count == 0:
            continue
        if as_of.date() - u.last_used > UNUSED_AFTER:
            add("unused", s, s.amount, True,
                f"{s.name}: {rub(s.amount)} a month, last used {u.last_used} "
                f"({(as_of.date() - u.last_used).days} days ago)",
                "not used for more than 60 days (synthetic usage data)", synthetic=True)

    for s in subs:
        if s.previous_amount and s.changed_on and s.amount > s.previous_amount \
                and as_of.date() - s.changed_on <= PRICE_CHANGE_WINDOW:
            diff = s.amount - s.previous_amount
            add("price_increase", s, diff, False,
                f"{s.name}: {rub(s.previous_amount)} -> {rub(s.amount)} since {s.changed_on} (+{rub(diff)} a month)",
                "price went up")

    for s in subs:
        if s.trial_until and s.count == 0 and s.next_expected - as_of.date() <= TRIAL_WARN:
            u = used.get(s.merchant)
            note = f"free trial ends {s.trial_until}; first charge {rub(s.amount)} on {s.next_expected}"
            if u:
                note += f"; used {u.sessions_30d} times in 30 days (synthetic usage data)"
            add("trial_ending", s, s.amount, False, f"{s.name}: {rub(s.amount)} a month from {s.next_expected}",
                note, synthetic=bool(u))

    counted = [x for x in found if x.counted]
    total = sum(x.monthly for x in counted)
    calc = " + ".join(f"{x.name} {rub(x.monthly)}" for x in counted) + f" = {rub(total)} a month"
    return LeakReport(leaks=found, monthly_total=total, yearly_total=total * 12, calc=calc)


def double_charges(txs: list[Tx]) -> list[DoubleCharge]:
    cand = sorted((t for t in debits(txs) if t.kind == TxKind.CARD and t.status == "posted"), key=lambda t: t.ts)
    out: list[DoubleCharge] = []
    used: set[str] = set()
    for i, a in enumerate(cand):
        if a.id in used:
            continue
        for b in cand[i + 1:]:
            if b.ts - a.ts > DOUBLE_WINDOW:
                break
            if b.id not in used and (b.counterparty, b.amount, b.card_id) == (a.counterparty, a.amount, a.card_id):
                out.append(DoubleCharge(first=a.id, second=b.id, merchant=a.counterparty, name=a.counterparty_name,
                                        amount=a.amount, card_id=a.card_id,
                                        seconds_apart=int((b.ts - a.ts).total_seconds())))
                used |= {a.id, b.id}
                break
    return out


def month_key(d: datetime | date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def by_month_category(txs: list[Tx]) -> dict[str, dict[Category, int]]:
    out: dict[str, dict[Category, int]] = defaultdict(lambda: defaultdict(int))
    for t in debits(txs):
        if t.status == "refunded":
            continue
        out[month_key(t.ts)][t.category] += t.amount
    for t in txs:
        if t.kind == TxKind.DISPUTE_CREDIT:
            out[month_key(t.ts)][t.category] -= t.amount
    return out
