from guardian.audit import AuditLog, verify_chain
from guardian.clock import SimClock
from guardian.crypto import KeyPair, canonical, sha256_hex


def make_log(n=5):
    log = AuditLog(KeyPair.from_seed("test"), SimClock())
    for i in range(n):
        log.append("guardian", "test.event", {"i": i, "amount": 100 * i})
    return log


def test_clean_chain_verifies():
    log = make_log()
    r = log.verify()
    assert r.ok and r.checked == 5 and r.head_hash == log.entries[-1].hash


def test_edit_is_detected():
    log = make_log()
    log.entries[2].data["amount"] = 999999
    r = log.verify()
    assert not r.ok and r.first_bad_seq == 2 and r.reason == "entry content modified"


def test_edit_with_recomputed_hash_is_detected_by_signature():
    log = make_log()
    e = log.entries[4]
    e.data["amount"] = 1
    e.hash = sha256_hex(canonical(e.body()))
    r = log.verify()
    assert not r.ok and r.reason == "invalid signature"


def test_delete_is_detected():
    log = make_log()
    del log.entries[1]
    assert not log.verify().ok


def test_reorder_is_detected():
    log = make_log()
    log.entries[1], log.entries[2] = log.entries[2], log.entries[1]
    assert not log.verify().ok


def test_forged_entry_with_other_key_is_detected():
    log = make_log()
    forger = AuditLog(KeyPair.from_seed("attacker"), SimClock())
    forger.entries = [e.model_copy() for e in log.entries]
    forger.append("guardian", "payment.executed", {"amount": 10**6})
    r = log.verify(forger.entries)
    assert not r.ok and r.first_bad_seq == 5 and r.reason == "invalid signature"


def test_export_jsonl(tmp_path):
    log = make_log(3)
    p = tmp_path / "audit.jsonl"
    log.export_jsonl(p)
    assert len(p.read_text().splitlines()) == 3


def test_truncation_detected_with_published_checkpoint():
    log = make_log(6)
    cp = log.checkpoint()
    truncated = log.entries[:4]
    assert log.verify(truncated).ok                     # a bare hash chain cannot see a cut tail
    r = verify_chain(truncated, log.public_key, checkpoint=cp)
    assert not r.ok and "truncation" in r.reason
    assert verify_chain(log.entries, log.public_key, checkpoint=cp).ok
