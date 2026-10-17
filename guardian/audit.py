"""Tamper-evident flight recorder.

Every entry carries the hash of the previous entry (hash chain) and an Ed25519 signature
over its own hash. Editing, deleting, inserting or reordering entries breaks verification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .clock import SimClock
from .crypto import KeyPair, canonical, sha256_hex, verify

GENESIS = "0" * 64


class AuditEntry(BaseModel):
    seq: int
    ts: str
    actor: str
    event: str
    data: dict[str, Any]
    prev_hash: str
    hash: str = ""
    sig: str = ""

    def body(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "actor": self.actor, "event": self.event,
                "data": self.data, "prev_hash": self.prev_hash}


class VerificationResult(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None
    head_hash: str | None = None


class AuditLog:
    def __init__(self, signer: KeyPair, clock: SimClock):
        self._signer = signer
        self._clock = clock
        self.entries: list[AuditEntry] = []

    @property
    def public_key(self) -> str:
        return self._signer.public_b64

    def append(self, actor: str, event: str, data: dict[str, Any] | None = None) -> AuditEntry:
        prev = self.entries[-1].hash if self.entries else GENESIS
        entry = AuditEntry(seq=len(self.entries), ts=self._clock.now().isoformat(), actor=actor,
                           event=event, data=json.loads(canonical(data or {})), prev_hash=prev)
        entry.hash = sha256_hex(canonical(entry.body()))
        entry.sig = self._signer.sign(entry.hash.encode())
        self.entries.append(entry)
        return entry

    def verify(self, entries: list[AuditEntry] | None = None) -> VerificationResult:
        return verify_chain(entries if entries is not None else self.entries, self.public_key)

    def checkpoint(self) -> dict[str, Any]:
        """Signed head of the chain. Publishing it outside the device (bank, auditor, transparency
        log) makes truncation of the newest entries detectable, which a hash chain alone cannot do."""
        head = self.entries[-1]
        body = {"seq": head.seq, "head_hash": head.hash}
        return {**body, "sig": self._signer.sign(canonical(body))}

    def tail(self, n: int = 50) -> list[AuditEntry]:
        return self.entries[-n:]

    def export_jsonl(self, path: Path) -> None:
        path.write_text("".join(e.model_dump_json() + "\n" for e in self.entries))


def verify_chain(entries: list[AuditEntry], public_key: str,
                 checkpoint: dict[str, Any] | None = None) -> VerificationResult:
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.seq != i:
            return VerificationResult(ok=False, checked=i, first_bad_seq=e.seq, reason="sequence gap or reorder")
        if e.prev_hash != prev:
            return VerificationResult(ok=False, checked=i, first_bad_seq=e.seq, reason="broken hash chain")
        if sha256_hex(canonical(e.body())) != e.hash:
            return VerificationResult(ok=False, checked=i, first_bad_seq=e.seq, reason="entry content modified")
        if not verify(public_key, e.hash.encode(), e.sig):
            return VerificationResult(ok=False, checked=i, first_bad_seq=e.seq, reason="invalid signature")
        prev = e.hash
    if checkpoint is not None:
        body = {"seq": checkpoint["seq"], "head_hash": checkpoint["head_hash"]}
        if not verify(public_key, canonical(body), checkpoint["sig"]):
            return VerificationResult(ok=False, checked=len(entries), reason="invalid checkpoint signature")
        n = checkpoint["seq"]
        if n >= len(entries) or entries[n].hash != checkpoint["head_hash"]:
            return VerificationResult(ok=False, checked=len(entries), first_bad_seq=n,
                                      reason="entries missing after a published checkpoint (truncation)")
    return VerificationResult(ok=True, checked=len(entries), head_hash=prev)
