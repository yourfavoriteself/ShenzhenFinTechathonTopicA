from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from data.catalog import directory
from kernel.clock import SimClock
from kernel.executors import HttpRails, RailsError
from kernel.money import from_rub
from kernel.statements import Category, TxKind
from sandbox.bank.bank import Declined, Envelope, SandboxBank
from sandbox.bank.http import create_bank_app


@pytest.fixture
def bank():
    b = SandboxBank(SimClock(), directory())
    b.open_account("cl_1", "acc_main", "current", "Current account")
    b.open_account("cl_1", "acc_save", "savings", "Savings account")
    b.issue_card("cl_1", "card_1234", "acc_main", "1234")
    b.accounts["acc_main"].balance = from_rub(50_000)
    return b


def test_trial_then_monthly_charges_and_block(bank):
    c = bank.add_card_on_file("cl_1", "tok_cloudnest", "card_1234", from_rub(149), date(2026, 10, 18),
                              trial_until=date(2026, 10, 17))
    bank.advance(days=1)
    assert [t.counterparty for t in bank.txs] == ["tok_cloudnest"] and bank.txs[0].kind == TxKind.RECURRING
    assert c.next_date == date(2026, 11, 18)
    bank.block_merchant("cl_1", "tok_cloudnest")
    bank.advance(days=32)
    assert len(bank.txs) == 1 and bank.declines[-1].reason == "merchant blocked by the client"


def test_merchant_cancel_stops_charges(bank):
    bank.add_card_on_file("cl_1", "tok_streambox", "card_1234", from_rub(399), date(2026, 10, 19))
    assert bank.cancel_card_on_file("cl_1", "tok_streambox")
    bank.advance(days=3)
    assert bank.txs == [] and bank.declines[-1].reason == "card on file cancelled"


def test_envelopes_bound_payments_and_attribute_card_spend(bank):
    bank.set_envelopes("cl_1", "acc_main", [
        Envelope(id="env_subs", client_id="", account_id="", name="Subscriptions",
                 categories=[Category.SUBSCRIPTIONS], balance=from_rub(150)),
        Envelope(id="env_food", client_id="", account_id="", name="Food", categories=[Category.GROCERIES],
                 balance=from_rub(5_000))])
    with pytest.raises(Declined, match="Subscriptions"):
        bank.pay("cl_1", "tok_melodify", from_rub(299), "env_subs")
    assert bank.available("cl_1", "env_subs") == from_rub(150)
    bank.card_purchase("card_1234", "tok_freshmart", from_rub(1_200))
    assert bank.envelopes["env_food"].balance == from_rub(3_800) and bank.txs[-1].envelope_id == "env_food"
    bank.transfer_own("cl_1", "env_food", "env_subs", from_rub(200))
    t = bank.pay("cl_1", "tok_melodify", from_rub(299), "env_subs")
    assert bank.envelopes["env_subs"].balance == from_rub(150) + from_rub(200) - from_rub(299) and t.kind == TxKind.BILL
    assert bank.label("cl_1", "env_subs") == 'envelope "Subscriptions"'
    with pytest.raises(Declined, match="unallocated"):
        bank.transfer_own("cl_1", "acc_main", "env_food", from_rub(50_000))


def test_dispute_refunds_only_a_real_duplicate(bank):
    a = bank.card_purchase("card_1234", "tok_freshmart", from_rub(2_345.60))
    bank.clock.advance(seconds=40)
    b = bank.card_purchase("card_1234", "tok_freshmart", from_rub(2_345.60))
    bank.clock.advance(hours=3)
    single = bank.card_purchase("card_1234", "tok_bistro21", from_rub(1_800))
    d1 = bank.open_dispute("cl_1", b.id, "duplicate")
    d2 = bank.open_dispute("cl_1", single.id, "duplicate")
    with pytest.raises(Exception):
        bank.open_dispute("cl_1", b.id, "again")
    before = bank.accounts["acc_main"].balance
    bank.advance(days=3)
    assert d1.status == "won" and bank.tx(b.id).status == "refunded"
    assert bank.accounts["acc_main"].balance == before + from_rub(2_345.60)
    assert d2.status == "rejected" and bank.tx(single.id).status == "posted" and a.status == "posted"


def test_scheduled_action_and_salary(bank):
    from sandbox.bank.bank import IncomeRule
    bank.incomes = [IncomeRule(client_id="cl_1", account_id="acc_main", payer="tok_northwind",
                               payer_name="Northwind LLC", day=25, amount=from_rub(70_000))]
    s = bank.schedule("cl_1", date(2026, 10, 26), "transfer_own", {"src": "acc_main", "dst": "acc_save",
                                                                   "amount": from_rub(10_000)})
    bank.advance(days=9)
    assert s.status == "done" and bank.accounts["acc_save"].balance == from_rub(10_000)
    assert bank.accounts["acc_main"].balance == from_rub(50_000) + from_rub(70_000) - from_rub(10_000)


def test_twin_is_independent(bank):
    t = bank.twin()
    t.pay("cl_1", "tok_mosenergo", from_rub(4_000), "acc_main")
    assert bank.accounts["acc_main"].balance == from_rub(50_000) and bank.txs == []


def test_http_api_requires_executor_token(bank):
    http = TestClient(create_bank_app(bank, executor_token="ex-secret", merchant_token="m-secret"))
    bad = HttpRails(http, "wrong")
    with pytest.raises(RailsError, match="token"):
        bad.pay("cl_1", "tok_mosenergo", from_rub(4_000), "acc_main", "", "grt_1")
    rails = HttpRails(http, "ex-secret")
    r = rails.pay("cl_1", "tok_mosenergo", from_rub(4_000), "acc_main", "electricity", "grt_1")
    assert bank.tx(r["tx_id"]).ref == "grt_1"
    with pytest.raises(RailsError, match="insufficient"):
        rails.pay("cl_1", "tok_mosenergo", from_rub(90_000), "acc_main", "", "grt_2")
    bank.add_card_on_file("cl_1", "tok_streambox", "card_1234", from_rub(399), date(2026, 10, 19))
    assert http.post("/merchant/tok_streambox/cancel", json={"client_id": "cl_1"}).status_code == 403
    r = http.post("/merchant/tok_streambox/cancel", json={"client_id": "cl_1"}, headers={"X-Merchant-Token": "m-secret"})
    assert len(r.json()["cancelled"]) == 1
