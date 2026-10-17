from datetime import date, datetime

import pytest

from data.generator import CLIENT, LANDLORD, MAIN, SAVINGS, build_world
from kernel import budget as B
from kernel.analytics import Usage, leaks, recurring
from kernel.money import kop
from kernel.statements import Category
from sandbox.bank.wiring import open_envelopes

GYM = [Category.SPORT]


@pytest.fixture(scope="module")
def world():
    return build_world()


def known_bills(world, after: date | None = None):
    return [B.KnownBill(payee=d.truth["payee"], name=d.subject.split(",")[0], amount=d.truth["amount"],
                        due=date.fromisoformat(d.truth["due"]), ref=d.id)
            for d in world.documents if d.truth.get("legit") and d.truth.get("due")
            and (after is None or date.fromisoformat(d.truth["due"]) > after)]


def state(world, bank=None, bills=None):
    bank = bank or world.bank
    now, txs = bank.clock.now(), bank.transactions(CLIENT)
    series = recurring(txs, bank.cards_on_file(CLIENT), world.merchants, now)
    slots = B.income_schedule(txs)
    payday = B.next_payday(slots, now)
    events = B.upcoming(series, slots, now, date(2026, 12, 31), known_bills(world) if bills is None else bills)
    return now, txs, series, slots, payday, events


def after_salary(world):
    """Twin of the world on 25 Oct after salary: gap closed from savings, rent and electricity paid."""
    t = world.bank.twin()
    tz = t.clock.now().tzinfo
    t.transfer_own(CLIENT, SAVINGS, MAIN, kop(12_000), memo="cover rent")
    t.run_until(datetime(2026, 10, 20, 12, tzinfo=tz))
    t.pay(CLIENT, LANDLORD, kop(35_000), MAIN, category=Category.RENT, payee_name="Igor Volkov")
    t.run_until(datetime(2026, 10, 25, 11, tzinfo=tz))
    t.pay(CLIENT, "tok_mosenergo", kop(4_382.40), MAIN)
    return t


def test_autobudget_from_twelve_months(world):
    now, txs, *_ = state(world)
    plan = B.autobudget(txs, now)
    assert len(plan.based_on) == 12 and plan.month == "2026-11"
    assert [e.id for e in plan.envelopes] == [e.id for e in B.ENVELOPES]
    assert all(e.planned % 10_000 == 0 for e in plan.envelopes)
    assert plan.savings == (plan.income - plan.spending) // 10_000 * 10_000
    bills = plan.env("env_bills")
    assert kop(40_000) <= bills.planned <= kop(43_000)
    assert plan.env("env_subs").planned >= kop(1_700)
    dec = B.autobudget(txs, now, month="2026-12")
    assert dec.env("env_shopping").seasonal_factor > 1.5 and dec.env("env_shopping").planned > \
        plan.env("env_shopping").planned
    assert any(t.counterparty == "tok_smiledent" for t in txs if t.id in plan.one_offs)


def test_upcoming_cash_gap_predicted_and_closed_only_with_own_money(world):
    now, txs, series, slots, payday, events = state(world)
    fc = B.forecast(now, world.bank.accounts[MAIN].balance, events, B.daily_flexible(txs, now), payday)
    gap = fc.gaps[0]
    assert gap.date.isoformat() == world.truth.upcoming_gap["due"] and gap.event == "Igor Volkov"
    assert gap.days_ahead == 3 and kop(1_000) < gap.shortfall < kop(15_000) and gap.refs
    lk = leaks(series, [Usage(**u.model_dump()) for u in world.usage], now)
    opts = B.gap_options(fc, now, None, lk, world.bank.accounts[SAVINGS].balance,
                         daily_flex=B.daily_flexible(txs, now))
    kinds = {o.kind for o in opts}
    assert not kinds & B.FORBIDDEN_OPTIONS and {"from_savings", "cancel_subscription", "spend_less"} <= kinds
    fix = next(o for o in opts if o.kind == "from_savings")
    assert fix.covers and fix.amount >= gap.shortfall
    stt = B.safe_to_spend(now, world.bank.accounts[MAIN].balance, events, kop(5_900), None, payday)
    assert stt.per_day == 0 and stt.payday == date(2026, 10, 25) and "rent" not in stt.calc

    t = world.bank.twin()
    t.transfer_own(CLIENT, SAVINGS, MAIN, fix.amount)
    fc2 = B.forecast(now, t.accounts[MAIN].balance, events, B.daily_flexible(txs, now), payday)
    assert not fc2.gaps


def test_envelopes_are_funded_bills_first(world):
    t = world.bank.twin()
    now, txs, series, slots, payday, events = state(world, t)
    plan = B.autobudget(txs, now, month="2026-10")
    due = {env: -sum(e.amount for e in events if e.date < payday and e.envelope == env and e.amount < 0)
           for env in ("env_bills", "env_subs")}
    funding = open_envelopes(t, CLIENT, MAIN, plan, due, payday)
    assert sum(funding.values()) <= t.accounts[MAIN].balance
    assert funding["env_bills"] == min(due["env_bills"], t.accounts[MAIN].balance)
    assert t.available(CLIENT, "env_fun") == funding["env_fun"] == 0


