from fastapi.testclient import TestClient

from guardian import demo, redteam
from guardian.api import app


def test_demo_story_runs_and_audit_verifies(tmp_path):
    r = demo.run(tmp_path)
    assert r["summary"]["audit_ok"] and r["summary"]["steps"] >= 12
    assert (tmp_path / "demo_run" / "audit_log.jsonl").exists()


def test_redteam_no_attack_succeeds_and_no_legit_blocked():
    s = redteam.run()["summary"]
    assert s["succeeded"] == 0 and s["controls_blocked"] == 0
    assert s["lost_guardian"] <= 30000 < s["lost_static_rules"] < s["lost_unguarded_agent"]


def test_api_flow():
    c = TestClient(app)
    c.post("/api/reset", json={})
    assert c.post("/api/clock", json={"action": "payday"}).json()["state"]["total"] == 260000
    r = c.post("/api/pay", json={"payee_name": "J. Smith", "account": "BSB 012-345 ACC 99887766", "amount": 500,
                                 "rail": "domestic_transfer", "category": "personal", "pin": "9731"}).json()
    assert r["outcome"]["status"] == "executed" and r["state"]["decoy"] is True
    assert r["state"]["incidents"] == [] and r["state"]["quarantine"] is None  # nothing leaks to the coercer
    assert c.post("/api/duress/resolve", json={}).status_code == 403
    c.post("/api/session/login", json={"pin": "1379"})
    assert c.post("/api/duress/resolve", json={}).json()["result"]["released"] == 50000
    assert c.get("/api/audit").json()["verification"]["ok"]
    assert c.post("/api/audit/tamper", json={"mode": "edit"}).json()["verification"]["ok"] is False
