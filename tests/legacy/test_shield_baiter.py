from guardian.baiter import PIIFilter, ScamBaiter, load_scenarios
from guardian.core import DATA_DIR
from guardian.intel import bech32_valid, extract_indicators, iban_valid, luhn_valid
from guardian.models import Payee, PaymentRequest, Rail

SCEN = load_scenarios(DATA_DIR / "scam_scripts.json")


def test_validators():
    assert iban_valid("GB78 MULE 4044 1273 9205 81") and not iban_valid("GB79 MULE 4044 1273 9205 81")
    assert bech32_valid("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    assert not bech32_valid("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")
    assert luhn_valid("4111 1111 1111 1111") and not luhn_valid("4111 1111 1111 1112")


def test_extraction_kinds():
    kinds = {i.kind for i in extract_indicators(
        "IBAN GB78 MULE 4044 1273 9205 81, call +61 491 570 156, see https://x.example/a, PayID a.b@c.example, @handle_123")}
    assert {"iban", "phone", "url", "email", "handle"} <= kinds


def test_scam_messages_are_flagged_and_payments_blocked(g):
    for s in SCEN.values():
        a, out = g.handle_message(s["opening"], s["sender"])
        assert a.verdict == "scam", s["id"]
        assert out.status == "blocked", s["id"]
    assert g.bank.total == 260000


def test_rent_redirect_detected_as_known_payee_mismatch(g):
    a = g.analyze_message(SCEN["rent_redirect"]["opening"], "Greenway Property Management")
    assert any("existing payee" in s for s in a.signals)


def test_legit_request_needs_approval_not_blocked(g):
    a, out = g.handle_message("Hey Mia, it's Tom. You owe me $24 for the pizza, my PayID is tom.lee@uni-mail.example.",
                              "Tom (classmate)")
    assert a.verdict == "low_risk" and out.status == "pending_confirmation"
    done = g.confirm(out.pending_id, approve=True)
    assert done.status == "executed"


def test_baiter_harvests_more_intel_and_closes_the_loop(g):
    report, added = g.bait("consulate_impersonation")
    assert report.indicators_in_opening == 1 and len(added) >= 5 and report.money_sent == 0
    second_mule = PaymentRequest(payee=Payee(name="Inspection", account="LT977044060008315520", country="LT"),
                                 amount=5000, rail=Rail.INTERNATIONAL_TRANSFER, category="government_fee")
    out = g.request_payment(second_mule)
    assert out.status == "blocked" and out.policy.blocked_by == "blocklist"


class LeakyLLM:
    available = True

    def chat(self, *a, **k):
        return "Sure, my card is 4000 0566 5566 5556 and I live at 21 Example Lane"


def test_pii_filter_blocks_leaks_from_llm():
    baiter = ScamBaiter(PIIFilter(["21 Example Lane", "4000 0566 5566 5556"]), LeakyLLM())
    report = baiter.run(SCEN["crypto_investment"], max_turns=3)
    persona = [t for t in report.turns if t.speaker == "persona"]
    assert all(t.filtered for t in persona) and report.pii_leaks_blocked == 3
    assert not any("5556" in t.text for t in persona)


def test_baiter_cannot_poison_blocklist_with_verified_payee(g):
    scenario = dict(SCEN["rent_redirect"])
    scenario["reveals"] = [{"triggers": ["account", "details", "where"],
                            "message": "Fine, pay the usual PayID rent@greenwaypm.example then."}] + scenario["reveals"]
    report = g.baiter.run(scenario)
    assert any("rent@greenwaypm.example" in i.value for i in report.indicators)
    import guardian.core as core
    orig = core.load_scenarios
    core.load_scenarios = lambda _p: {"rent_redirect": scenario}
    try:
        _, added = g.bait("rent_redirect")
    finally:
        core.load_scenarios = orig
    assert not g.blocklist.hit("payid:rent@greenwaypm.example")
    assert not any("greenwaypm" in a for a in added)
