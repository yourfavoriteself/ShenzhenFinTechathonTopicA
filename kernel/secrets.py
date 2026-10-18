"""Secret vault: the client's website credentials, encrypted and bound to one domain.

Agents only ever see references such as `{{secret:streambox.password}}`. The kernel swaps the
reference for the value at the moment it types into a page field, and only if the page is on the
domain the secret is bound to (the domain is part of the AES-GCM associated data, so a mismatch
cannot even decrypt). One-time secrets are destroyed after use. Every plaintext is registered
with the LLM gateway's guard, so a leak into a prompt fails the call instead of reaching a model.
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel

from .clock import SimClock

REF = re.compile(r"\{\{secret:([a-z0-9_.-]+)\}\}")


class SecretError(Exception):
    pass


class SecretEntry(BaseModel):
    client_id: str
    name: str
    domain: str
    nonce: str
    ciphertext: str
    remember: bool
    created: datetime
    uses: int = 0


def ref(name: str) -> str:
    return "{{secret:" + name + "}}"


def refs_in(text: str) -> list[str]:
    return REF.findall(text)


class Vault:
    def __init__(self, clock: SimClock, key: bytes | None = None):
        self.clock = clock
        self._aead = AESGCM(key or AESGCM.generate_key(bit_length=256))
        self._entries: dict[tuple[str, str], SecretEntry] = {}
        self.listeners: list[Callable[[str], None]] = []

    def __repr__(self) -> str:
        return f"Vault({len(self._entries)} entries)"

    @staticmethod
    def _aad(client_id: str, name: str, domain: str) -> bytes:
        return f"{client_id}|{name}|{domain}".encode()

    def put(self, client_id: str, name: str, domain: str, value: str, *, remember: bool = False) -> str:
        if not REF.fullmatch(ref(name)):
            raise SecretError("bad secret name")
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, value.encode(), self._aad(client_id, name, domain))
        self._entries[(client_id, name)] = SecretEntry(
            client_id=client_id, name=name, domain=domain, nonce=base64.b64encode(nonce).decode(),
            ciphertext=base64.b64encode(ct).decode(), remember=remember, created=self.clock.now())
        for fn in self.listeners:
            fn(value)
        return ref(name)

    def has(self, client_id: str, name: str, domain: str | None = None) -> bool:
        e = self._entries.get((client_id, name))
        return e is not None and (domain is None or e.domain == domain)

    def describe(self, client_id: str) -> list[dict]:
        """What the client may see in settings: names, domains and whether remembered. Never values."""
        return [{"name": e.name, "domain": e.domain, "remember": e.remember, "uses": e.uses}
                for e in self._entries.values() if e.client_id == client_id]

    def ciphertexts(self, client_id: str) -> list[SecretEntry]:
        """Encrypted entries for persistence; the key never leaves this object."""
        return [e.model_copy() for e in self._entries.values() if e.client_id == client_id]

    def fill(self, client_id: str, text: str, domain: str) -> str:
        """Substitute every reference in `text` for a field on `domain`."""
        def sub(m: re.Match) -> str:
            return self._open(client_id, m.group(1), domain)
        return REF.sub(sub, text)

    def _open(self, client_id: str, name: str, domain: str) -> str:
        e = self._entries.get((client_id, name))
        if e is None:
            raise SecretError(f"no secret {name}")
        if e.domain != domain:
            raise SecretError(f"secret {name} is bound to {e.domain}, not {domain}")
        try:
            value = self._aead.decrypt(base64.b64decode(e.nonce), base64.b64decode(e.ciphertext),
                                       self._aad(client_id, name, domain)).decode()
        except InvalidTag as ex:
            raise SecretError(f"secret {name} failed integrity check") from ex
        e.uses += 1
        if not e.remember:
            del self._entries[(client_id, name)]
        return value

    def forget(self, client_id: str, name: str | None = None, *, one_time_only: bool = False) -> int:
        keys = [k for k, e in self._entries.items() if k[0] == client_id and name in (None, e.name)
                and not (one_time_only and e.remember)]
        for k in keys:
            del self._entries[k]
        return len(keys)
