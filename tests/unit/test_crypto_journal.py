from kernel.crypto import ED25519, ES256, DeviceKey, KeyPair, mandate_signing_payload, verify_device
from kernel.journal import Journal, merkle_root


def test_device_signatures_both_algorithms():
    for alg in (ED25519, ES256):
        d = DeviceKey(alg)
        sig = d.sign_mandate("abc")
        assert verify_device(alg, d.public_b64, mandate_signing_payload("abc"), sig)
        assert not verify_device(alg, d.public_b64, mandate_signing_payload("abd"), sig)
    assert not verify_device("RS256", DeviceKey().public_b64, b"x", "AA==")


def _journal(clock, n=5):
    j = Journal(KeyPair.from_seed("j"), clock)
    for i in range(n):
        j.append("kernel", "step", {"i": i}, stage=1, passport_id=f"ps_{i}")
    return j


def test_journal_detects_edit_delete_reorder(clock):
    j = _journal(clock)
    assert j.verify().ok
    edited = [e.model_copy(deep=True) for e in j.entries]
    edited[2].data["i"] = 99
    assert j.verify(edited).reason == "entry content modified"
    edited = [e.model_copy(deep=True) for e in j.entries]
    edited[1].stage = 2
    assert not j.verify(edited).ok
    assert not j.verify(j.entries[:2] + j.entries[3:]).ok
    assert not j.verify([j.entries[1], j.entries[0]] + j.entries[2:]).ok


def test_checkpoint_detects_truncation(clock):
    j = _journal(clock)
    cp = j.checkpoint()
    assert j.verify(checkpoint=cp).ok
    assert "truncation" in j.verify(j.entries[:3], checkpoint=cp).reason


def test_merkle_root_changes_with_any_leaf():
    leaves = ["00" * 32, "11" * 32, "22" * 32]
    assert merkle_root(leaves) == merkle_root(list(leaves))
    assert merkle_root(leaves) != merkle_root(leaves[:2] + ["33" * 32])
