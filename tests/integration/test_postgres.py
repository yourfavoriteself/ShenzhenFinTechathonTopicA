"""PostgreSQL: migrations, journal written through and append-only, row-level security per client.

Runs when GUARDIAN_TEST_PG points at an empty database the test may reset, e.g.
    GUARDIAN_TEST_PG=postgresql://postgres@/guardian_test?host=/tmp&port=55432
"""

import os
import psycopg
import pytest

from app.service import GuardianService
from sandbox.sites.portal import DEMO_LOGINS
from tests.e2e.test_c2_cancel import cancel_streambox

URL = os.environ.get("GUARDIAN_TEST_PG")
pytestmark = pytest.mark.skipif(not URL, reason="set GUARDIAN_TEST_PG to run the PostgreSQL tests")


@pytest.fixture
def svc(fresh_world, gateway):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    svc = GuardianService(fresh_world(), gateway)
    applied = svc.connect_db(URL)
    assert applied == ["001_schema.sql", "002_row_level_security.sql", "003_journal_append_only.sql"]
    yield svc
    svc.db.close()


def test_migrations_are_idempotent(svc):
    assert svc.db.migrate() == []


def test_journal_is_written_through_and_verifies_from_the_database(svc, client):
    client.yes(client.say("Pay for Melodify"))
    rows = svc.db.journal()
    assert len(rows) == len(svc.k.journal.entries) and rows[-1].hash == svc.k.journal.entries[-1].hash
    assert svc.db.verify_journal(svc.k.journal.public_key).ok


@pytest.mark.parametrize("sql", ["UPDATE journal SET actor = 'x' WHERE seq = 1", "DELETE FROM journal WHERE seq = 1",
                                 "TRUNCATE journal"])
def test_journal_cannot_be_changed_even_by_the_owner(svc, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        svc.db.conn.execute(sql)


def test_journal_rejects_a_row_that_breaks_the_chain(svc):
    last = svc.k.journal.entries[-1]
    with pytest.raises(psycopg.errors.RaiseException, match="chain broken"):
        svc.db.conn.execute(
            "INSERT INTO journal (chain, seq, ts, ts_iso, actor, event, data, prev_hash, hash, sig) "
            "VALUES (%s, %s, now(), 'x', 'attacker', 'forged', '{}', %s, %s, 'x')",
            (svc.db.chain, last.seq + 1, "f" * 64, "e" * 64))
    with pytest.raises(psycopg.errors.RaiseException, match="must start at seq 0"):
        svc.db.conn.execute(
            "INSERT INTO journal (chain, seq, ts, ts_iso, actor, event, data, prev_hash, hash, sig) "
            "VALUES ('chain_forged', 5, now(), 'x', 'attacker', 'forged', '{}', %s, %s, 'x')", ("0" * 64, "e" * 64))


def test_a_restarted_kernel_writes_a_new_chain_and_the_old_one_still_verifies(svc, fresh_world, gateway):
    first = svc.db.chain
    again = GuardianService(fresh_world(), gateway)
    again.connect_db(URL)
    try:
        again.chat("Pay for Melodify")
        assert again.db.chains() == [first, again.db.chain]
        assert again.db.verify_journal(svc.k.journal.public_key, first).ok
        assert again.db.verify_journal(again.k.journal.public_key).ok
    finally:
        again.db.close()


def test_signed_mandates_and_vault_ciphertexts_are_stored_under_rls(svc, client):
    client.yes(client.say("Pay for Melodify"), standing=True)
    _, _, _, cid = cancel_streambox(svc, client.dev, remember=True)
    login, password = DEMO_LOGINS[("tok_streambox", "card_1234")]
    db = svc.db
    with db.as_client(svc.client) as c:
        mandates = c.execute("SELECT id, standing FROM mandates").fetchall()
        vault = c.execute("SELECT name, ciphertext FROM vault_entries").fetchall()
    assert len(mandates) >= 2 and any(s for _, s in mandates)
    assert {n for n, _ in vault} >= {"streambox.card_1234.login", "streambox.card_1234.password"}
    dump = " ".join(ct for _, ct in vault)
    assert password not in dump and login not in dump
    with db.as_client("cl_other") as c:
        assert c.execute("SELECT count(*) FROM mandates").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM vault_entries").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO vault_entries VALUES (%s, 'x', 'd', 'n', 'c', false, now())", (svc.client,))
    with db.as_client(None) as c:
        assert c.execute("SELECT count(*) FROM mandates").fetchone()[0] == 0


def test_held_transfer_and_analyst_case_are_persisted(svc, client, dev):
    svc.scam_call(True)
    items = client.say("Transfer 350 000 ₽ from my savings to safe account 40817810500001234567")
    svc.wait(15)
    card = [i for i in items if i.type == "card"][-1]
    svc.confirm(card.data["pending_id"], dev.alg, dev.public_b64, dev.sign_mandate(card.data["card"]["mandate_hash"]),
                   typed_amount=35_000_000, answers={"on_call": True, "pressure": True, "given_account": True},
                   pin="2580")
    with svc.db.as_client(svc.client) as c:
        holds = c.execute("SELECT level, status FROM holds").fetchall()
        cases = c.execute("SELECT body->>'title' FROM cases").fetchall()
    assert holds == [(4, "held")] and any("scam call" in (t or "") for (t,) in cases)
