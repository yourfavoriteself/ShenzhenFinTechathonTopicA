import pytest

from guardian.duress import DuressGuard
from guardian.models import Payee, PaymentRequest, Rail
from guardian.profile import DURESS_PIN, NORMAL_PIN


def coerced(amount=120000):
    return PaymentRequest(payee=Payee(name="J. Smith", account="BSB 012-345 ACC 99887766"), amount=amount,
                          rail=Rail.DOMESTIC_TRANSFER, category="personal", memo="loan")


def test_pins():
    d = DuressGuard("1111", "2222", [])
    assert d.check_pin("1111") == "normal" and d.check_pin("2222") == "duress" and d.check_pin("0000") == "invalid"
    with pytest.raises(ValueError):
        DuressGuard("1111", "1111", [])


def test_duress_pin_looks_like_success_but_money_stays(g):
    before = g.bank.total
    out = g.request_payment(coerced(), pin=DURESS_PIN)
    assert out.status == "executed" and out.receipt["title"] == "Transfer sent"
    assert g.bank.total == before                      # nothing left the bank
    assert g.bank.quarantine == 120000                 # funds are frozen
    assert g.account_view(decoy=True)["total"] == before - 120000  # coercer sees the drop
    assert "quarantine" not in g.account_view(decoy=True)
    assert len([m for m in g.outbox if m["kind"] == "duress"]) == 2
    assert g.blocklist.hit("BSB 012-345 ACC 99887766")
    assert g.incidents and g.audit_verify().ok


def test_decoy_receipt_has_same_shape_as_real(g):
    real = g.request_payment(PaymentRequest(payee=g.bank.payees["payee_freshmart"], amount=2000, rail=Rail.CARD,
                                            category="groceries"), pin=NORMAL_PIN)
    fake = g.request_payment(coerced(5000), pin=DURESS_PIN)
    assert real.status == fake.status == "executed"
    assert set(real.receipt) == set(fake.receipt)
    assert real.receipt["reference"][:3] == fake.receipt["reference"][:3] == "TRF"


def test_wrong_pin_is_rejected(g):
    assert g.request_payment(coerced(), pin="0000").status == "rejected"


def test_soft_coercion_signals(g):
    req = PaymentRequest(payee=Payee(name="Officer", account="GB78MULE40441273920581", country="GB"), amount=200000,
                         rail=Rail.INTERNATIONAL_TRANSFER, category="government_fee",
                         source_message="This is the police, stay on the call and do not tell anyone")
    assert len(g.duress.coercion_signals(req, g.bank)) >= 3
