"""Cancellation desk: finishes a signed "cancel this subscription" at the merchant and proves it.

The executor first has the bank block future charges from the merchant on that card (insurance),
then opens a cancellation here. The desk talks to the merchant only on its official domain,
checked against the directory, look-alikes and the certificate. Credentials come from the vault by
reference and are filled in by the kernel; the SMS code is typed by the client. Nothing secret is
stored on the cancellation record, and the merchant's full card number is reduced to the last four
digits at once. The result is verified from three independent sources: the merchant account, the
bank's card-on-file record plus an accelerated-time run with no next charge, and the confirmation
email from the official domain.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Protocol

from pydantic import BaseModel, Field

from .mandate import Grant, new_id
from .merchants import MerchantDirectory, domain_stem
from .secrets import SecretError, ref

if TYPE_CHECKING:
    from .core import Kernel
    from .intent import Intent


class PortalError(Exception):
    pass


class MerchantRails(Protocol):
    def certificate(self, domain: str) -> dict | None: ...
    def login(self, domain: str, login: str, password: str) -> dict: ...
    def verify_sms(self, challenge: str, code: str) -> str: ...
    def account(self, session: str) -> dict: ...
    def cancel(self, session: str) -> dict: ...


class DomainCheck(BaseModel):
    domain: str
    official: str | None
    matches: bool
    imitates: str | None = None
    certificate: dict | None = None
    ok: bool
    reasons: list[str] = []


class Cancellation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cxl"))
    client_id: str
    task_id: str
    mandate_id: str
    merchant: str
    merchant_name: str
    domain: str
    card_id: str
    amount: int | None = None
    status: str = "login_needed"     # login_needed | sms_needed | verified | failed
    domain_check: DomainCheck
    phone: str | None = None
    checks: dict[str, bool] = {}
    error: str | None = None
    opened: datetime
    finished: datetime | None = None


def secret_names(domain: str) -> dict[str, str]:
    s = domain_stem(domain)
    return {"login": f"{s}.login", "password": f"{s}.password", "sms": f"{s}.sms"}


class CancelDesk:
    def __init__(self, kernel: "Kernel", merchants: MerchantDirectory, portal: MerchantRails, bank: Any,
                 inbox: Callable[[], list], errors: tuple[type[Exception], ...] = ()):
        self.k, self.merchants, self.portal, self.bank, self.inbox = kernel, merchants, portal, bank, inbox
        self.errors = (PortalError, *errors)
        self.items: dict[str, Cancellation] = {}
        self._challenge: dict[str, str] = {}

    # ------------------------------------------------------------------ domain

    def check_domain(self, merchant: str, domain: str) -> DomainCheck:
        official = self.merchants.official_domain(merchant)
        reasons = []
        matches = official is not None and domain == official
        if not matches:
            reasons.append(f"{domain} is not the official domain {official or '(none on record)'}")
        fake = self.merchants.lookalike_of(domain)
        if fake is not None:
            reasons.append(f"{domain} imitates {fake.name}")
        cert = self.portal.certificate(domain) if matches else None
        if matches and not (cert and cert.get("valid") and cert.get("subject") == domain):
            reasons.append("no valid certificate for the domain")
        return DomainCheck(domain=domain, official=official, matches=matches, imitates=fake.name if fake else None,
                           certificate=cert, ok=not reasons, reasons=reasons)

    # ------------------------------------------------------------------ lifecycle

    def open(self, grant: Grant, intent: "Intent") -> Cancellation:
        m = self.merchants.get(grant.payee or "")
        check = self.check_domain(grant.payee or "", grant.domain or "")
        c = Cancellation(client_id=intent.client_id, task_id=intent.task_id, mandate_id=grant.mandate_id,
                         merchant=grant.payee or "", merchant_name=m.name if m else str(grant.payee),
                         domain=grant.domain or "", card_id=grant.funding_source or "", amount=intent.val("amount"),
                         domain_check=check, opened=self.k.clock.now())
        if not check.ok:
            c.status, c.error = "failed", "; ".join(check.reasons)
        self.items[c.id] = c
        self._journal(c, "cancellation.opened", {"domain_check": check.model_dump(mode="json")})
        return c

    def has_credentials(self, c: Cancellation) -> bool:
        n = secret_names(c.domain)
        return self.k.vault.has(c.client_id, n["login"], c.domain) and \
            self.k.vault.has(c.client_id, n["password"], c.domain)

    def store_credentials(self, c: Cancellation, login: str, password: str, remember: bool) -> None:
        """Called from the bank's protected form: values go straight into the vault, bound to the domain."""
        n = secret_names(c.domain)
        self.k.vault.put(c.client_id, n["login"], c.domain, login, remember=remember)
        self.k.vault.put(c.client_id, n["password"], c.domain, password, remember=remember)
        self._journal(c, "credentials.stored", {"remember": remember, "refs": [ref(n["login"]), ref(n["password"])]})

    def store_code(self, c: Cancellation, code: str) -> None:
        self.k.vault.put(c.client_id, secret_names(c.domain)["sms"], c.domain, code)

    def sign_in(self, cid: str) -> Cancellation:
        c = self.items[cid]
        if c.status not in ("login_needed", "sms_needed"):
            return c
        n = secret_names(c.domain)
        try:
            login = self.k.vault.fill(c.client_id, ref(n["login"]), c.domain)
            password = self.k.vault.fill(c.client_id, ref(n["password"]), c.domain)
            r = self.portal.login(c.domain, login, password)
        except SecretError as e:
            c.status, c.error = "login_needed", str(e)
            return c
        except self.errors as e:
            self.k.vault.forget(c.client_id, n["password"])
            c.status, c.error = "login_needed", str(e)
            self._journal(c, "merchant.login_failed", {"reason": str(e)})
            return c
        self._challenge[cid] = r["challenge"]
        c.status, c.phone, c.error = "sms_needed", r.get("phone"), None
        self._journal(c, "merchant.sms_sent", {"phone": c.phone})
        return c

    def complete(self, cid: str) -> Cancellation:
        c = self.items[cid]
        if c.status != "sms_needed":
            return c
        try:
            code = self.k.vault.fill(c.client_id, ref(secret_names(c.domain)["sms"]), c.domain)
            session = self.portal.verify_sms(self._challenge[cid], code)
            acct = self.portal.account(session)
            last4 = str(acct.pop("card_number", ""))[-4:]
            card = self.bank.cards.get(c.card_id)
            if card is None or last4 != card.last4:
                c.status, c.error = "failed", f"this {c.merchant_name} account is not paid with card *{card.last4 if card else '?'}"
                self._journal(c, "cancellation.failed", {"reason": c.error})
                return c
            self.portal.cancel(session)
        except SecretError as e:
            c.error = str(e)
            return c
        except self.errors as e:
            c.error = str(e)
            if "expired" in str(e):
                c.status = "login_needed"
            self._journal(c, "merchant.error", {"reason": str(e)})
            return c
        self._challenge.pop(cid, None)
        c.checks = self.verify(c)
        c.status = "verified" if all(c.checks.values()) else "failed"
        c.finished, c.error = self.k.clock.now(), None if c.status == "verified" else "the kernel could not confirm the result"
        self._journal(c, f"cancellation.{c.status}", {"checks": c.checks})
        return c

    # ------------------------------------------------------------------ result

    def verify(self, c: Cancellation) -> dict[str, bool]:
        cofs = [x for x in self.bank.cof.values() if x.client_id == c.client_id and x.merchant == c.merchant
                and x.card_id == c.card_id]
        merchant_side = bool(cofs) and not any(x.active for x in cofs)
        email = any(d.sender_domain == c.domain and "cancel" in d.subject.lower() and d.ts >= c.opened
                    for d in self.inbox())
        return {"merchant_card_on_file_removed": merchant_side, "confirmation_email_from_official_domain": email,
                "no_next_charge_in_accelerated_time": self._no_next_charge(c, cofs)}

    def _no_next_charge(self, c: Cancellation, cofs: list) -> bool:
        """Run a twin of the bank past the next billing date with the insurance block lifted."""
        if not cofs:
            return False
        twin = self.bank.twin()
        twin.blocks = [b for b in twin.blocks if not (b.merchant == c.merchant and b.card_id in (None, c.card_id))]
        start = twin.clock.now()
        until = max(x.next_date for x in cofs) + timedelta(days=1)
        twin.run_until(datetime.combine(until, start.timetz()))
        charged = [t for t in twin.transactions(c.client_id, since=start)
                   if t.counterparty == c.merchant and t.card_id == c.card_id]
        tried = [d for d in twin.declines if d.ts >= start and d.merchant == c.merchant and d.card_id == c.card_id
                 and d.reason != "card on file cancelled"]
        return not charged and not tried

    def public(self, c: Cancellation) -> dict[str, Any]:
        return c.model_dump(mode="json")

    def _journal(self, c: Cancellation, event: str, data: dict) -> None:
        self.k.journal.append("kernel", event, {"cancellation": c.id, "merchant": c.merchant, "domain": c.domain,
                                                "card": c.card_id, **data})
