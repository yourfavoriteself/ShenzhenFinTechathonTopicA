from datetime import timedelta

import pytest

from kernel.clock import SimClock
from kernel.crypto import DeviceKey, KeyPair
from kernel.mandate import (Action, AmountRange, AutonomyStage, Budget, Frequency, Mandate, MandateService,
                            OriginMode, OriginRule, PaymentConstraints, Recurrence)


@pytest.fixture
def clock():
    return SimClock()


@pytest.fixture
def kernel_key():
    return KeyPair.from_seed("test:kernel")


@pytest.fixture
def device():
    return DeviceKey(seed="test:device")


@pytest.fixture
def mandates(kernel_key, clock, device):
    svc = MandateService(kernel_key, clock)
    svc.register_device("cl_1", device.public_b64, device.alg)
    return svc


@pytest.fixture
def make_mandate(clock):
    def _make(**kw):
        now = clock.now()
        base = dict(client_id="cl_1", actions=[Action.PAY],
                    payment=PaymentConstraints(allowed_payees=["tok_melodify"], amount_range=AmountRange(max=35000),
                                               budget=Budget(total=105000),
                                               agent_recurrence=Recurrence(frequency=Frequency.MONTHLY,
                                                                           max_occurrences=None)),
                    allowed_origins=[OriginRule(domain="melodify.example", mode=OriginMode.ACT)],
                    funding_source="card_1234", nbf=now, exp=now + timedelta(days=90), standing=True,
                    stage=AutonomyStage.PRESET_CONDITIONS)
        base.update(kw)
        return Mandate(**base)
    return _make


@pytest.fixture
def issue(mandates, device):
    def _issue(m):
        return mandates.issue(m, device.alg, device.public_b64, device.sign_mandate(m.hash()))
    return _issue
