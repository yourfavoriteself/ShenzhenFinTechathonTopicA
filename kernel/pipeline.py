"""Decision pipeline. Every step can only tighten the decision: allow -> ask -> deny.

The final "allow" is always deterministic code. LLM-based checks (critic, counterfactual) plug in
as steps and can only say "no" or "ask the client".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from .cards import ConfirmationCard, Risk, render
from .intent import Intent
from .mandate import (MONEY_ACTIONS, SPEND_ACTIONS, Action, AmountRange, AutonomyStage, Budget, Frequency, Mandate, MandateError,
                      OriginMode, OriginRule, PaymentConstraints, Recurrence, SignedMandate)
from .money import from_rub
from .passport import Passport, Role
from .ports import PayeeInfo, Receipt
from .trust import AgentStatus, Category, TrustEvent, category_for

if TYPE_CHECKING:
    from .core import Kernel


class Verdict(IntEnum):
    ALLOW = 0
    ASK = 1
    DENY = 2


class StepResult(BaseModel):
    step: str
    verdict: Verdict = Verdict.ALLOW
    risk: Risk = Risk.LOW
    reasons: list[str] = []
    data: dict[str, Any] = {}


@dataclass
class Ctx:
    intent: Intent
    passport: Passport
    confirmed: bool = False
    mandate: SignedMandate | None = None
    payee: PayeeInfo | None = None
    category: Category = Category.EXTERNAL_PAYMENTS
    verdict: Verdict = Verdict.ALLOW
    risk: Risk = Risk.LOW
    reasons: list[str] = field(default_factory=list)
    results: list[StepResult] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    def apply(self, r: StepResult) -> None:
        self.results.append(r)
        self.verdict = max(self.verdict, r.verdict)
        self.risk = max(self.risk, r.risk)
        self.reasons += [x for x in r.reasons if x not in self.reasons]


class Outcome(str):
    EXECUTED = "executed"
    PENDING = "pending_confirmation"
    DENIED = "denied"
    FAILED = "failed"
    HELD = "held"


class Decision(BaseModel):
    outcome: str
    intent_id: str
    verdict: Verdict
    risk: Risk
    reasons: list[str] = []
    steps: list[StepResult] = []
    card: ConfirmationCard | None = None
    pending_id: str | None = None
    receipt: Receipt | None = None
    notify_only: bool = False
    stage: int | None = None
    hold: dict | None = None


class PendingConfirmation(BaseModel):
    id: str
    intent: Intent
    passport_id: str
    card: ConfirmationCard
    once: Mandate
    standing: Mandate | None = None


REQUIRED: dict[Action, tuple[str, ...]] = {
    Action.PAY: ("payee", "amount", "funding_source"),
    Action.TRANSFER_OWN: ("payee", "amount", "funding_source"),
    Action.CANCEL_SUBSCRIPTION: ("payee",),
    Action.BLOCK_MERCHANT: ("payee",),
    Action.DISPUTE: ("payee", "amount"),
    Action.BROWSE: ("domain",),
    Action.INVEST: ("payee", "amount"),
}
UNTRUSTED_DENY_AMOUNT = from_rub(10_000)
UNUSUAL_FACTOR = 1.5
STANDING_TOLERANCE = 1.15
STANDING_KINDS = {"subscription", "biller", "own", "security"}

Step = Callable[[Ctx], StepResult]


class Pipeline:
    def __init__(self, kernel: "Kernel"):
        self.k = kernel
        self.risk_checks: list[Step] = []
        self.critic: Step | None = None
        self.counterfactual: Step | None = None
        self.shadow: Step | None = None
        self.verifier: Callable[[Intent, Receipt], bool] | None = None

    def steps(self) -> list[tuple[str, Step]]:
        return [("agent", self.s_agent), ("schema", self.s_schema), ("mandate", self.s_mandate),
                ("provenance", self.s_provenance), ("references", self.s_references), ("policy", self.s_policy),
                ("broker", self.s_broker), ("risk", self.s_risk), ("critic", self.s_critic), ("shadow", self.s_shadow)]

    # ------------------------------------------------------------------ run

    def run(self, intent: Intent, passport: Passport, confirmed: bool = False) -> Decision:
        ctx = Ctx(intent=intent, passport=passport, confirmed=confirmed)
        if self.k.halted:
            ctx.apply(StepResult(step="kill_switch", verdict=Verdict.DENY, reasons=[f"kernel halted: {self.k.halted}"]))
            return self._finish(ctx, Outcome.DENIED)
        payee_token = intent.val("payee")
        directory = self.k.investing if intent.action == Action.INVEST and self.k.investing else self.k.directory
        ctx.payee = directory.payee(intent.client_id, payee_token) if isinstance(payee_token, str) else None
        ctx.category = category_for(intent.action, ctx.payee.kind if ctx.payee else None)
        for name, step in self.steps():
            r = step(ctx)
            r.step = name
            ctx.apply(r)
            self._journal(ctx, f"pipeline.{name}", r.model_dump(mode="json", exclude={"step"}))
            if ctx.verdict == Verdict.DENY:
                return self._finish(ctx, Outcome.DENIED)
        if ctx.verdict == Verdict.ASK and not confirmed:
            return self._ask(ctx)
        return self._execute(ctx)

    # ------------------------------------------------------------------ steps

    def s_agent(self, ctx: Ctx) -> StepResult:
        p = ctx.passport
        reasons = []
        try:
            self.k.passports.check_passport(p)
        except Exception as e:  # noqa: BLE001
            reasons.append(f"agent passport: {e}")
        if p.role not in (Role.PLANNER, Role.DISPATCHER):
            reasons.append(f"role {p.role.value} may not propose actions")
        if p.client_id != ctx.intent.client_id:
            reasons.append("agent works for another client")
        return StepResult(step="", verdict=Verdict.DENY if reasons else Verdict.ALLOW, reasons=reasons,
                          data={"passport": p.id, "role": p.role.value, "model": p.model, "level": p.model_level})

    def s_schema(self, ctx: Ctx) -> StepResult:
        i = ctx.intent
        reasons = [f"missing {f}" for f in REQUIRED[i.action] if i.val(f) in (None, "")]
        amt = i.val("amount")
        if amt is not None and (not isinstance(amt, int) or isinstance(amt, bool) or amt <= 0):
            reasons.append("amount must be a positive integer in cents")
        for f in ("payee", "funding_source", "domain"):
            if i.val(f) is not None and not isinstance(i.val(f), str):
                reasons.append(f"{f} must be a string")
        return StepResult(step="", verdict=Verdict.DENY if reasons else Verdict.ALLOW, reasons=reasons)

    def s_mandate(self, ctx: Ctx) -> StepResult:
        i, k = ctx.intent, self.k
        args = dict(payee=i.val("payee"), amount=i.val("amount"), funding_source=i.val("funding_source"),
                    domain=i.val("domain"))
        if i.mandate_id:
            sm = k.mandates.get(i.mandate_id)
            v = ["unknown mandate"] if sm is None else k.mandates.check(i.mandate_id, i.action, **args)
            if sm is not None and sm.mandate.client_id != i.client_id:
                v.append("mandate belongs to another client")
            if v:
                k.trust.record(ctx.category, TrustEvent.SCOPE_VIOLATION, ctx.passport, v[0])
                return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                                  reasons=[f"outside the mandate: {x}" for x in v])
            ctx.mandate = sm
        else:
            sm = k.mandates.find_standing(i.client_id, i.action, args["payee"])
            v = k.mandates.check(sm.mandate.id, i.action, **args) if sm else ["no mandate covers this action"]
            if v:
                return StepResult(step="", verdict=Verdict.ASK, risk=Risk.MEDIUM, reasons=v)
            ctx.mandate = sm
        m = ctx.mandate.mandate
        if m.standing and not ctx.confirmed and m.payment and args["amount"] is not None:
            limit = k.trust.auto_limit(ctx.category, m.payment.amount_range.max, ctx.passport)
            if args["amount"] > limit:
                rec = k.trust.agent(ctx.passport)
                why = (f"the {rec.role} agent is {rec.status.value}" if rec.status != AgentStatus.ACTIVE else
                       f"trust level {k.trust.level(ctx.category)} for {ctx.category.value}")
                return StepResult(step="", verdict=Verdict.ASK, risk=Risk.MEDIUM,
                                  reasons=[f"{why} does not allow acting alone on this amount"])
        return StepResult(step="", data={"mandate": m.id, "stage": int(m.stage)})

    def s_provenance(self, ctx: Ctx) -> StepResult:
        i = ctx.intent
        bad = {n: f for n, f in i.fields().items() if not f.trusted}
        if not bad:
            return StepResult(step="")
        reasons = [f"{n} comes from an unverified source ({f.source})" for n, f in bad.items()]
        new_payee = ctx.payee is None or not (ctx.payee.verified or ctx.payee.known_to_client)
        amt = i.val("amount") or 0
        if "payee" in bad and new_payee and amt >= UNTRUSTED_DENY_AMOUNT:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=reasons + ["large payment to an unverified new payee"])
        if "funding_source" in bad:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=reasons + ["the source of money must come from the bank or the client"])
        return StepResult(step="", verdict=Verdict.ASK, risk=Risk.HIGH, reasons=reasons)

    def s_references(self, ctx: Ctx) -> StepResult:
        missing = [r for r in ctx.intent.references if not self.k.provenance.exists(r)]
        if missing:
            self.k.trust.record(ctx.category, TrustEvent.FABRICATED_NUMBER, ctx.passport,
                                missing[0])
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=[f"reference to data that does not exist: {r}" for r in missing])
        return StepResult(step="")

    def s_policy(self, ctx: Ctx) -> StepResult:
        i, p = ctx.intent, ctx.payee
        token, amt, src = i.val("payee"), i.val("amount"), i.val("funding_source")
        deny, ask, high = [], [], False
        if isinstance(token, str) and self.k.directory.is_blocked(token):
            deny.append("payee is on the bank's blocklist")
        if i.action in MONEY_ACTIONS | {Action.CANCEL_SUBSCRIPTION, Action.BLOCK_MERCHANT} and p is None:
            ask.append("payee is not in the bank's directory")
            high = True
        if p is not None:
            if i.action in MONEY_ACTIONS and not p.known_to_client:
                ask.append("first payment to this payee")
                high = True
                if self.k.trust.elevated_protection:
                    ctx.flags.add("notify_trusted_person")
            if p.details_changed:
                ask.append("payee details changed since the last payment")
                high = True
            if p.usual_amount and amt and i.action in MONEY_ACTIONS and amt > p.usual_amount * UNUSUAL_FACTOR:
                ask.append("amount is well above the usual amount for this payee")
                high = True
            dom = i.val("domain")
            if dom and p.domain and dom != p.domain:
                deny.append(f"website {dom} is not the official domain {p.domain}")
        if src and amt and i.action in MONEY_ACTIONS:
            avail = self.k.funds.available(i.client_id, src)
            if avail is not None and amt > avail:
                ask.append(f"not enough money in {self.k.funds.label(i.client_id, src)}")
                high = True
        if deny:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH, reasons=deny)
        if ask:
            return StepResult(step="", verdict=Verdict.ASK, risk=Risk.HIGH if high else Risk.MEDIUM, reasons=ask)
        return StepResult(step="", risk=Risk.LOW if ctx.mandate else Risk.MEDIUM)

    def s_broker(self, ctx: Ctx) -> StepResult:
        if ctx.intent.action != Action.INVEST:
            return StepResult(step="")
        if self.k.investing is None:
            return StepResult(step="", verdict=Verdict.DENY, reasons=["no broker is connected"])
        return self.k.investing.check(ctx)

    def s_risk(self, ctx: Ctx) -> StepResult:
        return _merge([chk(ctx) for chk in self.risk_checks])

    def s_critic(self, ctx: Ctx) -> StepResult:
        results = []
        if self.critic and ctx.intent.action != Action.BROWSE:
            r = self.critic(ctx)
            if r.verdict != Verdict.ALLOW:
                self.k.trust.record(ctx.category, TrustEvent.CRITIC_DENY, ctx.passport,
                                    r.reasons[0] if r.reasons else "")
            results.append(r)
        if self.counterfactual and ctx.risk == Risk.HIGH:
            results.append(self.counterfactual(ctx))
        return _merge(results)

    def s_shadow(self, ctx: Ctx) -> StepResult:
        return self.shadow(ctx) if self.shadow else StepResult(step="")

    # ------------------------------------------------------------------ outcomes

    def _ask(self, ctx: Ctx) -> Decision:
        i, k = ctx.intent, self.k
        once, standing = self._drafts(ctx)
        label = k.funds.label(i.client_id, i.val("funding_source")) if i.val("funding_source") else "-"
        if i.action == Action.INVEST and k.investing:
            label = k.investing.label
        previous = {"amount": ctx.payee.usual_amount} if ctx.payee and ctx.payee.usual_amount else None
        card = render(once, i, ctx.payee, label, ctx.risk, ctx.reasons, previous=previous, standing=standing)
        a = k.intervention.assessments.get(i.id) if k.intervention else None
        if a is not None and a.level:
            card.intervention, card.type_amount, card.hold_to_confirm = a.card(), True, True
        ref = card.content_hash()
        for m in filter(None, (once, standing)):
            if m.payment:
                m.payment.reference = ref
        card.mandate_hash = once.hash()
        card.standing_mandate_hash = standing.hash() if standing else None
        pending = PendingConfirmation(id=card.id, intent=i, passport_id=ctx.passport.id, card=card, once=once,
                                      standing=standing)
        k.pending[pending.id] = pending
        self._journal(ctx, "card.shown", {"card": card.id, "risk": int(ctx.risk), "mandate_hash": card.mandate_hash})
        return self._finish(ctx, Outcome.PENDING, card=card, pending_id=pending.id)

    def _drafts(self, ctx: Ctx) -> tuple[Mandate, Mandate | None]:
        i, now = ctx.intent, self.k.clock.now()
        payee, amount = i.val("payee"), i.val("amount")
        origins = [OriginRule(domain=i.val("domain"), mode=OriginMode.ACT)] if i.val("domain") else []
        payment = None
        if payee is not None:
            payment = PaymentConstraints(allowed_payees=[payee], amount_range=AmountRange(max=amount or 0),
                                         agent_recurrence=Recurrence())
        end_of_tomorrow = datetime.combine((now + timedelta(days=1)).date(), time(23, 59), tzinfo=now.tzinfo)
        exp = min(end_of_tomorrow, i.not_after) if i.not_after else end_of_tomorrow
        once = Mandate(client_id=i.client_id, actions=[i.action], payment=payment, allowed_origins=origins,
                       funding_source=i.val("funding_source"), nbf=now, exp=exp, single_use=True,
                       stage=AutonomyStage.ASSISTED)
        standing = None
        if (i.action in SPEND_ACTIONS and ctx.risk < Risk.HIGH and i.not_after is None and ctx.payee is not None
                and ctx.payee.kind in STANDING_KINDS and amount):
            exp = datetime(now.year, 12, 31, 23, 59, tzinfo=now.tzinfo)
            if (exp - now).days < 30:
                exp = exp.replace(year=now.year + 1)
            months = max(1, (exp.year - now.year) * 12 + exp.month - now.month + 1)
            cap = int(math.ceil(amount * STANDING_TOLERANCE / 100) * 100)
            standing = Mandate(client_id=i.client_id, actions=[i.action],
                               payment=PaymentConstraints(allowed_payees=[payee], amount_range=AmountRange(max=cap),
                                                          budget=Budget(total=cap * months),
                                                          agent_recurrence=Recurrence(frequency=Frequency.MONTHLY,
                                                                                      max_occurrences=None)),
                               allowed_origins=origins, funding_source=i.val("funding_source"), nbf=now, exp=exp,
                               standing=True, stage=AutonomyStage.PRESET_CONDITIONS)
        return once, standing

    def _execute(self, ctx: Ctx) -> Decision:
        i, k = ctx.intent, self.k
        if ctx.mandate is None:
            ctx.apply(StepResult(step="execute", verdict=Verdict.DENY, reasons=["no mandate to execute under"]))
            return self._finish(ctx, Outcome.DENIED)
        executor = k.investing if i.action == Action.INVEST and k.investing else k.executor
        if executor is None:
            ctx.apply(StepResult(step="execute", verdict=Verdict.DENY, reasons=["no executor configured"]))
            return self._finish(ctx, Outcome.FAILED)
        args = dict(payee=i.val("payee"), amount=i.val("amount"), funding_source=i.val("funding_source"),
                    domain=i.val("domain"))
        holder = f"executor:{i.action.value}"
        try:
            grant = k.mandates.grant(ctx.mandate.mandate.id, holder, i.action, **args)
            self._journal(ctx, "grant.issued", {"grant": grant.id, "mandate": grant.mandate_id, **args})
            k.mandates.consume(grant, holder, i.action, **args)
            receipt = executor.execute(grant, i)
        except MandateError as e:
            ctx.apply(StepResult(step="execute", verdict=Verdict.DENY, reasons=[str(e)]))
            return self._finish(ctx, Outcome.DENIED)
        self._journal(ctx, "executed", {"ok": receipt.ok, "reference": receipt.reference})
        if not receipt.ok:
            ctx.reasons.append(str(receipt.details.get("error") or "the executor reported a failure"))
            return self._finish(ctx, Outcome.FAILED, receipt=receipt)
        verifier = k.investing.verify if i.action == Action.INVEST and k.investing else self.verifier
        verified = verifier(i, receipt) if verifier else True
        k.trust.record(ctx.category, TrustEvent.VERIFIED_SUCCESS if verified else TrustEvent.KERNEL_DENY, ctx.passport)
        self._journal(ctx, "result.verified", {"ok": verified})
        if not verified:
            ctx.reasons.append("the kernel could not confirm the result")
            return self._finish(ctx, Outcome.FAILED, receipt=receipt)
        notify = ctx.mandate.mandate.standing and not ctx.confirmed
        return self._finish(ctx, Outcome.EXECUTED, receipt=receipt, notify_only=notify)

    def _finish(self, ctx: Ctx, outcome: str, **kw) -> Decision:
        stage = int(ctx.mandate.mandate.stage) if ctx.mandate else None
        d = Decision(outcome=outcome, intent_id=ctx.intent.id, verdict=ctx.verdict, risk=ctx.risk,
                     reasons=ctx.reasons, steps=ctx.results, stage=stage, **kw)
        self._journal(ctx, f"decision.{outcome}", {**ctx.intent.summary(), "reasons": ctx.reasons,
                                                  "risk": int(ctx.risk), "flags": sorted(ctx.flags)})
        return d

    def _journal(self, ctx: Ctx, event: str, data: dict) -> None:
        stage = int(ctx.mandate.mandate.stage) if ctx.mandate else None
        self.k.journal.append("kernel", event, {"intent": ctx.intent.id, **data}, stage=stage,
                              passport_id=ctx.passport.id)


def _merge(results: list[StepResult]) -> StepResult:
    out = StepResult(step="")
    for r in results:
        out.verdict = max(out.verdict, r.verdict)
        out.risk = max(out.risk, r.risk)
        out.reasons += r.reasons
        out.data.update(r.data)
    return out
