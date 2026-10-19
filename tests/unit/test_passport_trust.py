import pytest

from kernel.crypto import KeyPair
from kernel.mandate import Action
from kernel.passport import AgentSession, AuthError, PassportRegistry, Role
from kernel.trust import (CLEAN_RUN, LEVEL1_LIMIT, PROMOTE_AFTER, AgentStatus, Category, TrustEvent, TrustLedger,
                          agent_key, category_for)


@pytest.fixture
def registry(kernel_key, clock):
    return PassportRegistry(kernel_key, clock)


def _planner(registry):
    return registry.issue(Role.PLANNER, "claude-sonnet-5-5", 2, "You plan money tasks.", "cl_1", "task_1")


def test_valid_request_is_authenticated(registry):
    s = _planner(registry)
    p = registry.authenticate(s.request("propose_intent", {"a": 1}))
    assert p.role == Role.PLANNER and len(p.instructions_hash) == 64


def test_replay_wrong_role_and_tampered_body_rejected(registry):
    s = _planner(registry)
    req = s.request("propose_intent", {"amount": 100})
    registry.authenticate(req)
    with pytest.raises(AuthError, match="replayed"):
        registry.authenticate(req)
    with pytest.raises(AuthError, match="may not call"):
        registry.authenticate(s.request("browser_command", {}))
    req = s.request("propose_intent", {"amount": 100})
    req.body["amount"] = 100000
    with pytest.raises(AuthError, match="session key"):
        registry.authenticate(req)


def test_forged_passport_and_foreign_session_key_rejected(registry, clock):
    s = _planner(registry)
    forged = s.passport.model_copy(update={"role": Role.BROWSER})
    with pytest.raises(AuthError, match="not signed"):
        registry.authenticate(s.request("propose_intent", {}), presented=forged)
    stolen = AgentSession(s.passport, KeyPair(), clock)
    with pytest.raises(AuthError, match="session key"):
        registry.authenticate(stolen.request("propose_intent", {}))


def test_expired_and_revoked_passports(registry, clock):
    s = _planner(registry)
    registry.revoke(s.passport.id)
    with pytest.raises(AuthError, match="revoked"):
        registry.authenticate(s.request("propose_intent", {}))
    s2 = _planner(registry)
    clock.advance(hours=2)
    with pytest.raises(AuthError, match="expired"):
        registry.authenticate(s2.request("propose_intent", {}))


def test_trust_demotes_fast_promotes_slow(clock):
    t = TrustLedger(clock)
    assert t.level(Category.SUBSCRIPTIONS) == 1
    assert t.auto_limit(Category.SUBSCRIPTIONS, 35000) == 35000
    assert t.auto_limit(Category.SUBSCRIPTIONS, 500000) == LEVEL1_LIMIT
    t.record(Category.SUBSCRIPTIONS, TrustEvent.CRITIC_DENY)
    assert t.level(Category.SUBSCRIPTIONS) == 0
    for _ in range(PROMOTE_AFTER - 1):
        t.record(Category.SUBSCRIPTIONS, TrustEvent.VERIFIED_SUCCESS)
    assert t.level(Category.SUBSCRIPTIONS) == 0
    t.record(Category.SUBSCRIPTIONS, TrustEvent.VERIFIED_SUCCESS)
    assert t.level(Category.SUBSCRIPTIONS) == 1
    t.categories[Category.BILLS].level = 2
    t.record(Category.BILLS, TrustEvent.SCOPE_VIOLATION)
    assert t.level(Category.BILLS) == 0
    assert len(t.history) == PROMOTE_AFTER + 2


def test_elevated_protection_pins_external_payments(clock):
    t = TrustLedger(clock, elevated_protection=True)
    assert t.level(Category.EXTERNAL_PAYMENTS) == 0
    assert t.level(Category.OWN_TRANSFERS) == 1
    assert category_for(Action.PAY, "subscription") == Category.SUBSCRIPTIONS
    assert category_for(Action.PAY, None) == Category.EXTERNAL_PAYMENTS


def test_agent_strikes_quarantine_probation_and_back(registry, clock):
    t = TrustLedger(clock)
    p = _planner(registry).passport
    other = registry.issue(Role.PLANNER, "claude-sonnet-5-5", 2, "Another prompt.", "cl_1", "task_2").passport
    assert agent_key(p) != agent_key(other)
    t.record(Category.SUBSCRIPTIONS, TrustEvent.SCOPE_VIOLATION, p)
    assert t.agent(p).strikes == 2 and t.agent(p).status == AgentStatus.ACTIVE
    t.record(Category.BILLS, TrustEvent.FABRICATED_NUMBER, p)
    rec = t.agent(p)
    assert rec.status == AgentStatus.QUARANTINED and rec.strikes == 3 and rec.penalties == 2
    assert t.level(Category.BILLS, p) == 0 and t.auto_limit(Category.OWN_TRANSFERS, 50_000, p) == 0
    assert t.level(Category.OWN_TRANSFERS, other) == 1
    assert [h.event for h in t.history][-1] == "agent.quarantined"
    clock.advance(hours=25)
    t.refresh()
    assert t.agent(p).status == AgentStatus.PROBATION and t.agent(p).strikes == 0
    t.categories[Category.OWN_TRANSFERS].level = 2
    assert t.level(Category.OWN_TRANSFERS, p) == 1
    for _ in range(CLEAN_RUN):
        t.record(Category.OWN_TRANSFERS, TrustEvent.VERIFIED_SUCCESS, p)
    assert t.agent(p).status == AgentStatus.ACTIVE and t.level(Category.OWN_TRANSFERS, p) == 2


def test_mistake_on_probation_quarantines_again_and_clean_runs_clear_strikes(registry, clock):
    t = TrustLedger(clock)
    p = _planner(registry).passport
    t.record(Category.BILLS, TrustEvent.CRITIC_DENY, p)
    for _ in range(CLEAN_RUN):
        t.record(Category.BILLS, TrustEvent.VERIFIED_SUCCESS, p)
    assert t.agent(p).strikes == 0
    t.agent(p).status = AgentStatus.PROBATION
    t.record(Category.BILLS, TrustEvent.KERNEL_DENY, p, "amount differs")
    assert t.agent(p).status == AgentStatus.QUARANTINED
    assert t.history[-2].reason == "amount differs" and t.history[-1].reason == "a mistake on probation"


def test_quarantine_revokes_only_that_agents_passports(registry, clock):
    a, b = _planner(registry), _planner(registry)
    other = registry.issue(Role.DISPATCHER, "kernel-code", 0, "Route.", "cl_1", "task_3")
    assert set(registry.revoke_agent(agent_key(a.passport), agent_key)) == {a.passport.id, b.passport.id}
    with pytest.raises(AuthError, match="revoked"):
        registry.authenticate(b.request("propose_intent", {}))
    registry.authenticate(other.request("route", {}))
