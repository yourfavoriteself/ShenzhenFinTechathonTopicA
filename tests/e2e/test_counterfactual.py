"""Counterfactual check: a high-risk payment is replayed with the client's request masked."""

import json

from .conftest import of


def obedient_planner(req: dict) -> str:
    """A planner that ignores the client and follows whatever the facts push (an injected model)."""
    p = json.loads(req["user"])
    c = p["facts"]["payees"][0]
    src = p["facts"]["funding_sources"][0]
    amount = c.get("usual_amount") or c.get("price")
    return json.dumps({"intents": [{"action": "pay", "payee_ref": c["payee"]["ref"],
                                    "amount_ref": amount["ref"] if amount else None,
                                    "funding_ref": src["source"]["ref"], "memo": "pay now"}],
                       "note": "", "confidence": 0.9})


def counterfactual_events(svc):
    return [e.data for e in svc.k.journal.entries if e.event == "counterfactual.checked"]


def test_honest_planner_passes_the_masked_replay(svc, client):
    items = client.say("Pay for CloudNest")          # never paid before: high risk
    card = of(items, "card")[-1].data
    assert card["card"]["risk"] == 2
    ev = counterfactual_events(svc)
    assert ev and ev[-1]["same_action"] is False and ev[-1]["masked_proposals"] == 0


def test_payment_that_does_not_depend_on_the_request_is_stopped(svc, client):
    svc.gw.register_stub("planner", obedient_planner)
    items = client.say("Pay for CloudNest")
    denied = of(items, "denied")[-1]
    assert "even without your request" in denied.text
    assert counterfactual_events(svc)[-1]["same_action"] is True
    assert not of(items, "card")


def test_lower_risk_actions_skip_the_replay(svc, client):
    client.say("Pay for Melodify")                   # paid every month: medium risk, no replay
    assert svc.cf.runs == 0 and not counterfactual_events(svc)
