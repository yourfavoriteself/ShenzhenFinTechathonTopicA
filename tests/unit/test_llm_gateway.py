import json

import pytest
from pydantic import BaseModel

from kernel.llm_gateway import (BudgetExceeded, LLMFailure, LLMGateway, Mode, ReplayMiss, SecretLeak,
                                TaskBudgetExceeded)


class Answer(BaseModel):
    category: str
    confidence: float


def fake_provider(texts, calls):
    def call(spec, req):
        calls.append((spec.model, req["level"]))
        return {"text": texts.pop(0) if texts else '{"category": "music", "confidence": 0.9}',
                "input_tokens": 1000, "output_tokens": 200}
    return call


@pytest.fixture
def gw(tmp_path):
    def make(mode="live", **kw):
        g = LLMGateway(mode, runs_dir=tmp_path / "runs", spend_path=tmp_path / "spend.json", usd_rub=100, **kw)
        return g
    return make


def test_stub_mode_never_touches_providers(gw):
    g = gw("stub")
    g.register_stub("reader", lambda req: '{"category": "bills", "confidence": 1}')
    r = g.call("reader", "Classify.", "Mosenergo bill", schema=Answer)
    assert r.parsed == {"category": "bills", "confidence": 1.0} and r.source == "stub"
    assert r.estimated and r.input_tokens > 0 and g.ledger.total_rub == 0


def test_live_call_is_costed_and_persisted(gw, tmp_path):
    g = gw()
    calls = []
    g.providers["anthropic"] = fake_provider([], calls)
    r = g.call("reader", "Classify.", "Melodify 299", schema=Answer)
    assert r.parsed["category"] == "music" and calls == [("claude-haiku-4-5", 1)]
    assert r.cost_rub == pytest.approx((1000 * 1 + 200 * 5) / 1e6 * 100)
    saved = json.loads((tmp_path / "spend.json").read_text())
    assert saved["total_rub"] == pytest.approx(r.cost_rub)


def test_hard_cap_stops_before_the_call(gw):
    g = gw(budget_rub=0.01)
    calls = []
    g.providers["anthropic"] = fake_provider([], calls)
    with pytest.raises(BudgetExceeded):
        g.call("planner", "Plan.", "x" * 3000, max_tokens=4000)
    assert calls == []


def test_cap_survives_restart(gw, tmp_path):
    g = gw(budget_rub=0.6)
    g.providers["anthropic"] = fake_provider([], [])
    g.call("reader", "Classify.", "a", schema=Answer)
    g2 = gw(budget_rub=0.6)
    g2.providers["anthropic"] = fake_provider([], [])
    assert g2.ledger.total_rub == pytest.approx(0.2)
    with pytest.raises(BudgetExceeded):
        g2.call("reader", "Classify.", "b", schema=Answer)


def test_task_budget_stops_the_task(gw):
    g = gw(task_budget_rub=0.5)
    g.providers["anthropic"] = fake_provider([], [])
    g.call("reader", "Classify.", "a", schema=Answer, max_tokens=100, task_id="t1")
    g.call("reader", "Classify.", "b", schema=Answer, max_tokens=100, task_id="t1")
    with pytest.raises(TaskBudgetExceeded):
        for _ in range(5):
            g.call("reader", "Classify.", "c", schema=Answer, max_tokens=100, task_id="t1")
    g.call("reader", "Classify.", "d", schema=Answer, max_tokens=100, task_id="t2")


def test_schema_failure_retries_then_escalates(gw):
    g = gw()
    calls = []
    g.providers["anthropic"] = fake_provider(["not json", '{"category": 5}'], calls)
    r = g.call("reader", "Classify.", "a", schema=Answer)
    assert [lvl for _, lvl in calls] == [1, 1, 2]
    assert r.level == 2 and len(r.escalations) == 2


def test_low_confidence_escalates(gw):
    g = gw()
    calls = []
    g.providers["anthropic"] = fake_provider(['{"category": "x", "confidence": 0.3}'], calls)
    r = g.call("reader", "Classify.", "a", schema=Answer, min_confidence=0.7)
    assert r.level == 2 and "low confidence" in r.escalations[0]


def test_gives_up_after_top_level(gw):
    g = gw()
    g.providers["anthropic"] = fake_provider(["bad"] * 6, [])
    with pytest.raises(LLMFailure):
        g.call("reader", "Classify.", "a", schema=Answer)


def test_record_then_replay_without_keys(gw):
    g = gw("record")
    g.providers["anthropic"] = fake_provider([], [])
    first = g.call("reader", "Classify.", "Melodify 299", schema=Answer)
    r = gw("replay")
    r.providers["anthropic"] = lambda *a: pytest.fail("replay must not call the provider")
    again = r.call("reader", "Classify.", "Melodify 299", schema=Answer)
    assert again.parsed == first.parsed and again.source == "replay" and again.cost_rub == first.cost_rub
    assert r.ledger.total_rub == pytest.approx(first.cost_rub)
    with pytest.raises(ReplayMiss):
        r.call("reader", "Classify.", "something else", schema=Answer)


@pytest.mark.parametrize("text", ["card 4111 1111 1111 1111 please", "pan 5500-0000-0000-0004"])
def test_card_number_never_reaches_a_model(gw, text):
    g = gw("stub")
    g.register_stub("reader", lambda req: pytest.fail("must not be called"))
    with pytest.raises(SecretLeak):
        g.call("reader", "Classify.", text)


def test_registered_secret_and_non_luhn_numbers(gw):
    g = gw("stub")
    g.register_stub("reader", lambda req: "ok")
    g.call("reader", "Classify.", "account 40817810099910004312 and phone 79161234567")
    g.guard.register("hunter2-streambox")
    with pytest.raises(SecretLeak):
        g.call("reader", "Classify.", "password is hunter2-streambox")


def test_report_by_level(gw):
    g = gw()
    g.providers["anthropic"] = fake_provider(["bad", "bad"], [])
    g.call("reader", "Classify.", "a", schema=Answer)
    rep = g.report()
    assert rep["by_level"][2]["calls"] == 1 and rep["spent_total_rub"] > 0
