"""Mandates: exactly what an agent may do after the client says "yes".

Field names follow Mastercard Verifiable Intent constraints (`mandate.payment.allowed_payees`,
`amount_range`, `budget`, `agent_recurrence`, `reference`); `allowed_origins`, `funding_source`
and `secret_refs` are our extensions. A client mandate is signed by the client's device key and
countersigned by the kernel. Rights only narrow: a derived mandate must fit inside its parent,
and executors receive single-use grants for exactly one action.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import Enum, IntEnum

from pydantic import BaseModel, Field

from .clock import SimClock
from .crypto import ED25519, KeyPair, canonical, mandate_signing_payload, obj_hash, verify, verify_device


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Action(str, Enum):
    PAY = "pay"
    CANCEL_SUBSCRIPTION = "cancel_subscription"
    BLOCK_MERCHANT = "block_merchant"
    DISPUTE = "dispute"
    TRANSFER_OWN = "transfer_own"
    BROWSE = "browse"


MONEY_ACTIONS = {Action.PAY, Action.TRANSFER_OWN}


class Frequency(str, Enum):
    ONCE = "once"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"


_FREQ_RANK = {Frequency.ONCE: 0, Frequency.MONTHLY: 1, Frequency.WEEKLY: 2, Frequency.DAILY: 3}


class AutonomyStage(IntEnum):
    """Stages of the 2026 agent payment code. Stage 3 (broad autonomy) is deliberately not supported."""
    ASSISTED = 1
    PRESET_CONDITIONS = 2


class AmountRange(BaseModel):
    min: int = 0
    max: int
    currency: str = "RUB"


class Budget(BaseModel):
    total: int
    currency: str = "RUB"


class Recurrence(BaseModel):
    frequency: Frequency = Frequency.ONCE
    max_occurrences: int | None = 1


class PaymentConstraints(BaseModel):
    allowed_payees: list[str]
    amount_range: AmountRange
    budget: Budget | None = None
    agent_recurrence: Recurrence = Field(default_factory=Recurrence)
    reference: str | None = None


class OriginMode(str, Enum):
    READ = "read"
    ACT = "act"


class OriginRule(BaseModel):
    domain: str
    mode: OriginMode


class Mandate(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mdt"))
    version: str = "vi-0.1"
    client_id: str
    actions: list[Action]
    payment: PaymentConstraints | None = None
    allowed_origins: list[OriginRule] = []
    funding_source: str | None = None
    secret_refs: list[str] = []
    nbf: datetime
    exp: datetime
    standing: bool = False
    single_use: bool = False
    stage: AutonomyStage = AutonomyStage.ASSISTED
    parent_id: str | None = None
    holder: str = "client"

    def body(self) -> dict:
        return self.model_dump(mode="json")

    def hash(self) -> str:
        return obj_hash(self.body())


class SignedMandate(BaseModel):
    mandate: Mandate
    mandate_hash: str
    device_alg: str | None = None
    device_key: str | None = None
    device_sig: str | None = None
    kernel_sig: str


class Grant(BaseModel):
    """Single-use right for one action, handed to an executor."""
    id: str = Field(default_factory=lambda: new_id("grt"))
    mandate_id: str
    holder: str
    action: Action
    payee: str | None = None
    amount: int | None = None
    funding_source: str | None = None
    domain: str | None = None
    exp: datetime
    kernel_sig: str = ""

    def body(self) -> dict:
        return self.model_dump(mode="json", exclude={"kernel_sig"})


class MandateError(Exception):
    pass


class Use(BaseModel):
    at: datetime
    amount: int
    grant_id: str


class MandateState(BaseModel):
    signed: SignedMandate
    revoked: bool = False
    uses: list[Use] = []

    @property
    def spent(self) -> int:
        return sum(u.amount for u in self.uses)


def narrowing_violations(child: Mandate, parent: Mandate) -> list[str]:
    """Why `child` is not strictly inside `parent` (empty list = it is)."""
    v: list[str] = []
    if child.client_id != parent.client_id:
        v.append("different client")
    if extra := set(child.actions) - set(parent.actions):
        v.append(f"actions not in parent: {sorted(a.value for a in extra)}")
    if child.nbf < parent.nbf or child.exp > parent.exp:
        v.append("validity period outside parent")
    if child.standing and not parent.standing:
        v.append("standing mandate derived from a one-time mandate")
    if child.stage > parent.stage:
        v.append("higher autonomy stage than parent")
    if parent.funding_source and child.funding_source != parent.funding_source:
        v.append("different funding source")
    if extra := set(child.secret_refs) - set(parent.secret_refs):
        v.append(f"secrets not in parent: {sorted(extra)}")
    parent_origins = {o.domain: o.mode for o in parent.allowed_origins}
    for o in child.allowed_origins:
        pm = parent_origins.get(o.domain)
        if pm is None or (o.mode == OriginMode.ACT and pm == OriginMode.READ):
            v.append(f"origin not allowed by parent: {o.domain} ({o.mode.value})")
    cp, pp = child.payment, parent.payment
    if pp is None:
        if cp is not None:
            v.append("payment constraints added to a mandate without payments")
    elif cp is None:
        if set(child.actions) & MONEY_ACTIONS:
            v.append("money action without payment constraints")
    else:
        if extra := set(cp.allowed_payees) - set(pp.allowed_payees):
            v.append(f"payees not in parent: {sorted(extra)}")
        if cp.amount_range.max > pp.amount_range.max or cp.amount_range.min < pp.amount_range.min:
            v.append("amount range wider than parent")
        if pp.budget and (cp.budget is None or cp.budget.total > pp.budget.total):
            v.append("budget larger than parent")
        cr, pr = cp.agent_recurrence, pp.agent_recurrence
        if _FREQ_RANK[cr.frequency] > _FREQ_RANK[pr.frequency]:
            v.append("more frequent than parent")
        if pr.max_occurrences is not None and (cr.max_occurrences is None or cr.max_occurrences > pr.max_occurrences):
            v.append("more occurrences than parent")
    return v


class MandateService:
    def __init__(self, kernel_key: KeyPair, clock: SimClock):
        self._key = kernel_key
        self._clock = clock
        self._devices: dict[str, set[tuple[str, str]]] = {}
        self._state: dict[str, MandateState] = {}
        self._grants: dict[str, Grant] = {}
        self._used_grants: set[str] = set()

    # ------------------------------------------------------------------ issuing

    def register_device(self, client_id: str, public_key: str, alg: str = ED25519) -> None:
        self._devices.setdefault(client_id, set()).add((alg, public_key))

    def issue(self, mandate: Mandate, device_alg: str, device_key: str, device_sig: str) -> SignedMandate:
        """Client pressed "Yes": verify the device signature over the mandate hash, then countersign."""
        if (device_alg, device_key) not in self._devices.get(mandate.client_id, set()):
            raise MandateError("device key is not registered for this client")
        h = mandate.hash()
        if not verify_device(device_alg, device_key, mandate_signing_payload(h), device_sig):
            raise MandateError("device signature does not match the mandate")
        if mandate.parent_id is not None:
            raise MandateError("client mandates have no parent")
        signed = SignedMandate(mandate=mandate, mandate_hash=h, device_alg=device_alg, device_key=device_key,
                               device_sig=device_sig, kernel_sig=self._key.sign(h.encode()))
        self._state[mandate.id] = MandateState(signed=signed)
        return signed

    def derive(self, parent_id: str, child: Mandate) -> SignedMandate:
        st = self._state.get(parent_id)
        if st is None or st.revoked:
            raise MandateError("parent mandate is unknown or revoked")
        parent = st.signed.mandate
        child.parent_id = parent_id
        if v := narrowing_violations(child, parent):
            raise MandateError("; ".join(v))
        h = child.hash()
        signed = SignedMandate(mandate=child, mandate_hash=h, kernel_sig=self._key.sign(h.encode()))
        self._state[child.id] = MandateState(signed=signed)
        return signed

    def revoke(self, mandate_id: str) -> list[str]:
        """Revocation is immediate and cascades to every mandate derived from this one."""
        revoked = [mandate_id]
        self._state[mandate_id].revoked = True
        for mid, st in self._state.items():
            if st.signed.mandate.parent_id == mandate_id and not st.revoked:
                revoked += self.revoke(mid)
        return revoked

    # ------------------------------------------------------------------ checking

    def get(self, mandate_id: str) -> SignedMandate | None:
        st = self._state.get(mandate_id)
        return st.signed if st else None

    def state(self, mandate_id: str) -> MandateState | None:
        return self._state.get(mandate_id)

    def verify_signed(self, signed: SignedMandate) -> list[str]:
        """Check a mandate as presented by an agent: any edit after signing breaks the hash."""
        v: list[str] = []
        stored = self._state.get(signed.mandate.id)
        h = signed.mandate.hash()
        if h != signed.mandate_hash:
            v.append("mandate content does not match its signed hash")
        if not verify(self._key.public_b64, signed.mandate_hash.encode(), signed.kernel_sig):
            v.append("invalid kernel signature")
        if signed.mandate.parent_id is None and signed.device_sig is not None:
            if not verify_device(signed.device_alg or "", signed.device_key or "",
                                 mandate_signing_payload(signed.mandate_hash), signed.device_sig):
                v.append("invalid client device signature")
        if stored is None or stored.signed.mandate_hash != signed.mandate_hash:
            v.append("mandate not issued by the kernel")
        return v

    def check(self, mandate_id: str, action: Action, *, payee: str | None = None, amount: int | None = None,
              funding_source: str | None = None, domain: str | None = None) -> list[str]:
        """Violations if this action is not inside the mandate and all its ancestors."""
        st = self._state.get(mandate_id)
        if st is None:
            return ["unknown mandate"]
        v: list[str] = []
        now = self._clock.now()
        node: MandateState | None = st
        while node is not None:
            m = node.signed.mandate
            if node.revoked:
                v.append(f"mandate {m.id} revoked")
            if not (m.nbf <= now <= m.exp):
                v.append(f"mandate {m.id} expired or not yet valid")
            v += self._check_one(node, action, payee, amount, funding_source, domain, now)
            node = self._state.get(m.parent_id) if m.parent_id else None
        return v

    def _check_one(self, st: MandateState, action: Action, payee, amount, funding_source, domain,
                   now: datetime) -> list[str]:
        m = st.signed.mandate
        v: list[str] = []
        if action not in m.actions:
            v.append(f"action {action.value} not allowed")
        if m.funding_source and funding_source is not None and funding_source != m.funding_source:
            v.append("funding source not allowed")
        if domain is not None:
            allowed = {o.domain: o.mode for o in m.allowed_origins}
            if domain not in allowed:
                v.append(f"domain {domain} not in the task's origin set")
            elif action != Action.BROWSE and allowed[domain] == OriginMode.READ:
                v.append(f"domain {domain} is read-only for this task")
        p = m.payment
        if p is not None and (amount is not None or action in MONEY_ACTIONS):
            if payee not in p.allowed_payees:
                v.append("payee not allowed")
            if amount is None or not (p.amount_range.min <= amount <= p.amount_range.max):
                v.append("amount outside allowed range")
            if p.budget and amount is not None and st.spent + amount > p.budget.total:
                v.append("budget exhausted")
            r = p.agent_recurrence
            if r.max_occurrences is not None and len(st.uses) >= r.max_occurrences:
                v.append("no occurrences left")
            if any(_same_period(u.at, now, r.frequency) for u in st.uses) and r.frequency != Frequency.ONCE:
                v.append(f"already used this {r.frequency.value} period")
        if m.single_use and st.uses:
            v.append("single-use mandate already used")
        return v

    # ------------------------------------------------------------------ grants

    def grant(self, mandate_id: str, holder: str, action: Action, *, payee: str | None = None,
              amount: int | None = None, funding_source: str | None = None, domain: str | None = None,
              ttl: timedelta = timedelta(minutes=10)) -> Grant:
        if v := self.check(mandate_id, action, payee=payee, amount=amount, funding_source=funding_source,
                           domain=domain):
            raise MandateError("; ".join(v))
        exp = min(self._clock.now() + ttl, self._state[mandate_id].signed.mandate.exp)
        g = Grant(mandate_id=mandate_id, holder=holder, action=action, payee=payee, amount=amount,
                  funding_source=funding_source, domain=domain, exp=exp)
        g.kernel_sig = self._key.sign_obj(g.body())
        self._grants[g.id] = g
        return g

    def consume(self, grant: Grant, holder: str, action: Action, *, payee: str | None = None,
                amount: int | None = None, funding_source: str | None = None, domain: str | None = None) -> None:
        """Called by an executor right before acting. A grant works once, for exactly one action."""
        if not verify(self._key.public_b64, canonical(grant.body()), grant.kernel_sig) or \
                self._grants.get(grant.id) != grant:
            raise MandateError("grant not issued by the kernel or modified")
        if grant.id in self._used_grants:
            raise MandateError("grant already used")
        if self._clock.now() > grant.exp:
            raise MandateError("grant expired")
        if grant.holder != holder:
            raise MandateError("grant belongs to another agent")
        if (grant.action, grant.payee, grant.amount, grant.funding_source, grant.domain) != \
                (action, payee, amount, funding_source, domain):
            raise MandateError("action differs from the grant")
        if v := self.check(grant.mandate_id, action, payee=payee, amount=amount, funding_source=funding_source,
                           domain=domain):
            raise MandateError("; ".join(v))
        self._used_grants.add(grant.id)
        now = self._clock.now()
        node: MandateState | None = self._state[grant.mandate_id]
        while node is not None:
            node.uses.append(Use(at=now, amount=amount or 0, grant_id=grant.id))
            pid = node.signed.mandate.parent_id
            node = self._state.get(pid) if pid else None

    def active(self, client_id: str, standing_only: bool = True) -> list[SignedMandate]:
        now = self._clock.now()
        return [st.signed for st in self._state.values()
                if st.signed.mandate.client_id == client_id and not st.revoked
                and st.signed.mandate.parent_id is None and st.signed.mandate.exp >= now
                and (st.signed.mandate.standing or not standing_only)]

    def find_standing(self, client_id: str, action: Action, payee: str | None) -> SignedMandate | None:
        for sm in self.active(client_id):
            m = sm.mandate
            if action in m.actions and (m.payment is None or payee in m.payment.allowed_payees):
                return sm
        return None


def _same_period(a: datetime, b: datetime, f: Frequency) -> bool:
    if f == Frequency.DAILY:
        return a.date() == b.date()
    if f == Frequency.WEEKLY:
        return a.isocalendar()[:2] == b.isocalendar()[:2]
    if f == Frequency.MONTHLY:
        return (a.year, a.month) == (b.year, b.month)
    return True
