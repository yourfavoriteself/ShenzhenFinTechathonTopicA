"""C10: before paying an account number, the kernel scores it on the bank's own transfer graph."""

from datetime import datetime

from kernel.clock import MSK
from risk_graph.features import NAMES, Index
from risk_graph.generator import SCHEMES, generate
from risk_graph.model import evaluate

from .conftest import of

MULE, ORDINARY = "40817810500001234567", "40817810500009998887"


def test_planted_collector_scores_high_and_the_ordinary_account_low(svc):
    p, why = svc.graph.score(MULE)
    assert p >= 0.7 and any("different people within 48 hours" in w for w in why)
    assert svc.graph.score(ORDINARY)[0] < 0.4
    assert svc.graph.score("40817810599999999999") is None          # unknown to the bank: no opinion


def test_mule_payee_without_a_call_triggers_the_pause(svc, client):
    card = of(client.say(f"Transfer $241.40 to account {MULE}"), "card")[-1].data["card"]
    assert any("money mule (graph score" in r for r in card["reasons"])
    iv = card["intervention"]
    assert iv["level"] == 2 and any("money-mule" in s for s in iv["signals"])


def test_ordinary_payee_is_not_flagged(svc, client):
    card = of(client.say(f"Transfer $60.35 to account {ORDINARY}"), "card")[-1].data["card"]
    assert not any("graph score" in r for r in card["reasons"]) and card["intervention"] is None


def test_generator_plants_all_four_schemes_and_honest_look_alikes():
    g = generate(7, datetime(2026, 10, 17, 10, tzinfo=MSK))
    assert {v for v in g.label.values() if v} == set(SCHEMES)
    idx = Index(g.transfers, g.opened, g.as_of)
    ring = next(a for a, v in g.label.items() if v == "ring")
    f = dict(zip(NAMES, idx.features(ring)))
    assert f["in_cycle"] == 1.0
    employer = next(a for a, k in g.kind.items() if k == "employer")
    assert dict(zip(NAMES, idx.features(employer)))["in_cycle"] == 0.0


def test_model_quality_on_unseen_graphs():
    r = evaluate(datetime(2026, 10, 17, 10, tzinfo=MSK))
    assert set(r["train_seeds"]).isdisjoint(r["test_seeds"])
    assert r["roc_auc"] > 0.9 and r["recall"] > 0.8 and r["false_positive_rate"] < 0.01
    assert all(v > 0.7 for v in r["recall_per_scheme"].values())
