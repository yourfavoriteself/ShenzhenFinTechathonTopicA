"""Extraction and validation of fraud indicators from free text.

Used by the Scam Shield (to read payment requests) and by the Scam Baiter (to harvest
mule accounts, wallets and contacts). Checksums are validated so a typo is not reported
as a confirmed indicator.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

IBAN_LENGTHS = {"GB": 22, "DE": 22, "LT": 20, "FR": 27, "ES": 24, "NL": 18, "IE": 22, "AE": 23, "PL": 28}

RE_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?)\b")
RE_BTC = re.compile(r"\b((?:bc1|tb1)[02-9ac-hj-np-z]{11,71})\b", re.I)
RE_ETH = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
RE_URL = re.compile(r"\bhttps?://[^\s<>\"')]+", re.I)
RE_EMAIL = re.compile(r"(?<![\w.])(?:payid:)?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)")
RE_HANDLE = re.compile(r"(?<![\w.@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
RE_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
RE_PHONE = re.compile(r"(?<![\w+])(\+\d{1,3}[ -]?\d{2,4}(?:[ -]?\d{2,4}){2,3}|0[45]\d{2}[ -]?\d{3}[ -]?\d{3})\b")
RE_BSB = re.compile(r"\bBSB[:\s]*(\d{3}-?\d{3})\D{0,20}?(?:acc(?:ount)?(?:\s*(?:no\.?|number))?)[:\s#]*(\d[\d ]{5,10}\d)", re.I)
RE_AMOUNT = re.compile(r"(?:A\$|AU\$|AUD|US\$|USD|\$)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?", re.I)

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


class Indicator(BaseModel):
    kind: str  # iban | btc | eth | url | email | handle | card | phone | bsb_account
    value: str
    valid: bool
    note: str = ""


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    if s[:2] in IBAN_LENGTHS and len(s) != IBAN_LENGTHS[s[:2]]:
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def iban_with_checksum(country: str, bban: str) -> str:
    digits = "".join(str(int(c, 36)) for c in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def luhn_valid(number: str) -> bool:
    ds = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def bech32_valid(addr: str) -> bool:
    a = addr.lower()
    pos = a.rfind("1")
    hrp, data = a[:pos], a[pos + 1:]
    if hrp not in ("bc", "tb") or len(data) < 6 or any(c not in BECH32_CHARSET for c in data):
        return False
    return _bech32_polymod(_hrp_expand(hrp) + [BECH32_CHARSET.index(c) for c in data]) in (1, 0x2BC830A3)


def bech32_address(hrp: str, program: bytes) -> str:
    """Encode a segwit v0 address (used to create fictional demo wallets)."""
    acc, bits, data = 0, 0, [0]
    for b in program:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    poly = _bech32_polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    data += [(poly >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(BECH32_CHARSET[d] for d in data)


def extract_indicators(text: str) -> list[Indicator]:
    found: list[Indicator] = []
    work = text

    def take(regex: re.Pattern, kind: str, check=None, group: int = 0, note: str = ""):
        nonlocal work
        for m in regex.finditer(work):
            value = m.group(group).strip().rstrip(".,;")
            found.append(Indicator(kind=kind, value=value, valid=check(value) if check else True, note=note))
        work = regex.sub(lambda m: " " * len(m.group(0)), work)

    take(RE_URL, "url")
    take(RE_IBAN, "iban", iban_valid, group=1)
    take(RE_BTC, "btc", bech32_valid, group=1)
    take(RE_ETH, "eth", group=1)
    take(RE_EMAIL, "email", group=1)
    take(RE_HANDLE, "handle", group=0)
    for m in RE_BSB.finditer(work):
        found.append(Indicator(kind="bsb_account", value=f"BSB {m.group(1)} ACC {m.group(2).replace(' ', '')}", valid=True))
    work = RE_BSB.sub(lambda m: " " * len(m.group(0)), work)
    for m in RE_CARD.finditer(work):
        if luhn_valid(m.group(0)):
            found.append(Indicator(kind="card", value=m.group(0), valid=True))
            work = work[:m.start()] + " " * len(m.group(0)) + work[m.end():]
    take(RE_PHONE, "phone", group=1)
    seen, out = set(), []
    for i in found:
        key = (i.kind, i.value.replace(" ", "").lower())
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def extract_amounts(text: str) -> list[int]:
    """Amounts in cents, in order of appearance."""
    out = []
    for m in RE_AMOUNT.finditer(text):
        out.append(int(m.group(1).replace(",", "")) * 100 + int(m.group(2) or 0))
    return out


PAYMENT_KINDS = ("iban", "bsb_account", "email", "btc", "eth")
BLOCKABLE_KINDS = ("iban", "bsb_account", "email", "btc", "eth", "phone", "url", "handle")
