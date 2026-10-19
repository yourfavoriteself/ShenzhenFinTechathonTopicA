"""Budget, envelopes and goals (scenario C8). Deterministic code only: every amount, forecast and
limit is computed here; a model may only phrase the explanation.

Envelopes are sub-balances of the current account and double as the `funding_source` boundary of a
mandate: the kernel never lets an agent pay more than an envelope holds, and an agent cannot move
money between envelopes on its own.

Cash gaps are never closed with credit: options are limited to the client's own money and plans.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel

from .analytics import LeakReport, Series, by_month_category, debits, month_key
from .money import from_rub, usd
from .statements import FLEXIBLE, Category, Direction, Tx, TxKind


class EnvelopeDef(BaseModel):
    id: str
    name: str
    kind: str                 # mandatory | subscriptions | flexible | cushion
    categories: list[Category]


ENVELOPES = [
    EnvelopeDef(id="env_bills", name="Bills", kind="mandatory",
                categories=[Category.RENT, Category.UTILITIES, Category.TELECOM]),
    EnvelopeDef(id="env_subs", name="Subscriptions", kind="subscriptions", categories=[Category.SUBSCRIPTIONS]),
    EnvelopeDef(id="env_food", name="Food", kind="flexible", categories=[Category.GROCERIES]),
    EnvelopeDef(id="env_transport", name="Transport", kind="flexible", categories=[Category.TRANSPORT]),
    EnvelopeDef(id="env_fun", name="Cafes & fun", kind="flexible", categories=[Category.CAFES, Category.ENTERTAINMENT]),
    EnvelopeDef(id="env_shopping", name="Shopping", kind="flexible", categories=[Category.SHOPPING]),
    EnvelopeDef(id="env_health", name="Health & sport", kind="flexible", categories=[Category.HEALTH, Category.SPORT]),
    EnvelopeDef(id="env_cushion", name="Cushion", kind="cushion", categories=[]),
]
ENV = {e.id: e for e in ENVELOPES}
ENV_OF = {c: e.id for e in ENVELOPES for c in e.categories}
PROTECTED = {"mandatory", "subscriptions"}
CUSHION_SHARE = 0.05
ONE_OFF = from_rub(10_000)    # a single flexible purchase above $120.70 is a one-off, not daily spending
FORBIDDEN_OPTIONS = {"credit", "loan", "installment", "overdraft", "credit_card", "bnpl"}
GOAL_CUT_CAP = 0.2
SEASON_LIMITS = (0.7, 2.5)
SEASON_MIN_EFFECT = 0.35


def _up_dollar(k: float) -> int:
    return int(math.ceil(k / 100) * 100)


def _down_dollar(k: float) -> int:
    return int(math.floor(k / 100) * 100)


def _month_add(m: str, n: int) -> str:
    y, mo = map(int, m.split("-"))
    mo += n
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def complete_months(txs: list[Tx], as_of: datetime) -> list[str]:
    if not txs:
        return []
    first = min(t.ts for t in txs)
    m = month_key(first) if first.day <= 3 else _month_add(month_key(first), 1)
    out = []
    while m < month_key(as_of):
        out.append(m)
        m = _month_add(m, 1)
    return out


# ---------------------------------------------------------------- income

class IncomeSlot(BaseModel):
    day: int
    amount: int
    payer: str
    name: str


def income_schedule(txs: list[Tx], as_of: datetime | None = None) -> list[IncomeSlot]:
    sal = [t for t in txs if t.kind == TxKind.SALARY and (as_of is None or t.ts < as_of)]
    by_day: dict[int, list[Tx]] = defaultdict(list)
    for t in sal:
        by_day[t.ts.day].append(t)
    return sorted((IncomeSlot(day=d, amount=int(statistics.median(x.amount for x in ts)), payer=ts[-1].counterparty,
                              name=ts[-1].memo or ts[-1].counterparty_name)
                   for d, ts in by_day.items() if len(ts) >= 3), key=lambda s: s.day)


def next_payday(slots: list[IncomeSlot], as_of: datetime) -> date:
    d = as_of.date() + timedelta(days=1)
    for _ in range(62):
        if any(s.day == d.day for s in slots):
            return d
        d += timedelta(days=1)
    raise ValueError("no regular income found")


# ---------------------------------------------------------------- 8.1 autobudget

class EnvelopePlan(BaseModel):
    id: str
    name: str
    kind: str
    categories: list[Category]
    planned: int
    average: int
    median: int
    spread: int
    seasonal_factor: float = 1.0
    untouchable_part: int = 0
    history: dict[str, int] = {}


class BudgetPlan(BaseModel):
    month: str
    based_on: list[str]
    income: int
    envelopes: list[EnvelopePlan]
    savings: int
    cushion_target: int
    one_offs: list[str]
    calc: str

    def env(self, env_id: str) -> EnvelopePlan:
        return next(e for e in self.envelopes if e.id == env_id)

    @property
    def spending(self) -> int:
        return sum(e.planned for e in self.envelopes if e.kind != "cushion")


def _env_totals(txs: list[Tx], months: list[str]) -> tuple[dict[str, dict[str, int]], list[str]]:
    per = by_month_category(txs)
    one_offs = [t.id for t in debits(txs) if t.category in FLEXIBLE and t.amount > ONE_OFF and t.kind == TxKind.CARD]
    totals: dict[str, dict[str, int]] = {e.id: {} for e in ENVELOPES}
    for m in months:
        for e in ENVELOPES:
            totals[e.id][m] = sum(per.get(m, {}).get(c, 0) for c in e.categories)
    return totals, one_offs


def autobudget(txs: list[Tx], as_of: datetime, month: str | None = None,
               untouchable: list[Category] | None = None, train_months: list[str] | None = None) -> BudgetPlan:
    """Plan for `month` (default: next month) from complete months of history before `as_of`."""
    months = train_months or complete_months(txs, as_of)
    if not months:
        raise ValueError("not enough history for a budget")
    month = month or _month_add(month_key(as_of), 1)
    hist = [t for t in txs if month_key(t.ts) in set(months)]
    totals, one_offs = _env_totals(hist, months)
    per = by_month_category(hist)
    income_by_month = defaultdict(int)
    for t in hist:
        if t.kind == TxKind.SALARY:
            income_by_month[month_key(t.ts)] += t.amount
    income = int(statistics.median(income_by_month[m] for m in months))
    plans: list[EnvelopePlan] = []
    for e in ENVELOPES:
        if e.kind == "cushion":
            continue
        vals = [totals[e.id][m] for m in months]
        med = statistics.median(vals)
        q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [min(vals), med, max(vals)]
        factor = 1.0
        same = _month_add(month, -12)
        if e.kind == "subscriptions":
            med = vals[-1]
        elif same in totals[e.id] and med > 0:
            r = totals[e.id][same] / med
            if r - 1 > SEASON_MIN_EFFECT:      # one year of history: only raise for known peaks
                factor = round(min(max(r, SEASON_LIMITS[0]), SEASON_LIMITS[1]), 2)
        keep = 0
        if untouchable:
            keep = int(statistics.median(sum(per.get(m, {}).get(c, 0) for c in e.categories if c in untouchable)
                                         for m in months))
        plans.append(EnvelopePlan(id=e.id, name=e.name, kind=e.kind, categories=e.categories,
                                  planned=_up_dollar(med * factor), average=int(statistics.mean(vals)), median=int(med),
                                  spread=int(q[2] - q[0]), seasonal_factor=factor, untouchable_part=keep,
                                  history=totals[e.id]))
    cushion = _up_dollar(income * CUSHION_SHARE)
    plans.append(EnvelopePlan(id="env_cushion", name="Cushion", kind="cushion", categories=[], planned=cushion,
                              average=0, median=0, spread=0))
    spending = sum(p.planned for p in plans if p.kind != "cushion")
    savings = max(0, _down_dollar(income - spending))
    calc = (f"income {usd(income)} (median of {len(months)} months) - envelopes {usd(spending)} "
            f"= {usd(savings)} to savings; cushion kept at {usd(cushion)}")
    return BudgetPlan(month=month, based_on=months, income=income, envelopes=plans, savings=savings,
                      cushion_target=cushion, one_offs=one_offs, calc=calc)


def spent_by_envelope(txs: list[Tx], month: str) -> dict[str, int]:
    per = by_month_category([t for t in txs if month_key(t.ts) == month])
    return {e.id: sum(per.get(month, {}).get(c, 0) for c in e.categories) for e in ENVELOPES}


def initial_funding(plan: BudgetPlan, spent: dict[str, int], due_until_payday: dict[str, int],
                    available: int, days_left: int, days_in_period: int) -> dict[str, int]:
    """Split the money on the current account across envelopes until the next payday.

    Mandatory payments due before payday come first, then subscriptions due, then the cushion, then
    flexible envelopes pro rata to the rest of the period. If money is short, flexible envelopes shrink
    proportionally; mandatory payments are never cut.
    """
    need: dict[str, int] = {}
    for e in plan.envelopes:
        if e.kind in PROTECTED:
            need[e.id] = due_until_payday.get(e.id, 0)
        elif e.kind == "flexible":
            need[e.id] = max(0, min(e.planned - spent.get(e.id, 0), e.planned * days_left // max(days_in_period, 1)))
    need["env_cushion"] = plan.cushion_target
    out: dict[str, int] = {}
    left = available
    for group in (["env_bills"], ["env_subs"], ["env_cushion"], [e.id for e in plan.envelopes if e.kind == "flexible"]):
        want = sum(need.get(g, 0) for g in group)
        k = 1.0 if want <= left else (left / want if want else 0)
        for g in group:
            out[g] = int(need.get(g, 0) * k)
        left -= sum(out[g] for g in group)
    return out


# ---------------------------------------------------------------- 8.3 forecast

class Event(BaseModel):
    date: date
    kind: str                  # income | mandatory | subscription | bill | trial | goal | purchase
    name: str
    amount: int                # signed: + income, - outflow
    envelope: str | None = None
    merchant: str | None = None
    refs: list[str] = []


class KnownBill(BaseModel):
    payee: str
    name: str
    amount: int
    due: date
    ref: str


class GoalTransfer(BaseModel):
    goal_id: str
    name: str
    amount: int
    day: int


def daily_flexible(txs: list[Tx], as_of: datetime, days: int = 90) -> int:
    since = as_of - timedelta(days=days)
    tot = sum(t.amount for t in debits(txs) if since <= t.ts < as_of and t.kind == TxKind.CARD
              and t.category in FLEXIBLE and t.amount <= ONE_OFF and t.status != "refunded")
    return tot // days


def upcoming(series: list[Series], slots: list[IncomeSlot], as_of: datetime, until: date,
             bills: list[KnownBill] = (), goals: list[GoalTransfer] = ()) -> list[Event]:
    out: list[Event] = []
    start = as_of.date()
    d = start
    while d <= until:
        for s in slots:
            if s.day == d.day and d > start:
                out.append(Event(date=d, kind="income", name=s.name, amount=s.amount, merchant=s.payer))
        for g in goals:
            if g.day == d.day and d > start:
                out.append(Event(date=d, kind="goal", name=f"to goal {g.name}", amount=-g.amount))
        d += timedelta(days=1)
    for b in bills:
        if start <= b.due <= until:
            out.append(Event(date=b.due, kind="bill", name=b.name, amount=-b.amount, merchant=b.payee,
                             envelope=ENV_OF.get(Category.UTILITIES), refs=[b.ref]))
    billed = {(b.payee, b.due.year, b.due.month) for b in bills}
    for s in series:
        if not s.active:
            continue
        nxt = max(s.next_expected, start)
        while nxt <= until:
            covered = any((s.merchant, y, m) in billed for y, m in ((nxt.year, nxt.month),
                                                                     _ym_shift(nxt, -1), _ym_shift(nxt, 1)))
            if not covered:
                kind = "trial" if s.count == 0 and s.trial_until else (
                    "mandatory" if ENV_OF.get(s.category) == "env_bills" else "subscription")
                out.append(Event(date=nxt, kind=kind, name=s.name, amount=-(s.median if s.variable else s.amount),
                                 merchant=s.merchant, envelope=ENV_OF.get(s.category), refs=s.tx_ids[-3:]))
            nxt = _next(nxt, s.day)
    order = {"income": 0}
    return sorted(out, key=lambda e: (e.date, order.get(e.kind, 1), e.amount))


def _ym_shift(d: date, n: int) -> tuple[int, int]:
    m = d.month - 1 + n
    return d.year + m // 12, m % 12 + 1


def _next(d: date, day: int) -> date:
    y, m = _ym_shift(d, 1)
    for dd in (day, 30, 29, 28):
        try:
            return date(y, m, dd)
        except ValueError:
            continue
    raise AssertionError


class Gap(BaseModel):
    date: date
    event: str
    amount: int
    shortfall: int
    days_ahead: int
    refs: list[str] = []


class Point(BaseModel):
    date: date
    balance: int
    events: list[str] = []


class Forecast(BaseModel):
    as_of: datetime
    until: date
    start_balance: int
    daily_flexible: int
    points: list[Point]
    events: list[Event]
    gaps: list[Gap]
    min_balance: int
    end_balance: int


def forecast(as_of: datetime, balance: int, events: list[Event], daily_flex: int, until: date) -> Forecast:
    by_day: dict[date, list[Event]] = defaultdict(list)
    for e in events:
        by_day[e.date].append(e)
    bal, points, gaps = balance, [], []
    flagged: set[str] = set()
    d = as_of.date()
    first_day_share = max(0.0, (24 - as_of.hour - as_of.minute / 60) / 24)
    while d <= until:
        names = []
        for e in sorted(by_day.get(d, []), key=lambda e: -e.amount):
            bal += e.amount
            names.append(f"{e.name} {'+' if e.amount > 0 else '-'}{usd(abs(e.amount))}")
            if e.amount < 0 and e.kind != "purchase" and bal < 0 and f"{e.name}{d}" not in flagged:
                flagged.add(f"{e.name}{d}")
                gaps.append(Gap(date=d, event=e.name, amount=-e.amount, shortfall=-bal,
                                days_ahead=(d - as_of.date()).days, refs=e.refs))
        bal -= int(daily_flex * (first_day_share if d == as_of.date() else 1))
        points.append(Point(date=d, balance=bal, events=names))
        d += timedelta(days=1)
    return Forecast(as_of=as_of, until=until, start_balance=balance, daily_flexible=daily_flex, points=points,
                    events=events, gaps=gaps, min_balance=min((p.balance for p in points), default=balance),
                    end_balance=bal)


class Option(BaseModel):
    kind: str                  # move_envelope | spend_less | cancel_subscription | shift_goal | from_savings
    title: str
    amount: int
    covers: bool
    action: dict = {}
    refs: list[str] = []


def gap_options(fc: Forecast, as_of: datetime, envelopes: dict[str, int] | None, leaks: LeakReport | None,
                savings_balance: int, goals: list[GoalTransfer] = (), daily_flex: int = 0) -> list[Option]:
    """Ways to close the forecast's cash gaps with the client's own money. Never credit, never instalments.

    The amount covers the lowest point of the forecast, not just the first missed payment."""
    opts: list[Option] = []
    gap = fc.gaps[0]
    need = max([g.shortfall for g in fc.gaps] + [-fc.min_balance])
    days = max(1, (gap.date - as_of.date()).days)
    if envelopes:
        for env_id in ("env_fun", "env_shopping"):
            have = envelopes.get(env_id, 0)
            if have > 0:
                amt = min(need, have)
                opts.append(Option(kind="move_envelope", amount=amt, covers=amt >= need,
                                   title=f'Move {usd(amt)} from "{ENV[env_id].name}" to "Bills"',
                                   action={"src": env_id, "dst": "env_bills", "amount": amt}))
    else:
        per_day = _up_dollar(need / days)
        opts.append(Option(kind="spend_less", amount=need, covers=per_day <= daily_flex // 2,
                           title=f"Spend {usd(per_day)} a day less on cafes and shopping until {gap.date}",
                           action={"per_day": per_day, "until": gap.date.isoformat()}))
    for g in goals:
        opts.append(Option(kind="shift_goal", amount=g.amount, covers=g.amount >= need,
                           title=f'Move this month\'s {usd(g.amount)} transfer to "{g.name}" after payday',
                           action={"goal_id": g.goal_id}))
    if leaks:
        for lk in leaks.leaks:
            if lk.counted:
                opts.append(Option(kind="cancel_subscription", amount=lk.monthly, covers=False,
                                   title=f"Cancel {lk.name}: {usd(lk.monthly)} a month less from now on",
                                   action={"merchant": lk.merchant, "card_id": lk.card_id}, refs=lk.evidence[-1:]))
    if savings_balance >= need:
        opts.append(Option(kind="from_savings", amount=_up_dollar(need), covers=True,
                           title=f"Transfer {usd(_up_dollar(need))} from your savings account",
                           action={"src": "savings", "dst": "env_bills", "amount": _up_dollar(need)}))
    assert not any(o.kind in FORBIDDEN_OPTIONS for o in opts)
    return opts


# ---------------------------------------------------------------- 8.2 safe to spend

class SafeToSpend(BaseModel):
    per_day: int
    days_to_payday: int
    payday: date
    balance: int
    mandatory_due: int
    goals_due: int
    cushion: int
    per_envelope: dict[str, int]
    calc: str


def safe_to_spend(as_of: datetime, balance: int, events: list[Event], cushion: int,
                  envelopes: dict[str, int] | None, payday: date) -> SafeToSpend:
    due = [e for e in events if as_of.date() <= e.date < payday and e.amount < 0]
    mandatory = -sum(e.amount for e in due if e.kind != "goal")
    goals = -sum(e.amount for e in due if e.kind == "goal")
    days = max(1, (payday - as_of.date()).days)
    free = balance - mandatory - goals - cushion
    per_day = max(0, free // days)
    per_env = {k: max(0, v // days) for k, v in (envelopes or {}).items() if ENV[k].kind == "flexible"}
    calc = (f"({usd(balance)} balance - {usd(mandatory)} payments due - {usd(goals)} to goals - {usd(cushion)} "
            f"cushion) / {days} days to payday {payday}")
    return SafeToSpend(per_day=per_day, days_to_payday=days, payday=payday, balance=balance, mandatory_due=mandatory,
                       goals_due=goals, cushion=cushion, per_envelope=per_env, calc=calc)


# ---------------------------------------------------------------- 8.4 salary distribution

class Transfer(BaseModel):
    dst: str
    name: str
    amount: int
    kind: str                  # envelope | reserve | savings | goal
    planned: int


class Distribution(BaseModel):
    income: int
    usual: int
    period_end: date
    transfers: list[Transfer]
    reduced: bool
    missing: int
    notes: list[str]


def _due(events: list[Event], start: date, end: date, env: str) -> int:
    return -sum(e.amount for e in events if start <= e.date < end and e.envelope == env and e.amount < 0
                and e.kind != "goal")


def distribute_salary(amount: int, as_of: datetime, slots: list[IncomeSlot], plan: BudgetPlan, events: list[Event],
                      balances: dict[str, int], goals: list[GoalTransfer] = ()) -> Distribution:
    """8.4: split an incoming salary payment across envelopes until the next payday.

    Bills and subscriptions due before the next payday are covered first, plus a reserve for bills
    the next payment will not cover (rent on the 20th falls before the salary on the 25th). Flexible
    envelopes get the plan for the days of the period, then goals, then the cushion; the rest goes to
    savings. A smaller salary cuts goals proportionally, then flexible envelopes; bills are never cut.
    """
    today = as_of.date()
    slot = next((s for s in slots if s.day == today.day), max(slots, key=lambda s: s.amount))
    p1 = next_payday(slots, as_of)
    p2 = next_payday(slots, datetime.combine(p1, time(12), tzinfo=as_of.tzinfo))
    nxt = next((s for s in slots if s.day == p1.day), slot)
    days1, days2 = (p1 - today).days, (p2 - p1).days
    rate = {e.id: e.planned / 30.44 for e in plan.envelopes if e.kind == "flexible"}
    flex_next = sum(r * days2 for r in rate.values())
    reserve = max(0, _up_dollar(_due(events, p1, p2, "env_bills") + _due(events, p1, p2, "env_subs")
                            + flex_next - nxt.amount))
    out: list[Transfer] = []
    bills = max(0, _due(events, today, p1, "env_bills") + reserve - balances.get("env_bills", 0))
    out.append(Transfer(dst="env_bills", name="Bills", amount=_up_dollar(bills), kind="reserve" if reserve else "envelope",
                        planned=_up_dollar(bills)))
    subs = max(0, _due(events, today, p1, "env_subs") - balances.get("env_subs", 0))
    out.append(Transfer(dst="env_subs", name="Subscriptions", amount=_up_dollar(subs), kind="envelope",
                        planned=_up_dollar(subs)))
    for e in plan.envelopes:
        if e.kind == "flexible":
            amt = max(0, _down_dollar(rate[e.id] * days1 - max(0, balances.get(e.id, 0))))
            out.append(Transfer(dst=e.id, name=e.name, amount=amt, kind="envelope", planned=amt))
    reduced = amount < slot.amount * 0.95
    notes: list[str] = []
    k = min(1.0, amount / slot.amount) if slot.amount else 1.0
    for g in goals:
        if g.day == today.day:
            amt = _down_dollar(g.amount * k) if reduced else g.amount
            out.append(Transfer(dst=g.goal_id, name=g.name, amount=amt, kind="goal", planned=g.amount))
    if reduced:
        notes.append(f"salary {usd(amount)} is {usd(slot.amount - amount)} below usual {usd(slot.amount)}; "
                     f"goal transfers scaled to {round(k * 100)}%")
    cushion = max(0, plan.cushion_target - balances.get("env_cushion", 0))
    left = amount - sum(t.amount for t in out)
    if left > 0 and cushion:
        c = min(left, cushion)
        out.append(Transfer(dst="env_cushion", name="Cushion", amount=c, kind="envelope", planned=cushion))
        left -= c
    missing = 0
    if left < 0:
        for kinds in (("goal",), ("flexible",)):
            items = [t for t in out if (t.kind in kinds) or ("flexible" in kinds and t.kind == "envelope"
                                                               and ENV.get(t.dst, ENVELOPES[0]).kind == "flexible")]
            pool = sum(t.amount for t in items)
            if pool <= 0 or left >= 0:
                continue
            cut = min(-left, pool)
            for t in items:
                t.amount = max(0, t.amount - int(math.ceil(cut * t.amount / pool)))
            left = amount - sum(t.amount for t in out)
            notes.append(f"{'goal transfers' if kinds == ('goal',) else 'flexible envelopes'} cut by {usd(cut)}")
        if left < 0:
            missing = -left
            notes.append(f"bills are protected; {usd(missing)} still missing before {p1}")
    if left > 0:
        out.append(Transfer(dst="acc_save", name="Savings", amount=left, kind="savings", planned=left))
    if reserve:
        notes.append(f"{usd(reserve)} kept in Bills for payments the next income on {p1} will not cover")
    return Distribution(income=amount, usual=slot.amount, period_end=p1, transfers=out, reduced=reduced,
                        missing=missing, notes=notes)


# ---------------------------------------------------------------- 8.6 can I afford

class Afford(BaseModel):
    verdict: str               # yes | after_payday | no
    price: int
    sources: list[tuple[str, int]]
    goal_delay_days: int = 0
    earliest: date | None = None
    calc: str
    reasons: list[str] = []


def can_afford(price: int, as_of: datetime, balance: int, events: list[Event], daily_flex: int,
               slots: list[IncomeSlot], available: dict[str, int], goal: "GoalPlan | None" = None,
               untouchable_envs: set[str] = frozenset()) -> Afford:
    """`available` is what is left this period in each flexible envelope (balances, or plan minus spent)."""
    payday = next_payday(slots, as_of)
    following = next_payday(slots, datetime.combine(payday, time(12), tzinfo=as_of.tzinfo))
    horizon = following - timedelta(days=1)
    base = forecast(as_of, balance, events, daily_flex, horizon)
    base_short = {(g.date, g.event): g.shortfall for g in base.gaps}

    def new_gaps(fc: Forecast) -> list[Gap]:
        return [g for g in fc.gaps if g.shortfall > base_short.get((g.date, g.event), 0)]

    buy = Event(date=as_of.date(), kind="purchase", name="purchase", amount=-price)
    fc = forecast(as_of, balance, events + [buy], daily_flex, horizon)
    sources: list[tuple[str, int]] = []
    left = price
    for env_id in ("env_shopping", "env_fun", "env_transport"):
        if env_id in untouchable_envs or left <= 0:
            continue
        take = min(left, max(0, available.get(env_id, 0)))
        if take:
            sources.append((env_id, take))
            left -= take
    delay = 0
    if left > 0 and goal is not None and goal.monthly > 0:
        sources.append((goal.goal.id, left))
        delay = int(math.ceil(left / goal.monthly * 30.44))
        left = 0
    reasons = [f"a cash gap is already expected: {usd(g.shortfall)} missing for {g.event} on {g.date}"
               for g in base.gaps[:1]]
    if new_gaps(fc):
        g = new_gaps(fc)[0]
        reasons.append(f"buying now leaves {usd(g.shortfall)} missing for {g.event} on {g.date}")
        after = Event(date=payday, kind="purchase", name="purchase", amount=-price)
        later = new_gaps(forecast(as_of, balance, events + [after], daily_flex, horizon))
        if not later:
            return Afford(verdict="after_payday", price=price, sources=sources, goal_delay_days=delay,
                          earliest=payday, reasons=reasons,
                          calc=f"after the income on {payday} the purchase fits without new gaps")
        return Afford(verdict="no", price=price, sources=sources, goal_delay_days=delay, reasons=reasons,
                      calc=f"even after {payday} the purchase leaves {usd(later[0].shortfall)} missing "
                           f"for {later[0].event} on {later[0].date}")
    if left > 0:
        reasons.append(f"flexible envelopes are {usd(left)} short")
        return Afford(verdict="after_payday", price=price, sources=sources, earliest=payday, reasons=reasons,
                      calc=f"envelopes cover {usd(price - left)} of {usd(price)}")
    return Afford(verdict="yes", price=price, sources=sources, goal_delay_days=delay,
                  calc=" + ".join(f"{ENV[s].name if s in ENV else 'goal'} {usd(a)}" for s, a in sources)
                  + f" = {usd(price)}; lowest forecast balance {usd(fc.min_balance)}")


# ---------------------------------------------------------------- 8.7 goals

class Goal(BaseModel):
    id: str
    name: str
    target: int
    deadline: date
    saved: int = 0
    untouchable: list[Category] = []
    monthly: int = 0
    day: int = 25


class MonthCheck(BaseModel):
    month: str
    income: int
    mandatory: int
    flexible: int
    goal: int
    left: int
    ok: bool


class GoalPlan(BaseModel):
    goal: Goal
    monthly: int
    contributions: int
    from_surplus: int
    cuts: dict[str, int]
    feasible: bool
    projected_date: date
    months: list[MonthCheck]
    calc: str


def _contribution_dates(as_of: datetime, deadline: date, day: int) -> list[date]:
    out, d = [], as_of.date() + timedelta(days=1)
    while d < deadline:
        if d.day == day:
            out.append(d)
        d += timedelta(days=1)
    return out


def projected_date(remaining: int, monthly: int, start: date) -> date:
    """Date of the contribution that completes the goal (monthly contributions from `start`)."""
    if monthly <= 0:
        return date.max
    n = max(1, math.ceil(remaining / monthly))
    y, m = _ym_shift(start, n - 1)
    return date(y, m, min(start.day, 28))


def plan_goal(goal: Goal, txs: list[Tx], as_of: datetime) -> GoalPlan:
    dates = _contribution_dates(as_of, goal.deadline, goal.day)
    if not dates:
        raise ValueError("deadline is too close")
    remaining = max(0, goal.target - goal.saved)
    monthly = _up_dollar(remaining / len(dates))
    base = autobudget(txs, as_of, untouchable=goal.untouchable)
    surplus = base.savings
    need = max(0, monthly - surplus)
    cuts: dict[str, int] = {}
    if need:
        cuttable = {e.id: int((e.planned - e.untouchable_part) * GOAL_CUT_CAP) for e in base.envelopes
                    if e.kind == "flexible" and not set(e.categories) <= set(goal.untouchable)}
        pool = sum(cuttable.values())
        take = min(need, pool)
        for k, v in cuttable.items():
            if v and pool:
                cuts[k] = _up_dollar(take * v / pool)
    covered = surplus + sum(cuts.values())
    feasible = covered >= monthly
    checks: list[MonthCheck] = []
    for d in dates:
        p = autobudget(txs, as_of, month=month_key(d), untouchable=goal.untouchable)
        mand = sum(e.planned for e in p.envelopes if e.kind in PROTECTED)
        flex = sum(e.planned - cuts.get(e.id, 0) for e in p.envelopes if e.kind == "flexible")
        left = p.income - mand - flex - monthly
        checks.append(MonthCheck(month=month_key(d), income=p.income, mandatory=mand, flexible=flex, goal=monthly,
                                 left=left, ok=left >= 0))
    feasible = feasible and all(c.ok for c in checks)
    effective = monthly if feasible else min(monthly, covered)
    proj = projected_date(remaining, effective, dates[0])
    calc = (f"({usd(goal.target)} - {usd(goal.saved)} saved) / {len(dates)} payments = {usd(monthly)} a month; "
            f"{usd(min(surplus, monthly))} from the usual surplus"
            + (f", {usd(sum(cuts.values()))} from flexible envelopes" if cuts else ""))
    return GoalPlan(goal=goal.model_copy(update={"monthly": monthly}), monthly=monthly, contributions=len(dates),
                    from_surplus=min(surplus, monthly), cuts=cuts, feasible=feasible, projected_date=proj,
                    months=checks, calc=calc)


class SavingsBoost(BaseModel):
    extra_monthly: int
    old_date: date
    new_date: date
    days_earlier: int
    calc: str


def savings_to_goal(plan: GoalPlan, extra_monthly: int, start: date, source: str) -> SavingsBoost:
    """8.8: money freed by cancelled subscriptions or refunds goes to the goal."""
    remaining = max(0, plan.goal.target - plan.goal.saved)
    old = projected_date(remaining, plan.monthly, start)
    new = projected_date(remaining, plan.monthly + extra_monthly, start)
    return SavingsBoost(extra_monthly=extra_monthly, old_date=old, new_date=new, days_earlier=(old - new).days,
                        calc=f"{source}: +{usd(extra_monthly)} a month -> goal \"{plan.goal.name}\" "
                             f"{(old - new).days} days earlier ({old} -> {new})")


# ---------------------------------------------------------------- 8.9 month review

class ReviewRow(BaseModel):
    envelope: str
    name: str
    planned: int
    actual: int
    diff: int


class Anomaly(BaseModel):
    category: Category
    actual: int
    usual: int
    pct: int
    note: str
    tx_ids: list[str]


class MonthReview(BaseModel):
    month: str
    rows: list[ReviewRow]
    anomalies: list[Anomaly]
    price_changes: list[str]
    suggestions: dict[str, int]


def month_review(month: str, txs: list[Tx], plan: BudgetPlan, series: list[Series] = ()) -> MonthReview:
    spent = spent_by_envelope(txs, month)
    rows = [ReviewRow(envelope=e.id, name=e.name, planned=e.planned, actual=spent.get(e.id, 0),
                      diff=spent.get(e.id, 0) - e.planned) for e in plan.envelopes if e.kind != "cushion"]
    per = by_month_category(txs)
    prev = [_month_add(month, -i) for i in range(1, 7) if _month_add(month, -i) in per]
    anomalies: list[Anomaly] = []
    for c in FLEXIBLE:
        actual = per.get(month, {}).get(c, 0)
        if not prev:
            break
        usual = int(statistics.median(per[m].get(c, 0) for m in prev))
        if usual and actual > usual * 1.4 and actual - usual > from_rub(2_000):
            mtx = [t for t in debits(txs) if month_key(t.ts) == month and t.category == c]
            late = [t for t in mtx if t.ts.hour >= 23 or t.ts.hour < 4]
            pct = round((actual / usual - 1) * 100)
            note = f"{c.value} +{pct}% to usual"
            if late:
                note += f", {len(late)} transactions after 23:00"
            anomalies.append(Anomaly(category=c, actual=actual, usual=usual, pct=pct, note=note,
                                     tx_ids=[t.id for t in (late or mtx)]))
    changes = [f"{s.name}: {usd(s.previous_amount)} -> {usd(s.amount)}" for s in series
               if s.changed_on and month_key(s.changed_on) == month and s.previous_amount]
    suggestions: dict[str, int] = {}
    last3 = [_month_add(month, -i) for i in range(0, 3)]
    for e in plan.envelopes:
        if e.kind != "flexible" or not e.planned:
            continue
        med = statistics.median(spent_by_envelope(txs, m).get(e.id, 0) for m in last3)
        if abs(med - e.planned) > 0.2 * e.planned:
            suggestions[e.id] = _up_dollar(med)
    return MonthReview(month=month, rows=rows, anomalies=anomalies, price_changes=changes, suggestions=suggestions)


# ---------------------------------------------------------------- 8.10 quality

class ForecastQuality(BaseModel):
    train: list[str]
    test: list[str]
    mape: dict[str, float]
    overall_wape: float
    detail: dict[str, dict[str, tuple[int, int]]]


def evaluate_budget(txs: list[Tx], as_of: datetime, train_n: int = 9) -> ForecastQuality:
    """Plan months 10-12 from the first nine and compare with what was actually spent."""
    months = complete_months(txs, as_of)
    train, test = months[:train_n], months[train_n:train_n + 3]
    mape: dict[str, list[float]] = defaultdict(list)
    detail: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    err = tot = 0
    for m in test:
        plan = autobudget(txs, as_of, month=m, train_months=train)
        actual = spent_by_envelope(txs, m)
        for e in plan.envelopes:
            if e.kind == "cushion":
                continue
            a = actual.get(e.id, 0)
            detail[e.id][m] = (e.planned, a)
            if a:
                mape[e.id].append(abs(e.planned - a) / a)
            err += abs(e.planned - a)
            tot += a
    return ForecastQuality(train=train, test=test, mape={k: round(statistics.mean(v) * 100, 1) for k, v in mape.items()},
                           overall_wape=round(err / tot * 100, 1) if tot else 0.0, detail=detail)


def balance_at(txs: list[Tx], account_id: str, current: int, t: datetime) -> int:
    return current - sum(x.signed for x in txs if x.account_id == account_id and x.ts >= t)


class GapBacktest(BaseModel):
    probes: int
    predicted: list[tuple[str, str]]          # (as_of, gap date)
    actual: list[str]
    caught_in_time: int
    false_alarms: int


def probe_dates(txs: list[Tx], as_of: datetime, days: tuple[int, ...] = (5, 17)) -> list[datetime]:
    """Backtest points: the 5th and 17th of every complete month after the first three (enough history)."""
    out = []
    for m in complete_months(txs, as_of)[3:]:
        y, mo = map(int, m.split("-"))
        out += [datetime(y, mo, d, 10, tzinfo=as_of.tzinfo) for d in days]
    return out


def backtest_cash_gaps(txs: list[Tx], cards_on_file: list, merchants, account_id: str, current_balance: int,
                       probes: list[datetime], actual_gaps: list[date], lead_days: int = 3) -> GapBacktest:
    """Re-run the payday forecast at past dates with only the data known then."""
    from .analytics import recurring
    predicted: list[tuple[str, str]] = []
    caught: set[date] = set()
    false = 0
    for as_of in probes:
        hist = [t for t in txs if t.ts < as_of]
        series = recurring(hist, [c for c in cards_on_file if c.started <= as_of.date()], merchants, as_of)
        slots = income_schedule(hist)
        payday = next_payday(slots, as_of)
        fc = forecast(as_of, balance_at(txs, account_id, current_balance, as_of), upcoming(series, slots, as_of, payday),
                      daily_flexible(hist, as_of), payday)
        for g in fc.gaps[:1]:
            predicted.append((as_of.date().isoformat(), g.date.isoformat()))
            if g.date in actual_gaps and g.days_ahead >= lead_days:
                caught.add(g.date)
            elif g.date not in actual_gaps:
                false += 1
    return GapBacktest(probes=len(probes), predicted=predicted, actual=[d.isoformat() for d in actual_gaps],
                       caught_in_time=len(caught), false_alarms=false)