def test_backtest_catches_august_gap_without_false_alarms(world):
    tz = world.clock.now().tzinfo
    probes = [datetime(2026, m, d, 10, tzinfo=tz) for m in range(1, 10) for d in (5, 17)]
    bt = B.backtest_cash_gaps(world.bank.transactions(CLIENT), world.bank.cards_on_file(CLIENT), world.merchants,
                              MAIN, world.bank.accounts[MAIN].balance, probes, [g.due for g in world.truth.cash_gaps])
    assert bt.caught_in_time == 1 and bt.false_alarms == 0 and bt.predicted == [("2026-08-17", "2026-08-20")]


def test_forecast_quality_is_measured(world):
    q = B.evaluate_budget(world.bank.transactions(CLIENT), world.clock.now())
    assert q.test == ["2026-07", "2026-08", "2026-09"] and len(q.train) == 9
    assert set(q.mape) == {e.id for e in B.ENVELOPES if e.kind != "cushion"}
    assert q.mape["env_bills"] < 10 and q.overall_wape < 35


def test_salary_distribution_protects_bills(world):
    t = after_salary(world)
    now, txs, series, slots, payday, events = state(world, t, known_bills(world, after=date(2026, 10, 25)))
    plan = B.autobudget(txs, now)
    goal = [B.GoalTransfer(goal_id="g1", name="Vacation", amount=kop(6_700), day=25)]
    usual = slots[1].amount
    normal = B.distribute_salary(usual, now, slots, plan, events, {}, goal)
    assert sum(x.amount for x in normal.transfers) == usual and not normal.reduced and normal.missing == 0
    by = {x.dst: x for x in normal.transfers}
    assert by["env_bills"].kind == "reserve" and by["g1"].amount == kop(6_700) and by["acc_save"].amount > 0
    small = B.distribute_salary(kop(50_000), now, slots, plan, events, {}, goal)
    sby = {x.dst: x for x in small.transfers}
    assert small.reduced and "acc_save" not in sby and sby["env_bills"].amount == by["env_bills"].amount
    assert sby["g1"].amount < kop(6_700) and sum(x.amount for x in small.transfers) == kop(50_000)


def test_can_i_afford(world):
    now, txs, series, slots, payday, events = state(world)
    g = B.plan_goal(B.Goal(id="g1", name="Vacation", target=kop(60_000), deadline=date(2027, 7, 1), untouchable=GYM),
                    txs, now)
    avail = {"env_shopping": kop(9_000), "env_fun": kop(3_000)}
    a = B.can_afford(kop(15_000), now, world.bank.accounts[MAIN].balance, events, B.daily_flexible(txs, now),
                     slots, avail, g)
    assert a.verdict == "after_payday" and a.earliest == date(2026, 10, 25) and "already expected" in a.reasons[0]
    t = after_salary(world)
    now, txs, series, slots, payday, events = state(world, t, known_bills(world, after=date(2026, 10, 25)))
    a = B.can_afford(kop(15_000), now, t.accounts[MAIN].balance, events, B.daily_flexible(txs, now), slots, avail, g)
    assert a.verdict == "yes" and a.sources == [("env_shopping", kop(9_000)), ("env_fun", kop(3_000)),
                                                 ("g1", kop(3_000))]
    assert a.goal_delay_days == 14
    big = B.can_afford(kop(150_000), now, t.accounts[MAIN].balance, events, B.daily_flexible(txs, now), slots,
                       avail, g)
    assert big.verdict == "no"


def test_goal_keeps_untouchable_items(world):
    now, txs, *_ = state(world)
    g = B.plan_goal(B.Goal(id="g1", name="Vacation", target=kop(60_000), deadline=date(2027, 7, 1), untouchable=GYM),
                    txs, now)
    assert g.monthly == kop(6_700) and g.contributions == 9 and g.feasible and all(m.ok for m in g.months)
    assert g.projected_date <= date(2027, 7, 1)
    car = B.plan_goal(B.Goal(id="g2", name="Car", target=kop(600_000), deadline=date(2027, 7, 1), untouchable=GYM),
                      txs, now)
    assert not car.feasible and car.projected_date > date(2027, 7, 1)
    plan = B.autobudget(txs, now, untouchable=GYM)
    health = plan.env("env_health")
    assert health.untouchable_part >= kop(2_500)
    assert car.cuts["env_health"] <= (health.planned - health.untouchable_part) * B.GOAL_CUT_CAP + 10_000
    boost = B.savings_to_goal(g, kop(847), date(2026, 10, 25), "cancelled StreamBox, TuneWave, PixelPlay")
    assert boost.days_earlier > 0 and boost.new_date < boost.old_date


def test_month_review_finds_late_night_cafes(world):
    now, txs, series, *_ = state(world)
    tz = now.tzinfo
    sep = B.month_review("2026-09", txs, B.autobudget(txs, datetime(2026, 9, 1, tzinfo=tz), month="2026-09"), series)
    cafes = next(a for a in sep.anomalies if a.category == Category.CAFES)
    assert cafes.pct >= 40 and "4 transactions after 23:00" in cafes.note
    assert {t.counterparty for t in txs if t.id in cafes.tx_ids} == {"tok_nightowl"}
    aug = B.month_review("2026-08", txs, B.autobudget(txs, datetime(2026, 8, 1, tzinfo=tz), month="2026-08"), series)
    assert aug.price_changes == ["NewsDaily: 199 ₽ -> 249 ₽"]
    assert {r.envelope for r in sep.rows} == {e.id for e in B.ENVELOPES if e.kind != "cushion"}
