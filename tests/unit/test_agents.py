import json

import pytest

from agents.dispatcher import Dispatcher, rule_route
from agents.planner import Planner
from kernel.llm_gateway import LLMGateway, Mode
from kernel.templates import Template, TemplateStore


@pytest.mark.parametrize("text,kind", [
    ("Pay for Melodify", "pay"), ("заплати за Melodify", "pay"), ("Pay my bills", "pay_bills"),
    ("Оплати счета", "pay_bills"), ("Find leaks in my subscriptions", "find_leaks"),
    ("Was I charged twice?", "double_charges"), ("Make me a budget", "budget"),
    ("How much can I spend today?", "safe_to_spend"), ("Can I afford headphones for 15 000?", "can_afford"),
    ("Will I have a cash gap?", "cash_gap"), ("What can you do?", "permissions"),
])
def test_level0_rules(text, kind):
    assert rule_route(text).kind == kind


def test_payee_and_amount_copied_as_written():
    assert rule_route("Pay for Melodify").payee_name == "Melodify"
    r = rule_route("Can I afford headphones for 15k?")
    assert r.amount == "15000" and r.item == "headphones"


def test_unmatched_message_goes_to_level1(tmp_path):
    gw = LLMGateway("stub", spend_path=tmp_path / "s.json")
    gw.register_stub("dispatcher", lambda req: json.dumps({"kind": "find_leaks", "confidence": 0.8}))
    r, level = Dispatcher(gw).route("I'm worried about my finances lately", "t1")
    assert r.kind == "find_leaks" and level == 1 and gw.log[-1].role == "dispatcher"


def test_templates_match_by_keywords():
    from datetime import datetime
    store = TemplateStore()
    t = store.save(Template(client_id="cl_1", action="pay", payee="tok_melodify", payee_name="Melodify", amount=29900,
                            funding_source="env_subs", keywords=["melodify"], created=datetime(2026, 10, 17)))
    assert store.match("cl_1", "pay", "pay for Melodify again") is t
    assert store.match("cl_1", "pay", "pay for TuneWave") is None and store.match("cl_2", "pay", "melodify") is None
    again = store.save(t.model_copy(update={"amount": 34900}))
    assert len(store.items) == 1 and again.id == t.id


def test_planner_answers_with_references_only(tmp_path):
    gw = LLMGateway("stub", spend_path=tmp_path / "s.json")
    gw.register_stub("planner", lambda req: json.dumps({"intents": [
        {"action": "pay", "payee_ref": "bank:payee:tok_x", "amount_ref": "bank:usual:tok_x",
         "funding_ref": "bank:src:card_1234"}]}))
    facts = {"payees": [{"name": "X", "payee": {"value": "tok_x", "ref": "bank:payee:tok_x"},
                         "usual_amount": {"value": 100, "ref": "bank:usual:tok_x"}}],
             "funding_sources": [{"source": {"value": "card_1234", "ref": "bank:src:card_1234"}}]}
    [p], _ = Planner(gw).plan("pay X", facts, "t1")
    assert (p.payee.value, p.amount.value, p.funding_source.value) == ("tok_x", 100, "card_1234")
    gw.register_stub("planner", lambda req: json.dumps({"intents": [
        {"action": "pay", "payee_ref": "bank:payee:tok_evil", "amount_ref": "bank:usual:tok_x"}]}))
    [p], _ = Planner(gw).plan("pay X", facts, "t2")
    assert p.payee is None, "an invented reference resolves to nothing; the kernel's schema step will refuse"


def test_record_then_replay_reproduces_a_run_without_provider(tmp_path):
    calls = []

    def fake_provider(spec, req):
        calls.append(req["role"])
        return {"text": json.dumps({"kind": "budget", "confidence": 0.9}), "input_tokens": 120, "output_tokens": 9}

    rec = LLMGateway(Mode.RECORD, spend_path=tmp_path / "s.json", runs_dir=tmp_path / "runs")
    rec.providers["anthropic"] = fake_provider
    r1, _ = Dispatcher(rec).route("sort out my money please", "t1")
    assert calls == ["dispatcher"] and list((tmp_path / "runs").glob("*.json"))
    rep = LLMGateway(Mode.REPLAY, spend_path=tmp_path / "s2.json", runs_dir=tmp_path / "runs")
    rep.providers["anthropic"] = lambda spec, req: pytest.fail("replay must not call the provider")
    r2, _ = Dispatcher(rep).route("sort out my money please", "t1")
    assert r1 == r2 and rep.log[-1].source == "replay" and rep.log[-1].cost_rub == rec.log[-1].cost_rub
