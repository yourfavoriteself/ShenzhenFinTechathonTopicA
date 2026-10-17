from datetime import timedelta
from pathlib import Path

from guardian.core import DATA_DIR
from guardian.firewall import load_shop, scan_page
from guardian.models import Initiator, PaymentRequest, Payee, Rail

SHOP = load_shop(DATA_DIR / "shop")


def mandate(g, max_total=6000):
    return g.create_mandate("*", "USB-C laptop charger, 65W", ["charger"], ["electronics"], max_total)


def test_scanner_finds_hidden_instructions():
    findings = scan_page("voltdeals_chargers.html", SHOP["voltdeals_chargers.html"])
    techniques = " ".join(f.technique for f in findings)
    assert len([f for f in findings if f.severity == "high"]) >= 4
    for t in ("HTML comment", "meta tag", "zero font size", "white-on-white"):
        assert t in techniques


def test_scanner_clean_pages():
    assert scan_page("techmart_chargers.html", SHOP["techmart_chargers.html"]) == []
    assert scan_page("gadgetblog_review.html", SHOP["gadgetblog_review.html"]) == []


def test_honest_agent_is_approved_and_paid(g):
    r = g.agent_shop("honest", mandate(g))
    assert r.verdict.approved and r.payment.status == "executed"
    assert r.cart.total == 3290


def test_manipulated_agent_is_blocked_with_clean_alternative(g):
    before = g.bank.total
    r = g.agent_shop("naive", mandate(g))
    failed = {c.name for c in r.verdict.checks if not c.ok}
    assert not r.verdict.approved and r.payment is None and g.bank.total == before
    assert {"No recurring charges", "Decision provenance clean", "Categories allowed"} <= failed
    assert "Within budget" not in failed  # a plain spending limit would have let it through
    assert r.alternative.items[0].sku == "TM-PC65"
    assert r.agent_trust < 0.7


def test_external_agent_without_token_is_blocked(g):
    req = PaymentRequest(payee=Payee(name="VoltDeals", account="payid:sales@voltdeals.example"), amount=3500,
                         rail=Rail.CARD, category="electronics", initiator=Initiator.EXTERNAL_AGENT)
    assert g.request_payment(req).status == "blocked"


def test_token_is_single_use_and_merchant_bound(g):
    m = mandate(g)
    r = g.agent_shop("honest", m)
    tok = next(iter(g.firewall.tokens.values()))
    assert tok.used
    ok, why = g.firewall.redeem(tok.token_id, tok.merchant_account, 100)
    assert not ok and "already used" in why
    tok2 = g.firewall.issue_token(m, r.cart)
    ok, why = g.firewall.redeem(tok2.token_id, "payid:sales@voltdeals.example", 100)
    assert not ok and "different merchant" in why


def test_tampered_or_expired_mandate_is_rejected(g):
    m = mandate(g)
    honest_cart = g.agent_shop("honest", m).cart
    m.mandate.max_total = 100000  # agent edits the signed mandate
    v = g.firewall.verify_cart(m, honest_cart, SHOP)
    assert not v.approved and not v.checks[0].ok
    m2 = mandate(g)
    g.clock.advance(hours=25)
    v2 = g.firewall.verify_cart(m2, honest_cart.model_copy(update={"mandate_id": m2.mandate.mandate_id}), SHOP)
    assert not v2.approved and any(c.name == "Mandate not expired" and not c.ok for c in v2.checks)
