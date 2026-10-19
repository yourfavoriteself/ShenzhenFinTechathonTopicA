"""Duress mode.

If the owner is coerced (robbery, "virtual kidnapping", a fake police officer on a video
call), they enter a secret duress PIN instead of their normal PIN. The app then behaves
exactly as if the transfer succeeded, while the money is quarantined inside the bank and
a silent alert goes to pre-agreed trusted contacts.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from pydantic import BaseModel

from .ledger import Bank
from .models import Initiator, PaymentRequest, Rail

COERCION_TERMS = ["police", "embassy", "consulate", "arrest", "warrant", "deport", "kidnap", "ransom",
                  "do not tell", "don't tell", "keep this secret", "stay on the call", "video call",
                  "hotel", "safe account", "investigation", "money laundering"]


def _hash_pin(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 12, r=8, p=1, dklen=32)


class TrustedContact(BaseModel):
    name: str
    channel: str  # sms | email | campus_security
    address: str


class DuressGuard:
    def __init__(self, normal_pin: str, duress_pin: str, contacts: list[TrustedContact]):
        if normal_pin == duress_pin:
            raise ValueError("duress PIN must differ from the normal PIN")
        self._salt = secrets.token_bytes(16)
        self._normal = _hash_pin(normal_pin, self._salt)
        self._duress = _hash_pin(duress_pin, self._salt)
        self.contacts = contacts
        self.failed_attempts = 0

    MAX_FAILED = 5

    def check_pin(self, pin: str) -> str:
        if self.failed_attempts >= self.MAX_FAILED:
            return "locked"
        h = _hash_pin(pin, self._salt)
        # Both comparisons always run, so timing does not reveal which PIN was entered.
        is_normal = hmac.compare_digest(h, self._normal)
        is_duress = hmac.compare_digest(h, self._duress)
        if is_duress:
            return "duress"
        if is_normal:
            self.failed_attempts = 0
            return "normal"
        self.failed_attempts += 1
        return "invalid"

    @staticmethod
    def coercion_signals(req: PaymentRequest, bank: Bank) -> list[str]:
        """Soft detection of coercion when no duress PIN was used."""
        signals = []
        text = " ".join(t for t in (req.source_message, req.memo) if t).lower()
        hits = [t for t in COERCION_TERMS if t in text]
        if hits:
            signals.append(f"coercion language: {', '.join(hits[:4])}")
        if bank.total and req.amount >= 0.6 * bank.total:
            signals.append(f"amount is {req.amount / bank.total:.0%} of all funds")
        if req.rail in (Rail.INTERNATIONAL_TRANSFER, Rail.CRYPTO, Rail.GIFT_CARD):
            signals.append(f"hard-to-reverse rail: {req.rail.value}")
        if bank.is_new_payee(req.payee):
            signals.append("first payment to this recipient")
        return signals if len(signals) >= 3 else []

    def alerts_for(self, req: PaymentRequest, location: str) -> list[dict]:
        return [{"to": c.name, "channel": c.channel, "address": c.address,
                 "message": (f"SILENT ALERT: Mia may be under duress. A coerced transfer of "
                             f"${req.amount / 100:,.2f} to '{req.payee.name}' was frozen. Last known location: "
                             f"{location}. Please call her and contact the police if she does not answer.")}
                for c in self.contacts]


def fake_reference() -> str:
    return "TRF" + secrets.token_hex(5).upper()


def receipt(req: PaymentRequest, reference: str, ts: str, balance_after: int) -> dict:
    """The receipt format is identical for real and decoy payments."""
    return {"title": "Transfer sent", "amount": req.amount, "to": req.payee.name, "account": req.payee.account,
            "reference": reference, "time": ts,
            "status": "Processing: international transfers can take up to 2 hours"
            if req.rail == Rail.INTERNATIONAL_TRANSFER else "Completed",
            "available_balance": balance_after}


__all__ = ["DuressGuard", "TrustedContact", "receipt", "fake_reference", "Initiator"]
