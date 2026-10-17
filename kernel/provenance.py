"""Provenance labels on every value that can reach an action (ARGUS, CaMeL).

Labels are assigned by the kernel, never by a model: readers return structured fields and the
kernel checks each one against the client's history and the merchant directory. A value
computed from several inputs inherits the least trusted label.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel


class Label(str, Enum):
    CLIENT = "client"
    BANK = "bank"
    DOCUMENT_VERIFIED = "document_verified"
    UNTRUSTED = "untrusted"


_RANK = {Label.CLIENT: 2, Label.BANK: 2, Label.DOCUMENT_VERIFIED: 1, Label.UNTRUSTED: 0}


class Labeled(BaseModel):
    value: Any
    label: Label
    source: str = ""

    @property
    def trusted(self) -> bool:
        return self.label != Label.UNTRUSTED


def client(value: Any, source: str = "client message") -> Labeled:
    return Labeled(value=value, label=Label.CLIENT, source=source)


def bank(value: Any, source: str) -> Labeled:
    return Labeled(value=value, label=Label.BANK, source=source)


def untrusted(value: Any, source: str) -> Labeled:
    return Labeled(value=value, label=Label.UNTRUSTED, source=source)


def weakest(*labels: Label) -> Label:
    return min(labels, key=lambda lb: _RANK[lb])


def derive(fn: Callable[..., Any], *inputs: Labeled, source: str = "kernel calculation") -> Labeled:
    """Compute a new value; the result is only as trusted as its least trusted input."""
    label = weakest(*(i.label for i in inputs)) if inputs else Label.BANK
    if label == Label.CLIENT:
        label = Label.BANK
    sources = "; ".join(i.source for i in inputs if i.source)
    return Labeled(value=fn(*(i.value for i in inputs)), label=label, source=f"{source} ({sources})" if sources else source)


def verify_document_field(value: Any, document: str, known: dict[str, Any]) -> Labeled:
    """A field read from an untrusted document becomes document_verified only if it matches
    a value the bank already trusts (history, merchant directory); otherwise it stays untrusted."""
    for source, expected in known.items():
        if expected is not None and _norm(expected) == _norm(value):
            return Labeled(value=value, label=Label.DOCUMENT_VERIFIED, source=f"{document}, matches {source}")
    return untrusted(value, document)


def _norm(v: Any) -> Any:
    return "".join(str(v).split()).lower() if isinstance(v, str) else v
