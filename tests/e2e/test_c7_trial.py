"""C7: a day before a free trial turns paid, Guardian asks; cancelling runs C2 under a permission that ends at the first charge."""

import re

from sandbox.sites.portal import DEMO_LOGINS

from .conftest import Client, of


def trial_item(svc):
    return next(i for i in svc.items if i.type == "trial")


def test_trial_question_on_the_day_before_the_first_charge(svc):
    it = trial_item(svc)
    assert "CloudNest" in it.text and it.data["first_charge"] == "2026-10-18" and it.data["amount"] == "149 ₽"
    assert [o["title"] for o in it.data["options"]] == ["Keep CloudNest", "Cancel CloudNest before the first charge"]


def test_cancel_trial_before_the_charge(svc, dev):
    c = Client(svc, dev)
    items = c.option([trial_item(svc)], "Cancel CloudNest")
    card = of(items, "card")[-1].data["card"]
    assert any("until 18 Oct 2026, 08:59" in x for x in card["can"]), card["can"]
    assert card["standing_offer"] is None
    items = c.yes(items)
    cid = of(items, "login")[-1].data["cancellation"]
    items = svc.cancel_login(cid, *DEMO_LOGINS[("tok_cloudnest", "card_1234")])
    code = re.search(r"code: (\d+)", svc.portal.sms[-1].text).group(1)
    items = svc.cancel_code(cid, code)
    assert of(items, "receipt")[-1].data["status"] == "verified"
    t0 = svc.clock.now()
    svc.advance(3)
    assert not [t for t in svc.bank.transactions(svc.client, since=t0) if t.counterparty == "tok_cloudnest"]


def test_trial_permission_expires_at_the_first_charge(svc, dev):
    c = Client(svc, dev)
    items = c.option([trial_item(svc)], "Cancel CloudNest")
    svc.advance(1)
    items = c.yes(items)
    assert not of(items, "login") and not of(items, "receipt")
    assert "expired at 18 Oct 2026, 08:59" in of(items, "error")[-1].text
    assert not svc.bank.is_blocked(svc.client, "tok_cloudnest", "card_1234")
