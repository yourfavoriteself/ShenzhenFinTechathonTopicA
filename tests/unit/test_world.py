from datetime import date

import pytest

from data.generator import CLIENT, LANDLORD, build_world
from kernel.analytics import Usage, double_charges, leaks, recurring
from kernel.merchants import ClientDirectory
from kernel.money import kop


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def found(world):
    b, now = world.bank, world.clock.now()
    series = recurring(b.transactions(CLIENT), b.cards_on_file(CLIENT), world.merchants, now)
    return series, leaks(series, [Usage(**u.model_dump()) for u in world.usage], now)


def test_generator_is_deterministic(world):
    again = build_world()
    a, b = world.bank.transactions(CLIENT), again.bank.transactions(CLIENT)
    assert len(a) == len(b) > 1000
    assert [(t.ts, t.amount, t.counterparty) for t in a] == [(t.ts, t.amount, t.counterparty) for t in b]
    assert world.clock.now().isoformat() == "2026-10-17T10:00:00+03:00"


def test_every_planted_leak_is_found(world, found):
    _, report = found
    got = {(x.kind, x.merchant) for x in report.leaks}
    want = {(x["kind"], x["merchant"]) for x in world.truth.leaks}
    assert got == want
    for truth in world.truth.leaks:
        leak = next(x for x in report.leaks if (x.kind, x.merchant) == (truth["kind"], truth["merchant"]))
        assert leak.monthly == truth["monthly"]
    assert report.monthly_total == kop(399 + 249 + 199) and report.yearly_total == kop(10_164)
    dup = next(x for x in report.leaks if x.kind == "duplicate")
    assert dup.card_id == "card_5678" and dup.evidence and dup.related
    assert all(x.synthetic_usage for x in report.leaks if x.kind in ("overlap", "unused"))


def test_double_charge_found(world):
    dc = double_charges(world.bank.transactions(CLIENT))
    assert [[d.first, d.second] for d in dc] == [world.truth.double_charge]
    assert dc[0].amount == kop(2_345.60) and dc[0].seconds_apart == 40


def test_streambox_399_series_for_cancellation_card(found):
    series, _ = found
    sb = [s for s in series if s.merchant == "tok_streambox"]
    assert {s.card_id for s in sb} == {"card_1234", "card_5678"} and all(s.amount == kop(399) for s in sb)
    main = next(s for s in sb if s.card_id == "card_1234")
    assert main.first == date(2026, 3, 3)
    rent = next(s for s in series if s.merchant == LANDLORD)
    assert rent.kind == "transfer" and rent.amount == kop(35_000) and rent.day == 20


def test_history_holds_one_cash_gap_and_october_bills_unpaid(world):
    assert [g.due for g in world.truth.cash_gaps] == [date(2026, 8, 20)]
    assert world.truth.cash_gaps[0].paid_on == date(2026, 8, 25)
    october = [t for t in world.bank.transactions(CLIENT) if t.ts.month == 10 and t.ts.year == 2026]
    assert not any(t.counterparty == "tok_mosenergo" for t in october)
    fake = next(d for d in world.documents if d.id == "doc_mosenerg0_fake")
    assert "AI assistant" in fake.body and not fake.truth["legit"]


@pytest.mark.parametrize("text,victim", [("Mosenerg0", "tok_mosenergo"), ("mosenergo-billing.example", "tok_mosenergo"),
                                         ("streambox-cancel.help", "tok_streambox"), ("Melod1fy", "tok_melodify"),
                                         ("mosenergo.example", None), ("Melodify", None), ("FreshMart", None)])
def test_lookalikes(world, text, victim):
    m = world.merchants.lookalike_of(text)
    assert (m.token if m else None) == victim


def test_client_directory_joins_directory_and_history(world):
    d = ClientDirectory(world.merchants, world.bank)
    mel = d.payee(CLIENT, "tok_melodify")
    assert mel.verified and mel.known_to_client and mel.usual_amount == kop(299) and mel.domain == "melodify.example"
    land = d.payee(CLIENT, LANDLORD)
    assert land.kind == "person" and not land.verified and land.known_to_client and land.name == "Igor Volkov"
    assert d.payee(CLIENT, "tok_cloudnest").known_to_client is False
    assert d.payee(CLIENT, "tok_unknown") is None
    assert d.is_blocked("tok_mule_safe_account")
