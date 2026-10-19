"""C11: investing on a US exchange from the investment envelope, inside the client's mandate."""

import pytest

from kernel.mandate import Action

from .conftest import of


def orders(svc):
    return svc.k.investing.orders


def test_first_order_asks_then_a_standing_plan_runs_alone(client, svc):
    first = client.say("Invest $10 a month in SPY")
    card = of(first, "card")[0]
    assert "SPDR S&P 500" in card.text and card.data["card"]["standing_mandate_hash"]
    done = client.yes(first, standing=True)
    receipt = of(done, "receipt")[0]
    assert "Order placed: $10 of SPDR" in receipt.text and "read the order back" in receipt.text
    assert len(orders(svc)) == 1 and orders(svc)[0].client_order_id.startswith("gr")
    svc.advance(31)
    assert orders(svc)[0].status == "filled"
    again = client.say("Invest $10 in SPY")
    assert not of(again, "card") and "standing permission" in of(again, "receipt")[0].text
    p = svc.portfolio()
    assert p["positions"][0]["symbol"] == "SPY" and p["remaining_label"] == "$30"
    assert svc.k.journal.verify().ok


@pytest.mark.parametrize("text, why", [
    ("Buy $10 of GME", "not on the client's list"),
    ("Invest $5 in SPY261218C00600000", "no options, crypto"),
    ("Buy $20 of BTC/USD", "no options, crypto"),
    ("Buy $5 of TLSS", "over the counter"),
    ("Invest $80 in VTI", "amount outside the allowed range"),
])
def test_the_broker_step_stops_orders_outside_the_mandate(client, svc, why, text):
    items = client.say(text)
    denied = of(items, "denied")
    assert denied and why in denied[0].text, [i.text for i in items]
    assert not orders(svc)


def test_a_standing_plan_cannot_be_stretched_to_another_security_or_more_money(client, svc):
    client.yes(client.say("Invest $10 a month in SPY"), standing=True)
    svc.advance(31)
    assert of(client.say("Invest $11 in QQQ"), "card"), "another security needs the client"
    over = client.say("Invest $20 in SPY")
    assert of(over, "card") and not any(o.notional_cents == 2000 for o in orders(svc)), "above the plan needs the client"
    sm = svc.k.mandates.find_standing(svc.client, Action.INVEST, "SPY")
    assert sm.mandate.payment.amount_range.max == 1200


def test_portfolio_question_shows_the_envelope(client, svc):
    items = client.say("Show my portfolio")
    assert "Investment envelope: $50 left of $50" in of(items, "portfolio")[0].text
