"""Bills and e-mails: the kernel screens them on arrival and verifies what a reader extracted.

Readers are small models without tools; they return fields by schema and nothing else. The kernel
then checks every field with code: the value must literally occur in the document, the account must
match the merchant directory, the sender domain must be the merchant's official one, the amount must
fit the client's history. Only then does a field get the `document_verified` label and a reference
agents may cite. Look-alike names, changed details, urgency and instructions for AI agents go to the
suspicious-event memory and, for bills, into a case file for the bank's fraud analysts.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

from .injection import Finding, scan_text, urgency
from .mandate import new_id
from .merchants import MerchantDirectory
from .provenance import Label, Labeled, untrusted
from .suspicious_memory import ACCOUNT_PREFIX, SuspiciousEvent, SuspiciousMemory

ACCOUNT_RE = re.compile(r"\b\d{20}\b")
DETAILS_CHANGE_RE = re.compile(r"(bank details|payment details|settlement bank|new account|реквизит)", re.I)
AMOUNT_TOLERANCE = 1.5


class Doc(BaseModel):
    """What the kernel needs from a document; `data.generator.Document` fits."""
    id: str
    ts: datetime
    kind: str
    sender: str
    sender_domain: str
    subject: str
    body: str


class Extraction(BaseModel):
    """Reader output schema. Amount as written in the document (e.g. "219.12")."""
    kind: Literal["bill", "details_change", "price_change", "trial", "other"]
    payee_name: str | None = None
    account: str | None = None
    amount: str | None = None
    due: date | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class FieldCheck(BaseModel):
    name: str
    value: Any
    label: Label
    note: str
    ref: str | None = None


class Screening(BaseModel):
    doc_id: str
    findings: list[Finding] = []
    urgency: list[str] = []
    impersonates: str | None = None
    accounts: list[str] = []
    events: list[str] = []


class CaseFile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("case"))
    client_id: str
    doc_id: str
    opened: datetime
    title: str
    indicators: list[str]
    sender: str
    impersonates: str | None = None
    account: str | None = None
    amount: int | None = None
    related_events: list[SuspiciousEvent] = []
    recommendation: str


class DocCheck(BaseModel):
    doc_id: str
    verdict: Literal["verified", "suspicious", "not_a_bill"]
    payee: str | None = None
    payee_name: str | None = None
    amount: int | None = None
    due: date | None = None
    fields: list[FieldCheck] = []
    reasons: list[str] = []
    refs: dict[str, str] = {}
    case: CaseFile | None = None


def parse_amount(text: str | None) -> int | None:
    if not text:
        return None
    t = re.sub(r"[\s\u00a0$]|USD|dollars?|долл\w*|美元", "", text, flags=re.I)
    t = re.sub(r",(?=\d{3}(?:\D|$))", "", t).replace(",", ".")
    try:
        return int((Decimal(t) * 100).to_integral_value())
    except InvalidOperation:
        return None


def _occurs(value: str, body: str) -> bool:
    squash = lambda s: re.sub(r"[\s ,]", "", s.lower())  # noqa: E731
    return bool(value) and squash(value) in squash(body)


class DocumentDesk:
    def __init__(self, kernel, merchants: MerchantDirectory, memory: SuspiciousMemory):
        self.k = kernel
        self.merchants = merchants
        self.memory = memory
        self.screened: dict[str, Screening] = {}
        self.cases: list[CaseFile] = []

    # ------------------------------------------------------------------ on arrival (code only, no LLM)

    def screen(self, client_id: str, doc: Doc) -> Screening:
        if doc.id in self.screened:
            return self.screened[doc.id]
        text = f"{doc.subject}\n{doc.body}"
        s = Screening(doc_id=doc.id, findings=scan_text(doc.body, where=doc.id), urgency=urgency(text),
                      accounts=ACCOUNT_RE.findall(doc.body))
        first_line = doc.body.strip().splitlines()[0] if doc.body.strip() else ""
        fake = self.merchants.lookalike_of(doc.sender_domain) or self.merchants.lookalike_of(first_line)
        named = self._named_merchant(text)
        s.impersonates = fake.token if fake else None
        ev = lambda kind, summary, **kw: s.events.append(self.memory.record(  # noqa: E731
            ts=doc.ts, client_id=client_id, kind=kind, summary=summary, source=doc.id,
            domain=doc.sender_domain, **kw).id)
        if fake:
            ev("lookalike", f"{doc.kind} from {doc.sender_domain} imitates {fake.name}", impersonates=fake.token,
               account=s.accounts[0] if s.accounts else None)
        target = fake or named
        if target and DETAILS_CHANGE_RE.search(text):
            ev("details_change_request", f"e-mail from {doc.sender_domain} asks to pay {target.name} to new "
                                         f"account {s.accounts[0] if s.accounts else '?'}",
               impersonates=target.token, account=s.accounts[0] if s.accounts else None)
        flagged = {e.account for e in self.memory.for_client(client_id) if e.source == doc.id}
        for a in s.accounts:
            known = self.merchants.find_by_account(a)
            if target and (known is None or known.token != target.token) and a not in flagged:
                ev("unverified_payee", f"account {a} in a document about {target.name} is not {target.name}'s "
                                       f"account in the bank's directory", impersonates=target.token, account=a)
        for f in s.findings:
            ev("injection", f"instruction for AI agents hidden in {doc.kind} from {doc.sender_domain}: {f.technique}",
               account=s.accounts[0] if s.accounts else None)
        if s.urgency and (fake or s.findings):
            ev("urgency", f"pressure in {doc.kind} from {doc.sender_domain}: {', '.join(s.urgency)}",
               account=s.accounts[0] if s.accounts else None)
        self.screened[doc.id] = s
        if s.events:
            self.k.journal.append("kernel", "document.flagged", {"doc": doc.id, "events": s.events})
        return s

    def _named_merchant(self, text: str):
        low = text.lower()
        for m in self.merchants.by_token.values():
            if m.kind in ("biller", "subscription") and re.search(rf"\b{re.escape(m.name.lower())}\b", low):
                return m
        return None

    # ------------------------------------------------------------------ after a reader extracted fields

    def verify(self, client_id: str, doc: Doc, ex: Extraction) -> DocCheck:
        s = self.screen(client_id, doc)
        check = DocCheck(doc_id=doc.id, verdict="not_a_bill")
        if ex.kind != "bill":
            return check
        reasons: list[str] = []
        for name in ("payee_name", "account", "amount"):
            val = getattr(ex, name)
            if val is not None and not _occurs(str(val), f"{doc.subject}\n{doc.body}"):
                reasons.append(f"the reader returned a {name.replace('_', ' ')} that is not in the document")
        merchant = self.merchants.find_by_name(ex.payee_name or "") if ex.payee_name else None
        fake = self.merchants.lookalike_of(ex.payee_name or "") if ex.payee_name and merchant is None else None
        fake = fake or (self.merchants.get(s.impersonates) if s.impersonates else None)
        if fake:
            reasons.append(f'"{ex.payee_name}" from {doc.sender_domain} imitates {fake.name}')
        by_account = self.merchants.find_by_account(ex.account) if ex.account else None
        amount = parse_amount(ex.amount)
        check.amount, check.due = amount, ex.due
        if merchant and not fake and by_account and by_account.token == merchant.token:
            if merchant.domain and doc.sender_domain != merchant.domain:
                reasons.append(f"sent from {doc.sender_domain}, not {merchant.name}'s domain {merchant.domain}")
            payee = Labeled(value=merchant.token, label=Label.DOCUMENT_VERIFIED,
                            source=f"{doc.id}: account matches {merchant.name} in the bank's directory")
            check.payee, check.payee_name = merchant.token, merchant.name
        else:
            target = fake or merchant
            if ex.account and target:
                reasons.append(f"account {ex.account} is not {target.name}'s account in the bank's directory")
            elif not ex.account:
                reasons.append("no payee account in the document")
            token = f"{ACCOUNT_PREFIX}{ex.account}" if ex.account else None
            payee = untrusted(token, f"{doc.id}: account not in the bank's directory")
            check.payee, check.payee_name = token, ex.payee_name
        info = self.k.directory.payee(client_id, merchant.token) if merchant and payee.trusted else None
        if amount is None:
            reasons.append("no amount in the document")
            amt = None
        elif payee.trusted and (info is None or info.usual_amount is None
                                or amount <= info.usual_amount * AMOUNT_TOLERANCE):
            amt = Labeled(value=amount, label=Label.DOCUMENT_VERIFIED,
                          source=f"{doc.id}: verified biller, amount within the usual range")
        else:
            amt = untrusted(amount, f"{doc.id}: amount not confirmed by history")
            if info and info.usual_amount and amount > info.usual_amount * AMOUNT_TOLERANCE:
                reasons.append("amount is well above this payee's usual bills")
        if s.findings:
            reasons += [f"hidden instruction for AI agents ({f.technique})" for f in s.findings]
        if s.urgency and reasons:
            reasons.append(f"pressure: {', '.join(s.urgency)}")
        linked = self.memory.related(client_id, account=ex.account, exclude_source=doc.id) if ex.account else []
        reasons += [f"linked to an earlier suspicious event ({e.ts.date()}): {e.summary}" for e in linked]
        check.fields = [FieldCheck(name="payee", value=payee.value, label=payee.label, note=payee.source)]
        if amt is not None:
            check.fields.append(FieldCheck(name="amount", value=amt.value, label=amt.label, note=amt.source))
        if payee.value is not None:
            check.refs["payee"] = self.k.publish(f"doc:{doc.id}:payee", payee)
        if amt is not None:
            check.refs["amount"] = self.k.publish(f"doc:{doc.id}:amount", amt)
        for f, key in zip(check.fields, ("payee", "amount")):
            f.ref = check.refs.get(key)
        check.reasons = reasons
        check.verdict = "suspicious" if reasons else "verified"
        if reasons:
            check.case = self._open_case(client_id, doc, check, s, fake, linked, ex.account)
        self.k.journal.append("kernel", "document.verified", {"doc": doc.id, "verdict": check.verdict,
                                                              "reasons": reasons, "labels": {
                                                                  f.name: f.label.value for f in check.fields}})
        return check

    def _open_case(self, client_id, doc: Doc, check: DocCheck, s: Screening, fake, linked, account) -> CaseFile:
        related = [e for e in self.memory.for_client(client_id) if e.source == doc.id] + linked
        case = CaseFile(client_id=client_id, doc_id=doc.id, opened=self.k.clock.now(),
                        title=f"Suspected fake bill: {doc.subject}", indicators=check.reasons, sender=doc.sender,
                        impersonates=fake.token if fake else None, account=account, amount=check.amount,
                        related_events=related,
                        recommendation="Do not pay. Add the account to the mule watchlist and warn other clients "
                                       "who received mail from this sender.")
        self.cases.append(case)
        self.k.journal.append("kernel", "case.opened", {"case": case.id, "doc": doc.id, "account": account,
                                                        "indicators": len(case.indicators)})
        return case
