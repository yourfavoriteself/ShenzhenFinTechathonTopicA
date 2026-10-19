import pytest

from kernel.cards import Risk
from kernel.core import ConfirmationError, Kernel
from kernel.crypto import DeviceKey
from kernel.money import from_rub
from kernel.passport import Role
from kernel.pipeline import Outcome, StepResult, Verdict
from kernel.ports import InMemoryDirectory, InMemoryFunds, PayeeInfo, Receipt
from kernel.provenance import Labeled, Label
from kernel.trust import AgentStatus, Category, agent_key


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, grant, intent):
        self.calls.append((grant.action.value, grant.payee, grant.amount))
        return Receipt(ok=True, reference=f"tx_{len(self.calls)}")


PAYEES = [
    PayeeInfo(token="tok_melodify", name="Melodify", kind="subscription", verified=True, known_to_client=True,
              usual_amount=from_rub(299), domain="melodify.example"),
    PayeeInfo(token="tok_mosenergo", name="Mosenergo", kind="biller", verified=True, known_to_client=True,
              usual_amount=from_rub(4_200)),
    PayeeInfo(token="tok_newshop", name="New Shop", verified=False, known_to_client=False),
]


@pytest.fixture
def env(clock):
    ex = FakeExecutor()
    k = Kernel(clock=clock, seed="t", executor=ex,
               directory=InMemoryDirectory(PAYEES, blocked={"tok_mule"}),
               funds=InMemoryFunds({"card_1234": from_rub(50_000), "env_subs": from_rub(200)}, {"card_1234": "card *1234"}))
    dev = DeviceKey(seed="t:device")
    k.mandates.register_device("cl_1", dev.public_b64, dev.alg)
    k.bank_value("bank:payee:melodify", "tok_melodify", "merchant directory")
    k.bank_value("bank:amount:melodify", from_rub(299), "usual amount from history")
    k.bank_value("bank:card", "card_1234", "client's cards")
    return k, ex, dev, new_planner(k)


def new_planner(k):
    return k.passports.issue(Role.PLANNER, "claude-sonnet-5-5", 2, "planner v1", "cl_1", "task_2")


def pay(planner, payee=("tok_melodify", "bank:payee:melodify"), amount=(from_rub(299), "bank:amount:melodify"),
        src=("card_1234", "bank:card"), **extra):
    intent = {"action": "pay", "payee": {"value": payee[0], "ref": payee[1]},
              "amount": {"value": amount[0], "ref": amount[1]},
              "funding_source": {"value": src[0], "ref": src[1]}, **extra}
    return planner.request("propose_intent", {"intent": intent})


def test_expired_passport_is_rejected(env, clock):
    k, ex, dev, planner = env
    clock.advance(hours=2)
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.DENIED and "passport expired" in d.reasons[0]


def test_first_payment_asks_then_standing_mandate_repeats_without_asking(env, clock):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.PENDING and d.risk == Risk.MEDIUM
    card = d.card
    assert card.standing_offer and "Melodify" in card.can[0] and card.cannot
    assert {f.name: f.badge for f in card.fields}["payee"] == "verified by the bank"
    d2 = k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(card.standing_mandate_hash), standing=True)
    assert d2.outcome == Outcome.EXECUTED and ex.calls == [("pay", "tok_melodify", from_rub(299))]
    clock.advance(days=31)
    planner = new_planner(k)
    d3 = k.submit(pay(planner))
    assert d3.outcome == Outcome.EXECUTED and d3.notify_only and d3.stage == 2 and len(ex.calls) == 2
    d4 = k.submit(pay(planner))
    assert d4.outcome == Outcome.PENDING and any("already used" in r for r in d4.reasons)
    assert k.journal.verify().ok


def test_price_increase_shows_card_with_highlighted_change(env, clock):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.standing_mandate_hash), standing=True)
    clock.advance(days=31)
    planner = new_planner(k)
    k.bank_value("bank:amount:melodify:new", from_rub(499), "invoice matched to history")
    d = k.submit(pay(planner, amount=(from_rub(499), "bank:amount:melodify:new")))
    assert d.outcome == Outcome.PENDING and d.risk == Risk.HIGH
    amount = next(f for f in d.card.fields if f.name == "amount")
    assert amount.changed and amount.previous == "$3.61" and amount.value == "$6.02"


