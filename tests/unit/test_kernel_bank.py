"""The kernel driving the sandbox bank: envelopes as the funding boundary, blocks, disputes, HTTP rails."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from data.generator import CLIENT, build_world
from kernel.crypto import DeviceKey
from kernel.executors import TX_REF, HttpRails
from kernel.money import from_rub
from kernel.passport import Role
from kernel.pipeline import Outcome
from kernel.statements import Category
from sandbox.bank.bank import Envelope
from sandbox.bank.http import create_bank_app
from sandbox.bank.wiring import connect


@pytest.fixture(scope="module")
def base():
    return build_world()


@pytest.fixture
def env(base):
    bank = base.bank.twin()
    bank.set_envelopes(CLIENT, "acc_main", [
        Envelope(id="env_subs", client_id="", account_id="", name="Subscriptions", categories=[Category.SUBSCRIPTIONS],
                 balance=from_rub(500)),
        Envelope(id="env_fun", client_id="", account_id="", name="Cafes & fun", categories=[Category.CAFES],
                 balance=from_rub(2_000))])
    k = connect(bank, base.merchants, seed="t")
    dev = DeviceKey(seed="t:device")
    k.mandates.register_device(CLIENT, dev.public_b64, dev.alg)
    planner = k.passports.issue(Role.PLANNER, "claude-sonnet-5-5", 2, "planner v1", CLIENT, "task_1")
    return k, bank, dev, planner


def ask(k, planner, action, payee, amount=None, src=None, refs=(), memo=""):
    intent = {"action": action, "payee": {"value": payee, "ref": k.bank_value(f"bank:payee:{payee}", payee, "directory")},
              "references": list(refs), "memo": memo}
    if amount is not None:
        intent["amount"] = {"value": amount, "ref": k.bank_value(f"bank:amount:{payee}:{amount}", amount, "history")}
    if src is not None:
        intent["funding_source"] = {"value": src, "ref": k.bank_value(f"bank:src:{src}", src, "client's envelopes")}
    return k.submit(planner.request("propose_intent", {"intent": intent}))


def yes(k, dev, d, standing=False):
    h = d.card.standing_mandate_hash if standing else d.card.mandate_hash
    return k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(h), standing=standing)


def test_payment_from_envelope_and_overspend_card(env):
    k, bank, dev, planner = env
    d = ask(k, planner, "pay", "tok_melodify", from_rub(299), "env_subs")
    assert d.outcome == Outcome.PENDING
    assert any('envelope "Subscriptions"' in line for line in d.card.can)
    done = yes(k, dev, d, standing=True)
    assert done.outcome == Outcome.EXECUTED
    tx = bank.tx(done.receipt.reference)
    assert tx.envelope_id == "env_subs" and tx.initiator == "agent" and tx.ref.startswith("grt_")
    assert bank.available(CLIENT, "env_subs") == from_rub(500) - from_rub(299)
    again = ask(k, planner, "pay", "tok_melodify", from_rub(299), "env_subs")
    assert again.outcome == Outcome.PENDING
    assert any('not enough money in envelope "Subscriptions"' in r for r in again.reasons)
    assert k.journal.verify().ok


def test_bank_refusal_is_a_failure_not_a_success(env):
    k, bank, dev, planner = env
    bank.envelopes["env_subs"].balance = from_rub(100)
    d = ask(k, planner, "pay", "tok_melodify", from_rub(299), "env_subs")
    bank.envelopes["env_subs"].balance = from_rub(500)
    d2 = yes(k, dev, d)
    assert d2.outcome == Outcome.EXECUTED
    d3 = ask(k, planner, "pay", "tok_melodify", from_rub(299), "env_subs")
    bank.envelopes["env_subs"].balance = from_rub(10)
    d4 = yes(k, dev, d3)
    assert d4.outcome == Outcome.FAILED and "not enough" in d4.receipt.details["error"]


def test_block_merchant_stops_the_duplicate_streambox(env):
    k, bank, dev, planner = env
    d = ask(k, planner, "block_merchant", "tok_streambox")
    assert d.outcome == Outcome.PENDING
    assert yes(k, dev, d).outcome == Outcome.EXECUTED
    bank.advance(days=3)
    assert bank.declines[-1].merchant == "tok_streambox" and bank.declines[-1].card_id == "card_5678"
    assert not [t for t in bank.transactions(CLIENT, since=datetime(2026, 10, 17, tzinfo=bank.clock.now().tzinfo))
                if t.counterparty == "tok_streambox"]


def test_dispute_double_charge_refunded_after_three_days(env, base):
    k, bank, dev, planner = env
    second = base.truth.double_charge[1]
    ref = k.bank_value(f"{TX_REF}{second}", second, "statement")
    d = ask(k, planner, "dispute", "tok_freshmart", from_rub(2_345.60), refs=[ref], memo="duplicate charge")
    assert d.outcome == Outcome.PENDING
    done = yes(k, dev, d)
    assert done.outcome == Outcome.EXECUTED and done.receipt.reference.startswith("dsp_")
    before = bank.accounts["acc_main"].balance
    bank.advance(days=3)
    assert bank.tx(second).status == "refunded" and bank.accounts["acc_main"].balance > before


def test_fabricated_transaction_reference_is_denied(env):
    k, bank, dev, planner = env
    d = ask(k, planner, "dispute", "tok_freshmart", from_rub(2_345.60), refs=[f"{TX_REF}tx_made_up"])
    assert d.outcome == Outcome.DENIED and any("does not exist" in r for r in d.reasons)


def test_executor_over_http(base):
    bank = base.bank.twin()
    http = TestClient(create_bank_app(bank, executor_token="ex"))
    k = connect(bank, base.merchants, rails=HttpRails(http, "ex"), seed="t")
    dev = DeviceKey(seed="t:device")
    k.mandates.register_device(CLIENT, dev.public_b64, dev.alg)
    planner = k.passports.issue(Role.PLANNER, "m", 2, "p", CLIENT, "t")
    d = ask(k, planner, "pay", "tok_citywater", from_rub(912.30), "acc_main")
    done = yes(k, dev, d)
    assert done.outcome == Outcome.EXECUTED and bank.tx(done.receipt.reference).counterparty == "tok_citywater"
