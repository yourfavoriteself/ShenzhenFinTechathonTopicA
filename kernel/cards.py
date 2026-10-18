"""Confirmation cards, rendered by the kernel from the mandate itself (never by an LLM).

What you see is what you sign: the card lists what the agent will and will not be able to do,
shows where every value came from, highlights what changed since last time, and carries the
hash of the mandate the client's device signs.
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field

from .crypto import obj_hash
from .intent import Intent
from .mandate import Action, Frequency, Mandate, new_id
from .money import rub
from .ports import PayeeInfo
from .provenance import Label


class Risk(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


ALWAYS_CANNOT = [
    "type or see your card numbers or passwords",
    "open websites outside the list above",
    "pay any other recipient",
    "send your data outside the bank without asking you",
]

_ACTION_TEXT = {
    Action.PAY: "Pay", Action.TRANSFER_OWN: "Move money to", Action.CANCEL_SUBSCRIPTION: "Cancel subscription",
    Action.BLOCK_MERCHANT: "Block future charges", Action.DISPUTE: "Dispute charge", Action.BROWSE: "Browse",
}

_LABEL_BADGE = {
    Label.CLIENT: "from your message",
    Label.BANK: "verified by the bank",
    Label.DOCUMENT_VERIFIED: "from the document, matches bank records",
    Label.UNTRUSTED: "UNVERIFIED SOURCE",
}


class CardField(BaseModel):
    name: str
    value: str
    badge: str | None = None
    warning: bool = False
    source: str | None = None
    changed: bool = False
    previous: str | None = None


class ConfirmationCard(BaseModel):
    id: str = Field(default_factory=lambda: new_id("card"))
    title: str
    risk: Risk
    fields: list[CardField]
    can: list[str]
    cannot: list[str]
    reasons: list[str] = []
    standing_offer: str | None = None
    hold_to_confirm: bool = False
    type_amount: bool = False
    intervention: dict | None = None
    mandate_hash: str = ""
    standing_mandate_hash: str | None = None

    def content_hash(self) -> str:
        return obj_hash(self.model_dump(mode="json", exclude={"id", "mandate_hash", "standing_mandate_hash"}))


def describe(m: Mandate, payee_names: dict[str, str], funding_label: str) -> tuple[list[str], list[str]]:
    can: list[str] = []
    until = m.exp.strftime("%d %b %Y, %H:%M")
    for a in m.actions:
        if m.payment and a in (Action.PAY, Action.TRANSFER_OWN):
            names = ", ".join(payee_names.get(p, _show("payee", p, None, "")) for p in m.payment.allowed_payees)
            r = m.payment.agent_recurrence
            how = "once" if r.frequency == Frequency.ONCE else f"once a {r.frequency.value.removesuffix('ly')}"
            line = f"{_ACTION_TEXT[a]} {names} up to {rub(m.payment.amount_range.max)}, {how}, from {funding_label}"
            if m.payment.budget:
                line += f", {rub(m.payment.budget.total)} in total"
            can.append(f"{line}, until {until}")
        else:
            target = ", ".join(payee_names.get(p, p) for p in (m.payment.allowed_payees if m.payment else []))
            can.append(f"{_ACTION_TEXT[a]}{' ' + target if target else ''}, until {until}")
    for o in m.allowed_origins:
        can.append(f"{'Use' if o.mode.value == 'act' else 'Read'} the website {o.domain}")
    return can, list(ALWAYS_CANNOT)


def render(mandate: Mandate, intent: Intent, payee: PayeeInfo | None, funding_label: str, risk: Risk,
           reasons: list[str], previous: dict | None = None, standing: Mandate | None = None) -> ConfirmationCard:
    names = {payee.token: payee.name} if payee else {}
    fields: list[CardField] = []
    prev_vals = previous or {}
    for name, labeled in intent.fields().items():
        shown = _show(name, labeled.value, payee, funding_label)
        f = CardField(name=name, value=shown, badge=_LABEL_BADGE[labeled.label],
                      warning=labeled.label == Label.UNTRUSTED, source=labeled.source)
        if name == "payee" and payee and payee.verified and labeled.trusted:
            f.badge = "verified by the bank"
        if name in prev_vals and prev_vals[name] != labeled.value:
            f.changed, f.previous = True, _show(name, prev_vals[name], payee, funding_label)
        fields.append(f)
    if payee and payee.details_changed:
        fields.append(CardField(name="payee details", value="changed since your last payment", warning=True,
                                changed=True))
    can, cannot = describe(mandate, names, funding_label)
    offer = None
    if standing is not None:
        s_can, _ = describe(standing, names, funding_label)
        offer = "Next time, without asking: " + s_can[0][0].lower() + s_can[0][1:]
    shown = payee.name if payee else _show("payee", intent.val("payee"), None, "") if intent.val("payee") else ""
    title = f"{_ACTION_TEXT[intent.action]} {shown}".strip()
    return ConfirmationCard(title=title, risk=risk, fields=fields, can=can, cannot=cannot, reasons=reasons,
                            standing_offer=offer, hold_to_confirm=risk == Risk.HIGH,
                            type_amount=risk == Risk.HIGH and intent.amount is not None
                            and not intent.amount.trusted)


def _show(name: str, value, payee: PayeeInfo | None, funding_label: str) -> str:
    if name == "amount":
        return rub(int(value))
    if name == "payee" and payee and payee.token == value:
        return payee.name
    if name == "funding_source":
        return funding_label
    if name == "payee" and str(value).startswith("acct:"):
        return f"account *{str(value)[-4:]}"
    return str(value)