def test_agent_cannot_exceed_referenced_mandate(env):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.standing_mandate_hash), standing=True)
    mid = k.mandates.active("cl_1")[0].mandate.id
    k.bank_value("bank:payee:mosenergo", "tok_mosenergo", "merchant directory")
    d = k.submit(pay(planner, payee=("tok_mosenergo", "bank:payee:mosenergo"), mandate_id=mid))
    assert d.outcome == Outcome.DENIED and any("payee not allowed" in r for r in d.reasons)
    assert k.trust.level(Category.BILLS) == 0
    assert len(ex.calls) == 1


def test_untrusted_document_values(env):
    k, ex, dev, planner = env
    d = k.submit(pay(planner, payee=("tok_attacker", None), amount=(from_rub(50_000), None)))
    assert d.outcome == Outcome.DENIED and any("unverified" in r for r in d.reasons)
    k.publish("doc:inv1:amount", Labeled(value=from_rub(1_500), label=Label.UNTRUSTED, source="invoice inv1"))
    d = k.submit(pay(planner, payee=("tok_newshop", None), amount=(from_rub(1_500), "doc:inv1:amount")))
    assert d.outcome == Outcome.PENDING and d.risk == Risk.HIGH
    assert d.card.hold_to_confirm and d.card.type_amount and d.card.standing_offer is None
    assert any(f.warning for f in d.card.fields)
    with pytest.raises(ConfirmationError, match="typed amount"):
        k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.mandate_hash), typed_amount=1)
    d2 = k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.mandate_hash), typed_amount=from_rub(1_500))
    assert d2.outcome == Outcome.EXECUTED


def test_mandate_changed_after_card_was_shown_is_rejected(env):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    sig = dev.sign_mandate(d.card.mandate_hash)
    k.pending[d.pending_id].once.payment.allowed_payees = ["tok_attacker"]
    with pytest.raises(ConfirmationError, match="does not match"):
        k.confirm(d.pending_id, dev.alg, dev.public_b64, sig)
    assert ex.calls == []


def test_client_amount_from_message_is_trusted(env):
    k, ex, dev, planner = env
    ref = k.provenance.put_client_message("m1", "Pay $3.61 for Melodify")
    d = k.submit(pay(planner, amount=(from_rub(299), ref)))
    assert d.outcome == Outcome.PENDING and d.risk == Risk.MEDIUM
    d = k.submit(pay(planner, amount=(from_rub(399), ref)))
    assert d.risk == Risk.HIGH


def test_blocklist_lookalike_domain_fake_reference_and_kill_switch(env):
    k, ex, dev, planner = env
    k.bank_value("bank:payee:mule", "tok_mule", "history")
    assert k.submit(pay(planner, payee=("tok_mule", "bank:payee:mule"))).outcome == Outcome.DENIED
    k.bank_value("bank:dom", "melodify-pay.example", "search")
    d = k.submit(pay(planner, domain={"value": "melodify-pay.example", "ref": "bank:dom"}))
    assert d.outcome == Outcome.DENIED and any("official domain" in r for r in d.reasons)
    d = k.submit(pay(planner, references=["bank:calc:savings_9999"]))
    assert d.outcome == Outcome.DENIED and any("does not exist" in r for r in d.reasons)
    k.halt("incident")
    assert k.submit(pay(planner)).outcome == Outcome.DENIED
    k.resume()
    assert k.submit(pay(planner)).outcome == Outcome.PENDING


def test_reader_and_forged_passports_cannot_propose(env):
    k, ex, dev, planner = env
    reader = k.passports.issue(Role.READER, "claude-haiku-4-5", 1, "reader", "cl_1", "task_1")
    assert k.submit(pay(reader)).outcome == Outcome.DENIED
    forged = planner.passport.model_copy(update={"client_id": "cl_2"})
    d = k.submit(pay(planner), presented=forged)
    assert d.outcome == Outcome.DENIED and "authentication failed" in d.reasons[0]
    assert k.journal.find("auth.rejected")


