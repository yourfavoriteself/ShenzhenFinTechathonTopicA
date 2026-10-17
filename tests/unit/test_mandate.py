from datetime import timedelta

import pytest

from kernel.crypto import DeviceKey
from kernel.mandate import (Action, AmountRange, Frequency, MandateError, OriginMode, OriginRule,
                            PaymentConstraints, Recurrence, narrowing_violations)


def test_issue_requires_registered_device_and_valid_signature(mandates, make_mandate, issue):
    m = make_mandate()
    assert issue(m).mandate_hash == m.hash()
    stranger = DeviceKey()
    with pytest.raises(MandateError, match="not registered"):
        mandates.issue(make_mandate(), stranger.alg, stranger.public_b64, stranger.sign_mandate("x"))
    m2 = make_mandate()
    good_sig = DeviceKey(seed="test:device").sign_mandate(m2.hash())
    m2.payment.amount_range.max = 10_000_000
    with pytest.raises(MandateError, match="does not match"):
        mandates.issue(m2, "Ed25519", DeviceKey(seed="test:device").public_b64, good_sig)


def test_edit_after_signing_is_detected(mandates, make_mandate, issue):
    signed = issue(make_mandate())
    assert mandates.verify_signed(signed) == []
    forged = signed.model_copy(deep=True)
    forged.mandate.payment.allowed_payees.append("tok_attacker")
    assert "mandate content does not match its signed hash" in mandates.verify_signed(forged)


def test_check_amount_payee_funding_domain(mandates, make_mandate, issue):
    mid = issue(make_mandate()).mandate.id
    ok = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    assert mandates.check(mid, Action.PAY, **ok) == []
    assert "amount outside allowed range" in mandates.check(mid, Action.PAY, **{**ok, "amount": 35001})
    assert "payee not allowed" in mandates.check(mid, Action.PAY, **{**ok, "payee": "tok_attacker"})
    assert "funding source not allowed" in mandates.check(mid, Action.PAY, **{**ok, "funding_source": "card_9"})
    assert any("not in the task's origin set" in v
               for v in mandates.check(mid, Action.PAY, **ok, domain="melodify-pay.example"))
    assert "action dispute not allowed" in mandates.check(mid, Action.DISPUTE, **ok)


def test_monthly_recurrence_and_budget(mandates, make_mandate, issue, clock):
    mid = issue(make_mandate()).mandate.id
    args = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    g = mandates.grant(mid, "ps_exec", Action.PAY, **args)
    mandates.consume(g, "ps_exec", Action.PAY, **args)
    assert "already used this monthly period" in mandates.check(mid, Action.PAY, **args)
    for _ in range(2):
        clock.advance(days=31)
        g = mandates.grant(mid, "ps_exec", Action.PAY, **args)
        mandates.consume(g, "ps_exec", Action.PAY, **args)
    clock.advance(days=31)
    assert "budget exhausted" in mandates.check(mid, Action.PAY, **args)


def test_grant_is_single_use_and_exact(mandates, make_mandate, issue):
    mid = issue(make_mandate()).mandate.id
    args = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    g = mandates.grant(mid, "ps_exec", Action.PAY, **args)
    with pytest.raises(MandateError, match="differs"):
        mandates.consume(g, "ps_exec", Action.PAY, **{**args, "amount": 30000})
    with pytest.raises(MandateError, match="another agent"):
        mandates.consume(g, "ps_other", Action.PAY, **args)
    tampered = g.model_copy(update={"amount": 30000})
    with pytest.raises(MandateError, match="not issued"):
        mandates.consume(tampered, "ps_exec", Action.PAY, **{**args, "amount": 30000})
    mandates.consume(g, "ps_exec", Action.PAY, **args)
    with pytest.raises(MandateError, match="already used"):
        mandates.consume(g, "ps_exec", Action.PAY, **args)


def test_grant_expires(mandates, make_mandate, issue, clock):
    mid = issue(make_mandate()).mandate.id
    args = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    g = mandates.grant(mid, "ps_exec", Action.PAY, **args)
    clock.advance(minutes=11)
    with pytest.raises(MandateError, match="expired"):
        mandates.consume(g, "ps_exec", Action.PAY, **args)


def test_rights_only_narrow(mandates, make_mandate, issue, clock):
    parent = issue(make_mandate())
    narrow = make_mandate(standing=False, exp=clock.now() + timedelta(days=1),
                          payment=PaymentConstraints(allowed_payees=["tok_melodify"],
                                                     amount_range=AmountRange(max=29900),
                                                     budget=parent.mandate.payment.budget,
                                                     agent_recurrence=Recurrence()),
                          single_use=True, holder="ps_planner")
    assert mandates.derive(parent.mandate.id, narrow).mandate.parent_id == parent.mandate.id
    wider = make_mandate(actions=[Action.PAY, Action.DISPUTE],
                         payment=PaymentConstraints(allowed_payees=["tok_melodify", "tok_attacker"],
                                                    amount_range=AmountRange(max=99900),
                                                    agent_recurrence=Recurrence(frequency=Frequency.DAILY,
                                                                                max_occurrences=None)),
                         allowed_origins=[OriginRule(domain="evil.example", mode=OriginMode.ACT)],
                         exp=clock.now() + timedelta(days=365))
    v = narrowing_violations(wider, parent.mandate)
    for frag in ("actions not in parent", "payees not in parent", "amount range wider", "budget larger",
                 "more frequent", "origin not allowed", "validity period"):
        assert any(frag in x for x in v), frag
    with pytest.raises(MandateError):
        mandates.derive(parent.mandate.id, wider)


def test_revocation_cascades(mandates, make_mandate, issue, clock):
    parent = issue(make_mandate())
    child = mandates.derive(parent.mandate.id, make_mandate(standing=False, single_use=True))
    assert set(mandates.revoke(parent.mandate.id)) == {parent.mandate.id, child.mandate.id}
    args = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    assert any("revoked" in v for v in mandates.check(child.mandate.id, Action.PAY, **args))
    assert mandates.active("cl_1") == []


def test_spend_through_child_counts_against_parent(mandates, make_mandate, issue):
    parent = issue(make_mandate())
    child = mandates.derive(parent.mandate.id, make_mandate(standing=False, single_use=True))
    args = dict(payee="tok_melodify", amount=29900, funding_source="card_1234")
    g = mandates.grant(child.mandate.id, "ps_exec", Action.PAY, **args)
    mandates.consume(g, "ps_exec", Action.PAY, **args)
    assert mandates.state(parent.mandate.id).spent == 29900
    assert "already used this monthly period" in mandates.check(parent.mandate.id, Action.PAY, **args)
