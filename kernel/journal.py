"""Tamper-evident journal of every kernel decision.

Each entry carries the hash of the previous one and an Ed25519 signature over its own hash,
plus the autonomy stage of the operation and the passport of the agent involved (KYA).
Merkle roots over entry hashes are anchored outside the bank (see chain/).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .clock import SimClock
from .crypto import KeyPair, canonical, sha256_hex, verify

GENESIS = "0" * 64


class JournalEntry(BaseModel):
    seq: int
    ts: str
    actor: str
    event: str
    data: dict[str, Any]
    stage: int | None = None
    passport_id: str | None = None
    prev_hash: str
    hash: str = ""
    sig: str = ""

    def body(self) -> dict[str, Any]:
        return self.model_dump(exclude={"hash", "sig"})


class VerificationResult(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None
    head_hash: str | None = None


class Journal:
    def __init__(self, signer: KeyPair, clock: SimClock):
        self._signer = signer
        self._clock = clock
        self.entries: list[JournalEntry] = []

    @property
    def public_key(self) -> str:
        return self._signer.public_b64

    def append(self, actor: str, event: str, data: dict[str, Any] | None = None, *,
               stage: int | None = None, passport_id: str | None = None) -> JournalEntry:
        prev = self.entries[-1].hash if self.entries else GENESIS
        entry = JournalEntry(seq=len(self.entries), ts=self._clock.now().isoformat(), actor=actor, event=event,
                             data=json.loads(canonical(data or {})), stage=stage, passport_id=passport_id,
                             prev_hash=prev)
        entry.hash = sha256_hex(canonical(entry.body()))
        entry.sig = self._signer.sign(entry.hash.encode())
        self.entries.append(entry)
        return entry

    def verify(self, entries: list[JournalEntry] | None = None,
               checkpoint: dict[str, Any] | None = None) -> VerificationResult:
        return verify_chain(self.entries if entries is None else entries, self.public_key, checkpoint)

    def checkpoint(self) -> dict[str, Any]:
        """Signed head and Merkle root; publishing it makes truncation of the newest entries detectable."""
        body = {"seq": self.entries[-1].seq, "head_hash": self.entries[-1].hash,
                "merkle_root": merkle_root([e.hash for e in self.entries])}
        return {**body, "sig": self._signer.sign(canonical(body))}

    def find(self, event: str) -> list[JournalEntry]:
        return [e for e in self.entries if e.event == event]

    def export_jsonl(self, path: Path) -> None:
        path.write_text("".join(e.model_dump_json() + "\n" for e in self.entries))


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return GENESIS
    level = [sha256_hex(bytes.fromhex(h)) for h in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(bytes.fromhex(a) + bytes.fromhex(b)) for a, b in zip(level[::2], level[1::2])]
    return level[0]


def verify_chain(entries: list[JournalEntry], public_key: str,
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
        body = {k: checkpoint[k] for k in ("seq", "head_hash", "merkle_root")}
        if not verify(public_key, canonical(body), checkpoint["sig"]):
            return VerificationResult(ok=False, checked=len(entries), reason="invalid checkpoint signature")
        n = checkpoint["seq"]
        if n >= len(entries) or entries[n].hash != checkpoint["head_hash"]:
            return VerificationResult(ok=False, checked=len(entries), first_bad_seq=n,
                                      reason="entries missing after a published checkpoint (truncation)")
        if merkle_root([e.hash for e in entries[: n + 1]]) != checkpoint["merkle_root"]:
            return VerificationResult(ok=False, checked=len(entries), reason="Merkle root mismatch")
    return VerificationResult(ok=True, checked=len(entries), head_hash=prev)
