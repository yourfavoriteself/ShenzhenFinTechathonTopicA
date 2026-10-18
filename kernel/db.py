"""PostgreSQL persistence: migrations, the journal mirror and client data under row-level security.

The kernel keeps working in memory; every journal entry is written through to Postgres as it is
appended, and the client data behind security-relevant events (signed mandates, vault ciphertexts,
held transfers, cases) is upserted at the same moment. Writes run as the `guardian_app` role with
the client set for the transaction, so row-level security applies to the application itself.
The journal table only accepts the next link of the hash chain; nothing can change or delete it.
Each kernel run writes its own chain, so a restart never collides with the chains already stored.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import psycopg
from psycopg.types.json import Jsonb

from .journal import JournalEntry, VerificationResult, verify_chain
from .mandate import new_id

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


class Database:
    def __init__(self, url: str):
        self.url = url
        self.conn = psycopg.connect(url, autocommit=True)
        self.errors: list[str] = []

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ schema

    def migrate(self, folder: Path = MIGRATIONS) -> list[str]:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                          "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in self.conn.execute("SELECT name FROM schema_migrations")}
        applied = []
        for f in sorted(folder.glob("*.sql")):
            if f.name in done:
                continue
            with self.conn.transaction():
                self.conn.execute(f.read_text())
                self.conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
        return applied

    @contextmanager
    def as_client(self, client_id: str | None) -> Iterator[psycopg.Connection]:
        """A transaction as the application role, seeing only `client_id`'s rows."""
        with self.conn.transaction():
            self.conn.execute("SET LOCAL ROLE guardian_app")
            self.conn.execute("SELECT set_config('guardian.client_id', %s, true)", (client_id or "",))
            yield self.conn

    # ------------------------------------------------------------------ kernel hooks

    def attach(self, kernel: Any, client_id: str, holds: Callable[[], dict] | None = None,
               cases: Callable[[], list] | None = None) -> None:
        self.k, self.client_id, self.chain = kernel, client_id, new_id("chain")
        self._holds, self._cases = holds, cases
        for e in kernel.journal.entries:           # entries written before the database was attached
            self._write(e)
        kernel.journal.listeners.append(self._write)

    def _write(self, e: JournalEntry) -> None:
        with self.as_client(self.client_id) as c:
            c.execute("INSERT INTO journal (chain, seq, ts, ts_iso, actor, event, data, stage, passport_id, prev_hash, "
                      "hash, sig) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      (self.chain, e.seq, e.ts, e.ts, e.actor, e.event, Jsonb(e.data), e.stage, e.passport_id, e.prev_hash, e.hash,
                       e.sig))
            if e.event in ("mandate.signed", "mandate.revoked"):
                self._mandates(c)
            if e.event.startswith(("credentials.", "secret.", "cancellation.", "merchant.")):
                self._vault(c)
            if e.event.startswith("intervention."):
                self._save_holds(c)
            if e.event == "case.opened" or e.event == "intervention.held":
                self._save_cases(c)

    def _mandates(self, c: psycopg.Connection) -> None:
        for sm in self.k.mandates.all(self.client_id):
            st = self.k.mandates.state(sm.mandate.id)
            c.execute("INSERT INTO mandates (id, client_id, mandate_hash, body, device_alg, device_sig, standing, "
                      "expires_at, revoked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                      "ON CONFLICT (id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at",
                      (sm.mandate.id, sm.mandate.client_id, sm.mandate_hash, Jsonb(sm.mandate.model_dump(mode="json")),
                       sm.device_alg or "", sm.device_sig or "", sm.mandate.standing, sm.mandate.exp,
                       self.k.clock.now() if st and st.revoked else None))

    def _vault(self, c: psycopg.Connection) -> None:
        rows = self.k.vault.ciphertexts(self.client_id)
        c.execute("DELETE FROM vault_entries WHERE NOT (name = ANY(%s))", ([r.name for r in rows],))
        for r in rows:
            c.execute("INSERT INTO vault_entries (client_id, name, domain, nonce, ciphertext, remember, created_at) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (client_id, name) DO UPDATE SET "
                      "domain = EXCLUDED.domain, nonce = EXCLUDED.nonce, ciphertext = EXCLUDED.ciphertext, "
                      "remember = EXCLUDED.remember", (r.client_id, r.name, r.domain, r.nonce, r.ciphertext,
                                                       r.remember, r.created))

    def _save_holds(self, c: psycopg.Connection) -> None:
        for h in (self._holds() if self._holds else {}).values():
            c.execute("INSERT INTO holds (id, client_id, level, status, release_at, duress, body) VALUES "
                      "(%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, "
                      "release_at = EXCLUDED.release_at, level = EXCLUDED.level",
                      (h.id, h.client_id, h.level, h.status, h.release_at, h.duress,
                       Jsonb(h.model_dump(mode="json", exclude={"intent"}) | {"intent": h.intent.summary()})))

    def _save_cases(self, c: psycopg.Connection) -> None:
        for case in (self._cases() if self._cases else []):
            c.execute("INSERT INTO cases (id, client_id, opened_at, body) VALUES (%s, %s, %s, %s) "
                      "ON CONFLICT (id) DO NOTHING", (case["id"], self.client_id, case["opened"], Jsonb(case)))

    # ------------------------------------------------------------------ reading back

    def chains(self) -> list[str]:
        with self.as_client(self.client_id) as c:
            return [r[0] for r in c.execute("SELECT chain FROM journal GROUP BY chain ORDER BY min(ts)")]

    def journal(self, chain: str | None = None) -> list[JournalEntry]:
        with self.as_client(self.client_id) as c:
            rows = c.execute("SELECT seq, ts_iso, actor, event, data, stage, passport_id, prev_hash, hash, sig "
                             "FROM journal WHERE chain = %s ORDER BY seq", (chain or self.chain,)).fetchall()
        return [JournalEntry(seq=r[0], ts=r[1], actor=r[2], event=r[3], data=r[4], stage=r[5], passport_id=r[6],
                             prev_hash=r[7], hash=r[8], sig=r[9]) for r in rows]

    def verify_journal(self, public_key: str, chain: str | None = None) -> VerificationResult:
        return verify_chain(self.journal(chain), public_key)
