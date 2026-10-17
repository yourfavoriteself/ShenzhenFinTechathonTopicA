from guardian.models import Initiator, Payee, PaymentRequest, Rail


def test_payday_allocates_whole_income(g):
    assert g.bank.unallocated == 0
    assert {e.id: e.balance for e in g.bank.envelopes.values()} == {
        "rent": 90000, "tuition": 30000, "bills": 12000, "food": 40000, "transport": 8000, "savings": 50000,
        "free": 30000}


def test_bills_are_paid_autonomously(g):
    out = g.run_due_bills()
    assert [o.status for o in out] == ["executed"]          # only rent is due on the 1st
    g.clock.advance(days=2)
    assert [o.status for o in g.run_due_bills()] == ["executed", "executed"]
    assert g.run_due_bills() == []                           # never paid twice


def test_revert_reduces_autonomy(g):
    out = g.run_due_bills()[0]
    res = g.revert(out.tx_id)
    assert res["new_trust"] == 0.5 and g.autonomy.trust_for("rent") == 0.5
    assert g.bank.env("rent").balance == 90000


def test_confirm_rechecks_policy(g):
    req = PaymentRequest(payee=Payee(name="Tom", account="payid:tom@uni.example"), amount=12000,
                         rail=Rail.DOMESTIC_TRANSFER, category="personal", initiator=Initiator.INBOUND_REQUEST)
    out = g.request_payment(req)
    assert out.status == "pending_confirmation"
    g.bank.env("free").balance = 0  # the money was spent elsewhere meanwhile
    assert g.confirm(out.pending_id, approve=True).status == "blocked"


def test_decline(g):
    req = PaymentRequest(payee=Payee(name="Tom", account="payid:tom@uni.example"), amount=12000,
                         rail=Rail.DOMESTIC_TRANSFER, category="personal", initiator=Initiator.INBOUND_REQUEST)
    out = g.request_payment(req)
    assert g.confirm(out.pending_id, approve=False).status == "declined"


def test_shadow_run_detects_ledger_errors(g):
    req = PaymentRequest(payee=g.bank.payees["payee_freshmart"], amount=10**7, rail=Rail.CARD, category="groceries")
    assert g._shadow_run(req, "food")


def test_audit_chain_valid_after_full_day(g):
    g.run_due_bills()
    g.bait("rent_redirect")
    assert g.audit_verify().ok and len(g.audit.entries) > 10
