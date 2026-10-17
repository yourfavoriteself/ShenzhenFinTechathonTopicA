"""Ed25519 signing helpers and canonical JSON hashing."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class KeyPair:
    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        self._sk = private_key or Ed25519PrivateKey.generate()
        self._pk = self._sk.public_key()

    @classmethod
    def from_seed(cls, seed: str) -> "KeyPair":
        """Deterministic key for reproducible demos. Never use seeded keys in production."""
        return cls(Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed.encode()).digest()))

    def sign(self, data: bytes) -> str:
        return base64.b64encode(self._sk.sign(data)).decode()

    def sign_obj(self, obj: Any) -> str:
        return self.sign(canonical(obj))

    @property
    def public_b64(self) -> str:
        raw = self._pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()


def verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
        pk.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_obj(public_b64: str, obj: Any, signature_b64: str) -> bool:
    return verify(public_b64, canonical(obj), signature_b64)
