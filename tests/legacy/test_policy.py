from datetime import datetime, timezone

from guardian.models import Payee, PaymentRequest, Rail


def rent_request(g, account="payid:rent@greenwaypm.example", amount=90000):
    return PaymentRequest(payee=Payee(name="Greenway Property Management", account=account), amount=amount,
                          rail=Rail.DOMESTIC_TRANSFER, category="rent")


def test_rent_to_verified_landlord_in_window_is_allowed(g):
    d = g.policy.evaluate(rent_request(g))
    assert d.allowed and d.envelope_name == "Rent"


def test_rent_outside_day_window_is_blocked(g):
    g.clock.set(datetime(2026, 10, 12, 9, tzinfo=timezone.utc))
    d = g.policy.evaluate(rent_request(g))
    assert not d.allowed
    rent = next(c for c in d.checks if c.envelope_name == "Rent")
    assert any("days 1-5" in r for r in rent.reasons)


def test_redirected_rent_account_is_blocked(g):
    d = g.policy.evaluate(rent_request(g, account="payid:greenway.pm.accounts@mailbox.example"))
    assert not d.allowed and d.blocked_by == "no_envelope"


def test_request_cannot_claim_verified_payee(g):
    p = Payee(name="Scammer", account="payid:x@evil.example", verified=True)
    resolved, is_new = g.policy.resolve_payee(p)
    assert resolved.verified is False and is_new


def test_new_payee_cap_in_free_envelope(g):
    req = PaymentRequest(payee=Payee(name="Someone", account="payid:someone@mail.example"), amount=20000,
                         rail=Rail.DOMESTIC_TRANSFER, category="personal")
    assert not g.policy.evaluate(req).allowed
    req.amount = 12000
    assert g.policy.evaluate(req).envelope_name == "Free money"


def test_savings_locked(g):
    req = PaymentRequest(payee=Payee(name="Me", account="internal:me"), amount=1000, rail=Rail.INTERNAL,
                         category="other", envelope_hint="savings")
    d = g.policy.evaluate(req)
    savings = next(c for c in d.checks if c.envelope_name == "Savings")
    assert not savings.ok and any("locked" in r for r in savings.reasons)


def test_blocklist_overrides_envelopes(g):
    g.blocklist.add("payid:rent@greenwaypm.example", "account", "test")
    d = g.policy.evaluate(rent_request(g))
    assert not d.allowed and d.blocked_by == "blocklist"


def test_worst_case_exposure_is_small(g):
    assert g.bank.total == 260000
    assert g.policy.worst_case_exposure() == 15000
