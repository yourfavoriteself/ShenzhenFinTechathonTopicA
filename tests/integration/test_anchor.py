"""Journal anchoring on a local chain: the contract stores the checkpoint; a changed copy no longer matches."""

import pytest

from chain import anchor as A

pytestmark = pytest.mark.skipif(not A.available(), reason="Foundry (forge, cast, anvil) is not installed")


@pytest.fixture(scope="module")
def chain_env():
    d = A.Devnet()
    a = A.Anchorer(d.rpc, d.key)
    a.deploy()
    yield a
    d.close()


def test_anchor_read_back_and_verify(chain_env, svc, client):
    client.yes(client.say("Pay for Melodify"))
    e = svc.k.journal.entries
    chain = A.run_id_chain(svc.k.journal.public_key, "test-1")
    first = A.checkpoint(e, 5)
    chain_env.anchor(chain, first)
    chain_env.anchor(chain, A.checkpoint(e))
    back = chain_env.read(chain)
    assert back["seq"] == e[-1].seq and A.verify(e, back)[0]
    assert chain_env.read(chain, 0)["merkle_root"] == "0x" + first["merkle_root"]


def test_changed_or_cut_copy_fails(chain_env, svc, client):
    client.yes(client.say("Pay for Melodify"))
    e = svc.k.journal.entries
    chain = A.run_id_chain(svc.k.journal.public_key, "test-2")
    chain_env.anchor(chain, A.checkpoint(e))
    back = chain_env.read(chain)
    changed = [x.model_copy(deep=True) for x in e]
    changed[2].hash = "f" * 64
    assert not A.verify(changed, back)[0]
    assert not A.verify(e[:-1], back)[0]


def test_sequence_cannot_go_back(chain_env, svc):
    e = svc.k.journal.entries
    chain = A.run_id_chain(svc.k.journal.public_key, "test-3")
    chain_env.anchor(chain, A.checkpoint(e))
    with pytest.raises(A.AnchorError):
        chain_env.anchor(chain, A.checkpoint(e))
