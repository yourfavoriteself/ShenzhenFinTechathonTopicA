import json

import pytest

from data.generator import CLIENT, build_world
from kernel.critic import MAX_REFUSALS, Critic, rule_critic
from kernel.intent import Claim, IntentProposal
from kernel.mandate import Action
from kernel.money import from_rub
from kernel.passport import Role
from kernel.pipeline import Outcome, Verdict
from sandbox.bank.wiring import connect


def judge(request, action="pay", payee="Melodify", kind="subscription", amount=None, usual=None):
    act = {"type": action, "payee": {"name": payee, "label": "bank", "kind": kind}}
    if amount:
        act["amount"] = {"value": amount, "label": "bank"}
        if usual:
            act["amount"]["usual_for_this_payee"] = usual
    return json.loads(rule_critic({"client_request": request, "mandate": None, "proposed_action": act}))["verdict"]


def test_rule_critic_matches_request_to_action():
    assert judge("Pay for Melodify") == "consistent"
    assert judge("Pay for Melodify", payee="TuneWave") == "inconsistent"
    assert judge("Cancel StreamBox", action="pay", payee="StreamBox") == "inconsistent"
    assert judge("Cancel StreamBox", action="block_merchant", payee="StreamBox") == "consistent"
    assert judge("Pay for Melodify", amount="$36.09", usual="$3.61") == "inconsistent"
    assert judge("Pay for Melodify for a year", amount="$36.09", usual="$3.61") == "consistent"
    assert judge("hmm") == "unsure"


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture
def k(world):
    k = connect(world.bank.twin(), world.merchants, seed="t")
    k.pipeline.critic = Critic(k)
    return k


def pay(k, task, payee, amount):
    s = k.passports.issue(Role.PLANNER, "m", 2, "p", CLIENT, task.task_id)
    return k.submit(s.request("propose_intent", {"intent": IntentProposal(
        action=Action.PAY, payee=Claim(value=payee, ref=k.bank_value(f"bank:payee:{payee}", payee, "dir")),
        amount=Claim(value=amount, ref=k.bank_value(f"bank:amt:{payee}:{amount}", amount, "hist")),
        funding_source=Claim(value="card_1234", ref=k.bank_value("bank:src:card_1234", "card_1234", "cards")),
    ).model_dump(mode="json")}))


def test_critic_denies_wrong_payee_and_hands_control_back_after_three(k):
    task = k.open_task(CLIENT, "Pay for Melodify")
    assert pay(k, task, "tok_melodify", from_rub(299)).outcome == Outcome.PENDING
    for _ in range(MAX_REFUSALS):
        d = pay(k, task, "tok_tunewave", from_rub(249))
        assert d.outcome == Outcome.DENIED and any(r.startswith("critic:") for r in d.reasons)
    d = pay(k, task, "tok_melodify", from_rub(299))
    assert d.outcome == Outcome.DENIED and "control returned to the client" in d.reasons[-1]


def test_critic_never_sees_documents_and_is_cached(k):
    task = k.open_task(CLIENT, "Pay for Melodify")
    seen = []
    k.pipeline.critic.judge = (lambda orig: lambda payload, tid="c": (seen.append(payload), orig(payload, tid))[1])(
        k.pipeline.critic.judge)
    pay(k, task, "tok_melodify", from_rub(299))
    assert set(seen[0]) == {"client_request", "mandate", "proposed_action"}
    assert "memo" not in json.dumps(seen[0])
    c = Critic(k)
    p = {"client_request": "Pay for Melodify", "mandate": None,
         "proposed_action": {"type": "pay", "payee": {"name": "Melodify", "label": "bank", "kind": "subscription"}}}
    c.judge(p), c.judge(p)
    assert c.calls == 1 and c.cache_hits == 1


def test_no_task_on_record_means_ask(k):
    s = k.passports.issue(Role.PLANNER, "m", 2, "p", CLIENT, "task_unknown")
    d = k.submit(s.request("propose_intent", {"intent": IntentProposal(
        action=Action.BLOCK_MERCHANT,
        payee=Claim(value="tok_pixelplay", ref=k.bank_value("bank:payee:tok_pixelplay", "tok_pixelplay", "dir")),
    ).model_dump(mode="json")}))
    critic = next(s for s in d.steps if s.step == "critic")
    assert critic.verdict == Verdict.ASK and "no client request" in critic.reasons[0]


def test_suspicious_memory_links_by_account_and_domain(k):
    from datetime import datetime
    from kernel.clock import MSK
    m = k.memory
    m.record(ts=datetime(2026, 10, 3, tzinfo=MSK), client_id=CLIENT, kind="details_change_request", summary="x",
             account="40817810500001234987", domain="evil.example", source="doc_a")
    assert m.related(CLIENT, account="40817810500001234987")
    assert m.related(CLIENT, domain="evil.example")
    assert not m.related(CLIENT, account="40817810500001234987", exclude_source="doc_a")
    assert not m.related("cl_2", account="40817810500001234987")
