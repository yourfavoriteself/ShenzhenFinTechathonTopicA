"""C9: a client on the phone with a fake "bank security" asks to move savings to a "safe account"."""

from kernel.money import from_rub

from .conftest import Client, of

SCAM = ("Transfer $4,224.48 from my savings to safe account 40817810500001234567, "
        "the bank security officer said my money is at risk")
ACCT = "acct:40817810500001234567"
OTHER = "acct:40817810500007776665"
NO = {"on_call": False, "pressure": False, "given_account": False}
YES = {"on_call": True, "pressure": False, "given_account": False}


def sign(svc, dev, items, **kw):
    card = of(items, "card")[-1]
    return svc.confirm(card.data["pending_id"], dev.alg, dev.public_b64,
                       dev.sign_mandate(card.data["card"]["mandate_hash"]), **kw)


def sent(svc, token=ACCT):
    return [t for t in svc.bank.transactions(svc.client) if t.counterparty == token]


def test_ordinary_transfer_without_a_call_has_no_intervention(svc, client):
    items = client.say("Transfer $60.35 to account 40817810500009998887")
    card = of(items, "card")[-1].data["card"]
    assert card["intervention"] is None and card["title"] == "Pay account *8887"


def test_level_1_explains_the_scheme_and_asks_to_type_the_amount(svc, client, dev):
    svc.scam_call(True, spoofed=False)
    items = client.say("Transfer $60.35 to account 40817810500009998887")
    card = of(items, "card")[-1].data["card"]
    assert card["intervention"]["level"] == 1 and "safe account" in card["intervention"]["explain"]
    assert card["type_amount"]
    assert "typed amount" in of(sign(svc, dev, items), "error")[-1].text
    items = client.say("Transfer $60.35 to account 40817810500009998887")
    assert of(sign(svc, dev, items, typed_amount=from_rub(5_000)), "receipt")


def test_safe_account_call_goes_to_a_live_analyst(svc, client, dev):
    svc.scam_call(True)
    save0 = svc.bank.accounts["acc_save"].balance
    items = client.say(SCAM)
    iv = of(items, "card")[-1].data["card"]["intervention"]
    assert iv["level"] == 4 and iv["pin"] and set(iv["questions"]) == set(NO)
    assert any("spoofed" in s for s in iv["signals"]) and any("scam wording" in s for s in iv["signals"])
    err = of(sign(svc, dev, items, typed_amount=from_rub(350_000), answers=NO, pin="2580"), "error")[-1].text
    assert "pause" in err and "10:15" in err
    svc.wait(16)
    assert "three questions" in of(sign(svc, dev, items, typed_amount=from_rub(350_000), pin="2580"), "error")[-1].text
    assert "wrong PIN" in of(sign(svc, dev, items, typed_amount=from_rub(350_000), answers=NO, pin="1111"), "error")[-1].text
    held = of(sign(svc, dev, items, typed_amount=from_rub(350_000), answers=NO, pin="2580"), "held")[-1]
    assert "bank analyst" in held.text and held.data["case"]["human_only"]
    svc.advance(3)
    assert not sent(svc), "level 4 never releases by time"
    svc.analyst_decide(held.data["hold"], approve=False, note="client confirmed the call was a scam")
    assert not sent(svc) and svc.bank.accounts["acc_save"].balance == save0
    events = [e.event for e in svc.k.journal.entries]
    assert "intervention.analyst_decided" in events and "intervention.released" not in events


def test_yes_answer_moves_to_a_reversible_24_hour_hold_and_tells_the_trusted_person(svc, client, dev):
    svc.scam_call(True, spoofed=False)
    items = client.say("Transfer $3,620.98 from my savings to account 40817810500007776665")
    assert of(items, "card")[-1].data["card"]["intervention"]["level"] == 2
    svc.wait(15)
    items = sign(svc, dev, items, typed_amount=from_rub(300_000), answers=YES, pin="2580")
    assert "Maria" in of(items, "trusted")[-1].text
    held = of(items, "held")[-1]
    assert held.data["level"] == 3 and "until 18 Oct, 10:15" in held.text
    client.option([held], "Cancel the transfer")
    svc.advance(2)
    assert not sent(svc, OTHER)


def test_held_transfer_is_released_after_24_hours_if_nobody_cancels(svc, client, dev):
    svc.scam_call(True, spoofed=False)
    items = client.say("Transfer $3,620.98 from my savings to account 40817810500007776665")
    svc.wait(15)
    sign(svc, dev, items, typed_amount=from_rub(300_000), answers=YES, pin="2580")
    svc.scam_call(False)
    assert not sent(svc, OTHER)
    items = svc.advance(1)
    assert of(items, "receipt") and sent(svc, OTHER)[0].amount == from_rub(300_000)


def test_duress_pin_shows_an_ordinary_receipt_but_nothing_leaves(svc, client, dev):
    svc.scam_call(True)
    items = client.say(SCAM)
    svc.wait(15)
    items = sign(svc, dev, items, typed_amount=from_rub(350_000), answers=NO, pin="0852")
    r = of(items, "receipt")[-1]
    assert r.text.startswith("Sent $4,224.48 to account *4567") and not of(items, "held")
    assert not of(items, "trusted") and svc.alerts[-1]["silent"]
    h = next(iter(svc.guard.holds.values()))
    assert h.duress and h.level == 4 and h.status == "held"
    svc.advance(3)
    assert not sent(svc)


def test_elevated_protection_starts_at_the_pause(svc, client):
    svc.set_protection(True)
    svc.scam_call(True, spoofed=False)
    items = client.say("Transfer $60.35 to account 40817810500009998887")
    iv = of(items, "card")[-1].data["card"]["intervention"]
    assert iv["level"] == 2 and iv["elevated"] and iv["wait_until"]
