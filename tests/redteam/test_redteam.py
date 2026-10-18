"""Every static and retention attack is stopped; the adaptive search finds nothing outside a mandate."""

import pytest

from redteam import adaptive
from redteam.harness import BLOCKED, Env
from redteam.static import RETENTION, STATIC


@pytest.mark.parametrize("attack", STATIC + RETENTION, ids=lambda a: a.id)
def test_attack_is_stopped(attack):
    r = attack.run(Env())
    assert r.outcome in BLOCKED, f"{attack.id} {attack.title}: {r.outcome} ({r.detail})"
    assert r.lost == 0 and not r.out_of_mandate


def test_adaptive_search_finds_nothing_outside_a_mandate():
    r = adaptive.run(rounds=4, width=5)
    assert r["with_kernel"]["out_of_mandate_successes"] == 0
    assert all(g["attempts"] == 20 for g in r["goals"])


def test_harness_detects_money_that_left():
    """Sanity check of the harness itself: a card the client signs without reading does move money."""
    from kernel.intent import Claim
    from kernel.mandate import Action
    from redteam.harness import ATTACKER, judge
    env = Env()
    t = env.task("Transfer 900 to account 40817810500000006666")
    ref = f"client:{t.task_id}"
    d = env.propose(env.agent(t), action=Action.PAY, payee=Claim(value=ATTACKER, ref=ref),
                    amount=Claim(value=90_000, ref=ref), funding_source=env.src())
    r = judge(env.sign(d, typed_amount=90_000) if d.card.type_amount else env.sign(d), payee=ATTACKER, env=env)
    assert r.outcome == "succeeded" and r.lost == 90_000
