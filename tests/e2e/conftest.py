import pytest

from app.service import GuardianService
from data.generator import World, build_world
from kernel.crypto import DeviceKey
from kernel.llm_gateway import LLMGateway


@pytest.fixture(scope="session")
def base_world():
    return build_world()


@pytest.fixture
def fresh_world(base_world):
    def make() -> World:
        bank = base_world.bank.twin()
        return base_world.model_copy(update={"bank": bank, "clock": bank.clock,
                                            "documents": list(base_world.documents)})
    return make


@pytest.fixture
def gateway(tmp_path):
    return LLMGateway("stub", spend_path=tmp_path / "spend.json", runs_dir=tmp_path / "runs")


@pytest.fixture
def svc(fresh_world, gateway):
    return GuardianService(fresh_world(), gateway)


@pytest.fixture
def dev(svc):
    d = DeviceKey(seed="e2e:device")
    svc.register_device(d.alg, d.public_b64)
    return d


class Client:
    """The client side of the chat: reads items, signs cards with the device key, presses buttons."""

    def __init__(self, svc, dev):
        self.svc, self.dev = svc, dev

    def say(self, text):
        return self.svc.chat(text)

    def yes(self, items, standing=False, typed_amount=None, index=-1):
        card = [i for i in items if i.type == "card"][index]
        c = card.data["card"]
        h = c["standing_mandate_hash"] if standing else c["mandate_hash"]
        return self.svc.confirm(card.data["pending_id"], self.dev.alg, self.dev.public_b64, self.dev.sign_mandate(h),
                                standing=standing, typed_amount=typed_amount)

    def option(self, items, kind):
        for it in items:
            for o in it.data.get("options", []):
                if o.get("kind") == kind or kind in o["title"]:
                    return self.svc.choose(o["id"])
        raise AssertionError(f"no option {kind}")


def of(items, type_):
    return [i for i in items if i.type == type_]


@pytest.fixture
def client(svc, dev):
    return Client(svc, dev)
