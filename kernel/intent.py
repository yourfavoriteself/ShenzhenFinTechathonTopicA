"""Intents: what an agent proposes. Agents send values with a reference to where each value came
from; the kernel resolves the reference and assigns the provenance label itself."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .mandate import Action, new_id
from .provenance import Label, Labeled, untrusted


class Claim(BaseModel):
    value: Any
    ref: str | None = None


class IntentProposal(BaseModel):
    action: Action
    payee: Claim | None = None
    amount: Claim | None = None
    funding_source: Claim | None = None
    domain: Claim | None = None
    mandate_id: str | None = None
    memo: str = ""
    references: list[str] = []


class Intent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("int"))
    client_id: str
    task_id: str
    proposed_by: str
    action: Action
    payee: Labeled | None = None
    amount: Labeled | None = None
    funding_source: Labeled | None = None
    domain: Labeled | None = None
    mandate_id: str | None = None
    memo: str = ""
    references: list[str] = []

    def fields(self) -> dict[str, Labeled]:
        return {k: v for k in ("payee", "amount", "funding_source", "domain") if (v := getattr(self, k)) is not None}

    def val(self, name: str) -> Any:
        f = getattr(self, name)
        return None if f is None else f.value

    def summary(self) -> dict[str, Any]:
        return {"intent_id": self.id, "action": self.action.value, "payee": self.val("payee"),
                "amount": self.val("amount"), "funding_source": self.val("funding_source"),
                "domain": self.val("domain"), "mandate_id": self.mandate_id,
                "labels": {k: v.label.value for k, v in self.fields().items()}}


class ProvenanceStore:
    """Values the kernel already knows, by reference: client messages, bank data, verified documents."""

    def __init__(self):
        self._values: dict[str, Labeled] = {}
        self._messages: dict[str, str] = {}

    def put(self, ref: str, value: Labeled) -> str:
        self._values[ref] = value
        return ref

    def put_client_message(self, msg_id: str, text: str) -> str:
        self._messages[msg_id] = text
        return f"client:{msg_id}"

    def exists(self, ref: str) -> bool:
        return ref in self._values or (ref.startswith("client:") and ref[7:] in self._messages)

    def resolve(self, claim: Claim) -> Labeled:
        ref = claim.ref
        if ref and ref.startswith("client:") and ref[7:] in self._messages:
            if _mentions(self._messages[ref[7:]], claim.value):
                return Labeled(value=claim.value, label=Label.CLIENT, source=f"client message {ref[7:]}")
        elif ref and ref in self._values and self._values[ref].value == claim.value:
            return self._values[ref]
        return untrusted(claim.value, f"agent-supplied ({ref or 'no reference'})")


def _mentions(text: str, value: Any) -> bool:
    t = re.sub(r"(?<=\d)[\s,](?=\d{3}\b)", "", text.lower())
    if isinstance(value, int):
        return value % 100 == 0 and re.search(rf"(?<![\d.]){value // 100}(?![\d])", t) is not None
    return "".join(str(value).lower().removeprefix("acct:").split()) in "".join(t.split())
