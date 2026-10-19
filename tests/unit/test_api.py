import pytest
from fastapi.testclient import TestClient

from kernel.api import create_app
from kernel.money import from_rub
from tests.unit.test_pipeline import env, new_planner, pay  # noqa: F401


@pytest.fixture
def api(env):  # noqa: F811
    k, ex, dev, planner = env
    return TestClient(create_app(k, operator_token="op-secret")), k, ex, dev, planner


def body(req):
    return req.model_dump(mode="json")


def test_full_flow_over_http(api):
    c, k, ex, dev, planner = api
    d = c.post("/agent/propose_intent", json=body(pay(planner))).json()
    assert d["outcome"] == "pending_confirmation"
    card = c.get(f"/cards/{d['pending_id']}").json()
    sig = dev.sign_mandate(card["standing_mandate_hash"])
    r = c.post(f"/cards/{d['pending_id']}/confirm",
               json={"device_alg": dev.alg, "device_key": dev.public_b64, "device_sig": sig, "standing": True})
    assert r.json()["outcome"] == "executed" and len(ex.calls) == 1
    ms = c.get("/clients/cl_1/mandates").json()
    assert len(ms) == 1 and ms[0]["spent"] == from_rub(299) and "Melodify" in ms[0]["can"][0]
    assert c.post(f"/mandates/{ms[0]['id']}/revoke").json()["revoked"] == [ms[0]["id"]]
    assert c.get("/clients/cl_1/mandates").json() == []
    assert c.get("/journal/verify").json()["ok"]
    cp = c.get("/journal/checkpoint").json()
    assert cp["merkle_root"] and cp["public_key"] == k.journal.public_key


def test_wrong_signature_and_replay_over_http(api):
    c, k, ex, dev, planner = api
    d = c.post("/agent/propose_intent", json=body(pay(planner))).json()
    card = c.get(f"/cards/{d['pending_id']}").json()
    bad = c.post(f"/cards/{d['pending_id']}/confirm", json={"device_alg": dev.alg, "device_key": dev.public_b64,
                                                            "device_sig": dev.sign_mandate("0" * 64)})
    assert bad.status_code == 409 and ex.calls == []
    req = body(pay(planner))
    c.post("/agent/propose_intent", json=req)
    again = c.post("/agent/propose_intent", json=req).json()
    assert again["outcome"] == "denied" and "replay" in again["reasons"][0]


def test_endpoint_mismatch_and_tampered_body(api):
    c, k, ex, dev, planner = api
    req = body(planner.request("query_data", {"intent": {}}))
    assert c.post("/agent/propose_intent", json=req).status_code == 400
    req = body(pay(planner))
    req["body"]["intent"]["amount"]["value"] = 2990000
    d = c.post("/agent/propose_intent", json=req).json()
    assert d["outcome"] == "denied" and "session key" in d["reasons"][0]


def test_kill_switch_needs_operator_token(api):
    c, k, ex, dev, planner = api
    assert c.post("/operator/halt", json={"reason": "x"}).status_code == 403
    assert c.post("/operator/halt", json={"reason": "x"}, headers={"X-Operator-Token": "nope"}).status_code == 403
    assert c.post("/operator/halt", json={"reason": "incident"}, headers={"X-Operator-Token": "op-secret"}).status_code == 200
    d = c.post("/agent/propose_intent", json=body(pay(planner))).json()
    assert d["outcome"] == "denied" and c.get("/health").json()["halted"] == "incident"
    c.post("/operator/resume", headers={"X-Operator-Token": "op-secret"})
    assert c.post("/agent/propose_intent", json=body(pay(planner))).json()["outcome"] == "pending_confirmation"
