import numpy as np

from guardian.autonomy import conformal_threshold
from guardian.models import Payee, PaymentRequest, Rail, Initiator
from guardian.profile import risk_model


def test_conformal_threshold_toy():
    p = np.array([0.01, 0.02, 0.03, 0.5, 0.6, 0.9])
    y = np.array([0, 0, 0, 1, 1, 1])
    assert conformal_threshold(p, y, alpha=0.2) >= 0.03
    assert conformal_threshold(p, y, alpha=0.01) == 0.0


def test_guarantee_holds_on_held_out_data():
    r = risk_model().report
    assert r["test_auto_and_bad_rate"] <= r["alpha"]
    assert r["threshold"] <= r["p_cap"]
    assert r["test_auc"] > 0.95 and r["test_autonomy_rate"] > 0.5


def test_inbound_request_is_never_autonomous(g):
    req = PaymentRequest(payee=g.bank.payees["payee_freshmart"], amount=500, rail=Rail.CARD, category="groceries",
                         initiator=Initiator.INBOUND_REQUEST)
    d = g.policy.evaluate(req)
    payee, new = g.policy.resolve_payee(req.payee)
    assert g.autonomy.assess(req, payee, new, d, g.bank).decision == "confirm"


def test_expected_loss_formula(g):
    req = PaymentRequest(payee=g.bank.payees["payee_greenway"], amount=90000, rail=Rail.DOMESTIC_TRANSFER,
                         category="rent", initiator=Initiator.GUARDIAN)
    d = g.policy.evaluate(req)
    payee, new = g.policy.resolve_payee(req.payee)
    a = g.autonomy.assess(req, payee, new, d, g.bank)
    assert abs(a.expected_loss - a.p_error * 90000 * (1 - 0.3)) < 10  # p_error is rounded in the report
    assert a.decision == "auto"


def test_trust_halves_on_revert_and_recovers_slowly(g):
    eng = g.autonomy
    assert eng.record_outcome("electronics", reverted=True) == 0.5
    assert eng.record_outcome("electronics", reverted=True) == 0.25
    for _ in range(5):
        t = eng.record_outcome("electronics", reverted=False)
    assert t == 0.3125


def test_high_risk_payment_is_blocked(g):
    req = PaymentRequest(payee=Payee(name="Unknown", account="bc1qgpma2x56kf7hl794ju05xjtx49sv2plyxpgc4q"),
                         amount=200000, rail=Rail.CRYPTO, category="investment", initiator=Initiator.INBOUND_REQUEST,
                         source_message="urgent! guaranteed returns, do not tell anyone")
    d = g.policy.evaluate(req)
    payee, new = g.policy.resolve_payee(req.payee)
    assert g.autonomy.assess(req, payee, new, d, g.bank).decision == "block"
