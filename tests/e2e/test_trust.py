"""Earned trust: a faulty planner collects strikes and is quarantined; the client can reverse a payment."""

from fastapi.testclient import TestClient

from app.web import create_web_app
from kernel.trust import AgentStatus

from .conftest import of
from .test_scenarios import close_gap, open_budget


def standing_melodify(client, svc):
    close_gap(client)
    open_budget(client)
    client.yes(client.say("Pay for Melodify"), standing=True)


def planner(svc):
    return next(r for r in svc.k.trust.agents.values() if r.role == "planner")


def test_faulty_planner_is_demoted_then_quarantined(client, svc):
    standing_melodify(client, svc)
    first = svc.faulty_agent()
    assert of(first, "fault") and "denied" in [i.type for i in first]
    assert any("Trust lowered" in i.text and "to 0" in i.text for i in of(first, "trust"))
    assert planner(svc).strikes == 2
    second = svc.faulty_agent()
    assert any("does not exist" in i.text for i in second if i.type == "denied")
    assert any("quarantined for 24 hours" in i.text for i in of(second, "trust"))
    assert planner(svc).status == AgentStatus.QUARANTINED
    assert any(e.event == "agent.passports_revoked" for e in svc.k.journal.entries)
    svc.advance(31)
    items = client.say("Pay for Melodify")
    assert of(items, "card"), "a quarantined or probation agent cannot act alone"
    assert not [x for x in svc.k.journal.entries if x.event == "decision.executed" and x.data.get("action") == "pay"
                and x.data.get("amount", 0) > 34400]
    assert svc.k.journal.verify().ok


def test_client_can_undo_a_payment_made_alone(client, svc):
    standing_melodify(client, svc)
    svc.advance(31)
    receipt = of(client.say("Pay for Melodify"), "receipt")[-1]
    assert receipt.data["notify_only"]
    items = client.option([receipt], "Undo")
    assert any("Reversal opened" in i.text for i in items)
    assert any("Trust lowered" in i.text for i in of(items, "trust"))
    assert any(e.event == "action.reversed" for e in svc.k.journal.entries)


def test_trust_section_and_fault_endpoint(svc, dev):
    web = TestClient(create_web_app(svc))
    assert web.post("/api/demo/faulty_agent").status_code == 200
    s = web.get("/api/sections").json()["trust"]
    assert {"levels", "agents", "history", "quarantines"} <= set(s)
    assert s["agents"] and s["history"]
