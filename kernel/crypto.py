"""Signing helpers: kernel Ed25519 keys, canonical JSON hashing, client device signatures.

Device keys come from WebCrypto in the browser: Ed25519 (raw 32-byte public key) or, where
Ed25519 is unavailable, ECDSA P-256 (raw uncompressed public key, raw r||s signature).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

ED25519 = "Ed25519"
ES256 = "ES256"


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def obj_hash(obj: Any) -> str:
    return sha256_hex(canonical(obj))


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
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_obj(public_b64: str, obj: Any, signature_b64: str) -> bool:
    return verify(public_b64, canonical(obj), signature_b64)


def verify_device(alg: str, public_b64: str, data: bytes, signature_b64: str) -> bool:
    if alg == ED25519:
        return verify(public_b64, data, signature_b64)
    if alg != ES256:
        return False
    try:
        pk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(public_b64))
        raw = base64.b64decode(signature_b64)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pk.verify(der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def mandate_signing_payload(mandate_hash: str) -> bytes:
    """What the client's device signs when the client presses "Yes" (domain-separated)."""
    return f"guardian-mandate:{mandate_hash}".encode()


class DeviceKey:
    """Test and demo stand-in for the browser's non-extractable WebCrypto key."""

    def __init__(self, alg: str = ED25519, seed: str | None = None):
        self.alg = alg
        if alg == ED25519:
            self._kp = KeyPair.from_seed(seed) if seed else KeyPair()
            self.public_b64 = self._kp.public_b64
        else:
            self._ec = ec.generate_private_key(ec.SECP256R1())
            raw = self._ec.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
            self.public_b64 = base64.b64encode(raw).decode()

    def sign(self, data: bytes) -> str:
        if self.alg == ED25519:
            return self._kp.sign(data)
        r, s = decode_dss_signature(self._ec.sign(data, ec.ECDSA(hashes.SHA256())))
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()

    def sign_mandate(self, mandate_hash: str) -> str:
        return self.sign(mandate_signing_payload(mandate_hash))