def test_steps_only_tighten(env):
    k, ex, dev, planner = env
    k.pipeline.critic = lambda ctx: StepResult(step="critic", verdict=Verdict.ALLOW)
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.PENDING
    k.pipeline.critic = lambda ctx: StepResult(step="critic", verdict=Verdict.DENY, reasons=["accepts a discount"])
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.DENIED and "accepts a discount" in d.reasons


def test_failed_verification_lowers_trust(env):
    k, ex, dev, planner = env
    k.pipeline.verifier = lambda intent, receipt: False
    d = k.submit(pay(planner))
    d = k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.mandate_hash))
    assert d.outcome == Outcome.FAILED and k.trust.level(Category.SUBSCRIPTIONS) == 0


def test_elevated_protection_always_asks_for_external_payments(clock):
    k = Kernel(clock=clock, seed="t2", elevated_protection=True, executor=FakeExecutor(),
               directory=InMemoryDirectory([PayeeInfo(token="tok_x", name="X", verified=True, known_to_client=False)]),
               funds=InMemoryFunds({"card_1": 10**9}))
    k.bank_value("p", "tok_x", "directory")
    k.bank_value("a", from_rub(1_000), "history")
    k.bank_value("c", "card_1", "cards")
    planner = k.passports.issue(Role.PLANNER, "m", 2, "p", "cl_1", "t")
    d = k.submit(pay(planner, payee=("tok_x", "p"), amount=(from_rub(1_000), "a"), src=("card_1", "c")))
    assert d.outcome == Outcome.PENDING
    assert "notify_trusted_person" in k.journal.entries[-1].data["flags"]


def test_client_reversal_penalises_agent_and_kind_in_the_journal(env, clock):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.standing_mandate_hash), standing=True)
    clock.advance(days=31)
    planner = new_planner(k)
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.EXECUTED and d.notify_only
    with pytest.raises(ConfirmationError, match="no executed action"):
        k.reverse("nope")
    r = k.reverse(d.intent_id, "not what I wanted")
    assert r["category"] == "subscriptions" and r["agent"] == agent_key(planner.passport)
    assert k.trust.agent(planner.passport).strikes == 1
    events = [e.event for e in k.journal.entries]
    assert "action.reversed" in events and events[-1] == "trust.changed"
    assert k.journal.entries[-1].data["event"] == "client_reversal"
    assert k.journal.verify().ok


def test_three_strikes_quarantine_agent_and_revoke_its_passports(env):
    k, ex, dev, planner = env
    d = k.submit(pay(planner))
    k.confirm(d.pending_id, dev.alg, dev.public_b64, dev.sign_mandate(d.card.standing_mandate_hash), standing=True)
    mid = k.mandates.active("cl_1")[0].mandate.id
    k.bank_value("bank:amount:big", from_rub(2_990), "invoice")
    d = k.submit(pay(planner, amount=(from_rub(2_990), "bank:amount:big"), mandate_id=mid))
    assert d.outcome == Outcome.DENIED and k.trust.agent(planner.passport).strikes == 2
    d = k.submit(pay(planner, references=["bank:invoice:ghost"]))
    assert d.outcome == Outcome.DENIED
    assert k.trust.agent(planner.passport).status == AgentStatus.QUARANTINED
    revoked = next(e for e in k.journal.entries if e.event == "agent.passports_revoked")
    assert planner.passport.id in revoked.data["passports"]
    d = k.submit(pay(planner))
    assert d.outcome == Outcome.DENIED and "revoked" in d.reasons[0]
    fresh = new_planner(k)
    assert k.trust.auto_limit(Category.SUBSCRIPTIONS, from_rub(299), fresh.passport) == 0
    d = k.submit(pay(fresh))
    assert d.outcome == Outcome.PENDING and len(ex.calls) == 1
    assert k.journal.verify().ok
