"""Guardian facade: the single payment pipeline every request goes through.

    request -> (PIN / duress) -> (agent token) -> PBM policy -> risk-priced autonomy
            -> coercion signals -> shadow execution on a digital twin -> ledger -> audit

Every step is written to the signed audit log, so any decision can be replayed and
explained afterwards.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .audit import AuditLog
from .autonomy import AutonomyEngine, RiskAssessment, RiskModel
from .baiter import BaitReport, PIIFilter, ScamBaiter, load_scenarios, police_report
from .clock import SimClock
from .crypto import KeyPair
from .duress import DuressGuard, fake_reference, receipt
from .firewall import AgentFirewall, Cart, CartVerdict, HonestAgent, IntentMandate, NaiveAgent, SignedMandate, load_shop
from .intel import BLOCKABLE_KINDS
from .ledger import Bank, LedgerError
from .llm import LLM
from .models import Initiator, Payee, PaymentRequest, Rail
from .money import usd
from .policy import Blocklist, PolicyDecision, PolicyEngine
from .shield import MessageAnalysis, analyze_message

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

Status = Literal["executed", "pending_confirmation", "blocked", "rejected", "declined"]


class PaymentOutcome(BaseModel):
    request_id: str
    status: Status
    tx_id: str | None = None
    envelope: str | None = None
    policy: PolicyDecision | None = None
    risk: RiskAssessment | None = None
    explanation: str
    receipt: dict | None = None
    signals: list[str] = []
    pending_id: str | None = None


class ShoppingResult(BaseModel):
    agent_id: str
    mandate: SignedMandate
    cart: Cart
    verdict: CartVerdict
    payment: PaymentOutcome | None = None
    alternative: Cart | None = None
    agent_trust: float


class Guardian:
    def __init__(self, bank: Bank, duress: DuressGuard, owner_key: KeyPair, guardian_key: KeyPair,
                 llm: LLM | None = None, risk_model: RiskModel | None = None, owner_secrets: list[str] | None = None,
                 location: str = "unknown"):
        self.bank = bank
        self.clock: SimClock = bank.clock
        self.duress = duress
        self.owner_key = owner_key
        self.key = guardian_key
        self.llm = llm or LLM()
        self.audit = AuditLog(guardian_key, self.clock)
        self.blocklist = Blocklist()
        self.policy = PolicyEngine(bank, self.blocklist)
        self.autonomy = AutonomyEngine(risk_model or RiskModel())
        self.firewall = AgentFirewall(guardian_key, self.clock, self.llm)
        self.baiter = ScamBaiter(PIIFilter(owner_secrets or []), self.llm)
        self.location = location
        self.pending: dict[str, PaymentRequest] = {}
        self.outbox: list[dict] = []
        self.incidents: list[dict] = []
        self.budget_plan: list[tuple[str, int]] = []
        self.bills: list[dict] = []
        self._tx_category: dict[str, str] = {}
        self.audit.append("guardian", "guardian.started", {"public_key": self.audit.public_key,
                                                           "risk_model": self.autonomy.model.report,
                                                           "llm": self.llm.available})

    # ------------------------------------------------------------------ payments
    def request_payment(self, req: PaymentRequest, pin: str | None = None) -> PaymentOutcome:
        self.audit.append(req.initiator.value, "payment.requested", _req_log(req))
        if pin is not None:
            mode = self.duress.check_pin(pin)
            self.audit.append("guardian", "auth.pin_checked", {"request_id": req.id,
                                                                "result": "ok" if mode != "invalid" else "invalid"})
            if mode in ("invalid", "locked"):
                why = "Wrong PIN." if mode == "invalid" else "Too many wrong PINs: payments are locked."
                return self._finish(req, PaymentOutcome(request_id=req.id, status="rejected", explanation=why))
            if mode == "duress":
                return self._duress_payment(req)

        if req.initiator == Initiator.EXTERNAL_AGENT:
            ok, why = self.firewall.redeem(req.mandate_token, req.payee.account, req.amount)
            self.audit.append("guardian", "agent.token_checked", {"request_id": req.id, "ok": ok, "reason": why})
            if not ok:
                return self._finish(req, PaymentOutcome(request_id=req.id, status="blocked",
                                                        explanation=f"Blocked: {why}."))

        decision = self.policy.evaluate(req)
        payee, is_new = self.policy.resolve_payee(req.payee)
        risk = self.autonomy.assess(req, payee, is_new, decision, self.bank)
        self.audit.append("guardian", "payment.assessed", {
            "request_id": req.id, "policy_allowed": decision.allowed, "envelope": decision.envelope_name,
            "blocked_by": decision.blocked_by, "p_error": risk.p_error, "expected_loss": risk.expected_loss,
            "el_limit": risk.el_limit, "decision": risk.decision})

        signals = self.duress.coercion_signals(req, self.bank)
        if signals and risk.decision == "auto":
            risk.decision = "confirm"
            risk.reasons.append("Possible coercion: " + "; ".join(signals) + ".")

        if risk.decision == "block":
            outcome = PaymentOutcome(request_id=req.id, status="blocked", policy=decision, risk=risk, signals=signals,
                                     explanation=self._explain("blocked", req, decision, risk))
            self._alert_owner(f"Guardian blocked a payment of {usd(req.amount)} to {req.payee.name}.", req)
            return self._finish(req, outcome)

        if risk.decision == "confirm":
            self.pending[req.id] = req
            outcome = PaymentOutcome(request_id=req.id, status="pending_confirmation", policy=decision, risk=risk,
                                     signals=signals, pending_id=req.id, envelope=decision.envelope_name,
                                     explanation=self._explain("confirm", req, decision, risk))
            if req.initiator != Initiator.USER:
                self._alert_owner(f"Approval needed: {usd(req.amount)} to {req.payee.name}.", req)
            return self._finish(req, outcome)

        return self._execute(req, decision, risk, signals)

    def _execute(self, req: PaymentRequest, decision: PolicyDecision, risk: RiskAssessment | None,
                 signals: list[str]) -> PaymentOutcome:
        violations = self._shadow_run(req, decision.envelope_id)
        self.audit.append("guardian", "shadow.executed", {"request_id": req.id, "violations": violations})
        if violations:
            return self._finish(req, PaymentOutcome(request_id=req.id, status="blocked", policy=decision, risk=risk,
                                                    explanation="Blocked: shadow execution violated invariants: "
                                                    + "; ".join(violations)))
        # Use the bank's own payee record; a request can never register itself as "verified".
        payee, _ = self.policy.resolve_payee(req.payee)
        tx = self.bank.pay(decision.envelope_id, payee, req.amount, req.rail, req.memo, req.initiator, ref=req.id)
        self._tx_category[tx.id] = req.category
        if req.initiator in (Initiator.GUARDIAN, Initiator.EXTERNAL_AGENT):
            self.autonomy.record_outcome(req.category, reverted=False)
        self.audit.append("guardian", "payment.executed", {"request_id": req.id, "tx_id": tx.id,
                                                            "envelope": decision.envelope_name, "amount": req.amount,
                                                            "payee": req.payee.name, "rail": req.rail.value})
        rec = receipt(req, tx.id.upper().replace("TX_", "TRF"), tx.ts.isoformat(), self.available_balance())
        return self._finish(req, PaymentOutcome(request_id=req.id, status="executed", tx_id=tx.id,
                                                envelope=decision.envelope_name, policy=decision, risk=risk,
                                                signals=signals, receipt=rec,
                                                explanation=self._explain("executed", req, decision, risk)))

    def _shadow_run(self, req: PaymentRequest, envelope_id: str | None) -> list[str]:
        """Execute on a deep-copied twin first and check ledger invariants."""
        twin = self.bank.twin()
        before_total = twin.total
        before = {e.id: e.balance for e in twin.envelopes.values()}
        try:
            twin.pay(envelope_id, req.payee, req.amount, req.rail, req.memo, req.initiator)
        except LedgerError as e:
            return [str(e)]
        v = []
        if twin.total != before_total - req.amount:
            v.append("total did not decrease by exactly the amount")
        if any(e.balance < 0 for e in twin.envelopes.values()):
            v.append("negative envelope balance")
        changed = [i for i, e in twin.envelopes.items() if e.balance != before[i]]
        if changed != [envelope_id]:
            v.append("more than one envelope changed")
        if twin.quarantine != self.bank.quarantine:
            v.append("quarantine changed")
        return v

    def _duress_payment(self, req: PaymentRequest) -> PaymentOutcome:
        """Show a perfect-looking success to the coercer while nothing leaves the bank."""
        ref = fake_reference()
        remaining = req.amount
        quarantined = []
        for env in sorted(self.bank.envelopes.values(), key=lambda e: e.protected):
            if remaining <= 0:
                break
            if env.balance > 0:
                t = self.bank.quarantine_payment(env.id, req.payee, min(remaining, env.balance), req.rail,
                                                 req.memo, req.initiator, ref=ref)
                remaining -= t.amount
                quarantined.append(t.id)
        alerts = self.duress.alerts_for(req, self.location)
        for a in alerts:
            self.outbox.append({"ts": self.clock.now().isoformat(), "kind": "duress", **a})
        incident = {"ts": self.clock.now().isoformat(), "request_id": req.id, "decoy_reference": ref,
                    "amount": req.amount, "payee": req.payee.name, "payee_account": req.payee.account,
                    "quarantine_tx": quarantined, "alerts_sent": len(alerts), "location": self.location}
        self.incidents.append(incident)
        self.blocklist.add(req.payee.account, "account", "duress incident")
        self.audit.append("guardian", "duress.activated", incident)
        rec = receipt(req, ref, self.clock.now().isoformat(), self.available_balance())
        return PaymentOutcome(request_id=req.id, status="executed", tx_id=ref.lower(), receipt=rec,
                              explanation="Transfer sent.")

    def resolve_incident(self, index: int = -1) -> dict:
        """Owner, logged in with the normal PIN, releases frozen funds after a duress incident."""
        inc = self.incidents[index]
        released = 0
        for tx_id in inc["quarantine_tx"]:
            released += self.bank.release_quarantine(tx_id).amount
        inc["resolved"] = True
        self.audit.append("user", "duress.resolved", {"request_id": inc["request_id"], "released": released})
        return {"released": released, "incident": inc}

    def confirm(self, pending_id: str, approve: bool, pin: str | None = None) -> PaymentOutcome:
        req = self.pending.pop(pending_id, None)
        if req is None:
            return PaymentOutcome(request_id=pending_id, status="rejected", explanation="No such pending payment.")
        if not approve:
            self.audit.append("user", "payment.declined", {"request_id": req.id})
            return PaymentOutcome(request_id=req.id, status="declined", explanation="You declined this payment.")
        if pin is not None:
            mode = self.duress.check_pin(pin)
            if mode in ("invalid", "locked"):
                self.pending[pending_id] = req
                return PaymentOutcome(request_id=req.id, status="rejected", explanation="Wrong PIN.")
            if mode == "duress":
                return self._duress_payment(req)
        decision = self.policy.evaluate(req)  # state may have changed since the request was parked
        self.audit.append("user", "payment.approved", {"request_id": req.id, "policy_allowed": decision.allowed})
        if not decision.allowed:
            return self._finish(req, PaymentOutcome(request_id=req.id, status="blocked", policy=decision,
                                                    explanation=decision.summary))
        return self._execute(req, decision, None, [])

    def revert(self, tx_id: str) -> dict:
        t = self.bank.reverse(tx_id)
        orig = self.bank.tx(tx_id)
        trust = None
        if orig.initiator in (Initiator.GUARDIAN, Initiator.EXTERNAL_AGENT):
            trust = self.autonomy.record_outcome(self._tx_category.get(tx_id, "other"), reverted=True)
        self.audit.append("user", "payment.reverted", {"tx_id": tx_id, "category": self._tx_category.get(tx_id),
                                                        "new_trust": trust})
        return {"reversal_tx": t.id, "category": self._tx_category.get(tx_id), "new_trust": trust}

    # ------------------------------------------------------------------ budgeting
    def receive_income(self, amount: int, source: str) -> list[dict]:
        self.bank.deposit(amount, source)
        self.audit.append("bank", "income.received", {"amount": amount, "source": source})
        done = []
        for env_id, target in self.budget_plan:
            # Top up each envelope to its monthly target, in priority order.
            share = min(max(0, target - self.bank.env(env_id).balance), self.bank.unallocated)
            if share <= 0:
                continue
            self.bank.allocate(env_id, share)
            done.append({"envelope": self.bank.env(env_id).name, "amount": share})
        self.audit.append("guardian", "budget.allocated", {"allocations": done})
        return done

    def run_due_bills(self) -> list[PaymentOutcome]:
        out = []
        today = self.clock.now()
        for bill in self.bills:
            paid_key = (today.year, today.month)
            if bill.get("_paid") == paid_key or today.day < bill["day"]:
                continue
            payee = self.bank.payees[bill["payee_id"]]
            req = PaymentRequest(payee=payee, amount=bill["amount"], rail=Rail(bill["rail"]), category=bill["category"],
                                 memo=bill["memo"], initiator=Initiator.GUARDIAN, envelope_hint=bill["envelope"])
            res = self.request_payment(req)
            if res.status in ("executed", "pending_confirmation"):
                bill["_paid"] = paid_key
            out.append(res)
        return out

    # ------------------------------------------------------------------ scam shield and baiter
    def analyze_message(self, text: str, sender: str) -> MessageAnalysis:
        a = analyze_message(text, sender, self.blocklist, self.llm, known_payees=list(self.bank.payees.values()))
        self.audit.append("guardian", "message.analyzed", {"sender": sender, "verdict": a.verdict, "score": a.score,
                                                            "indicators": [i.value for i in a.indicators]})
        return a

    def handle_message(self, text: str, sender: str) -> tuple[MessageAnalysis, PaymentOutcome | None]:
        a = self.analyze_message(text, sender)
        if a.payment_request is None:
            return a, None
        return a, self.request_payment(a.payment_request)

    def bait(self, scenario_id: str) -> tuple[BaitReport, list[str]]:
        scenario = load_scenarios(DATA_DIR / "scam_scripts.json")[scenario_id]
        report = self.baiter.run(scenario)
        added = []
        # A scammer could plant a legitimate account (e.g. the real landlord's) to get it blocked.
        verified = {self.blocklist.norm(p.account.removeprefix("payid:")) for p in self.bank.payees.values() if p.verified}
        for i in report.indicators:
            if self.blocklist.norm(i.value) in verified:
                self.audit.append("guardian", "baiter.poisoning_ignored", {"indicator": i.value})
                continue
            if i.kind in BLOCKABLE_KINDS and i.valid and not self.blocklist.hit(i.value):
                self.blocklist.add(i.value if i.kind != "email" else f"payid:{i.value}", i.kind,
                                   f"scam baiter: {scenario['title']}")
                if i.kind == "email":
                    self.blocklist.add(i.value, i.kind, f"scam baiter: {scenario['title']}")
                added.append(i.value)
        self.audit.append("guardian", "baiter.session", {"scenario": scenario_id, "turns": len(report.turns),
                                                          "indicators": added,
                                                          "pii_leaks_blocked": report.pii_leaks_blocked})
        return report, added

    def police_report(self, report: BaitReport) -> dict:
        return police_report(report)

    # ------------------------------------------------------------------ agent shopping
    def create_mandate(self, agent_id: str, description: str, keywords: list[str], categories: list[str],
                       max_total: int, hours: int = 24) -> SignedMandate:
        m = IntentMandate(owner=self.bank.owner, agent_id=agent_id, description=description, keywords=keywords,
                          allowed_categories=categories, max_total=max_total,
                          expires_at=self.clock.now() + timedelta(hours=hours))
        signed = self.firewall.sign_mandate(m, self.owner_key)
        self.audit.append("user", "mandate.signed", {"mandate_id": m.mandate_id, "agent": agent_id,
                                                      "description": description, "max_total": max_total})
        return signed

    def agent_shop(self, agent_kind: str, signed: SignedMandate) -> ShoppingResult:
        shop = load_shop(DATA_DIR / "shop")
        agent = NaiveAgent() if agent_kind == "naive" else HonestAgent()
        cart = agent.propose(signed.mandate, shop)
        verdict = self.firewall.verify_cart(signed, cart, shop)
        self.audit.append("guardian", "agent.cart_verified", {
            "agent": cart.agent_id, "cart_total": cart.total, "items": [i.sku for i in cart.items],
            "approved": verdict.approved, "failed_checks": [c.name for c in verdict.checks if not c.ok and c.hard],
            "hidden_instructions": len([f for f in verdict.findings if f.severity == "high"])})
        payment = alternative = None
        if verdict.approved:
            token = self.firewall.issue_token(signed, cart)
            self.audit.append("guardian", "agent.token_issued", {"token": token.token_id, "merchant": token.merchant_account,
                                                                  "max_amount": token.max_amount})
            item = cart.items[0]
            payee = self.bank.find_payee_by_account(item.seller_account)
            req = PaymentRequest(payee=payee or Payee(name=item.seller, account=item.seller_account, category="shopping"),
                                 amount=cart.total, rail=Rail.CARD, category=item.category,
                                 memo=f"{cart.agent_id}: {', '.join(i.name for i in cart.items)}",
                                 initiator=Initiator.EXTERNAL_AGENT, mandate_token=token.token_id)
            payment = self.request_payment(req)
        else:
            alternative = self.firewall.clean_alternative(signed.mandate, shop)
            self._alert_owner(f"Shopping agent {cart.agent_id} proposed a cart outside your mandate; it was blocked.", None)
        return ShoppingResult(agent_id=cart.agent_id, mandate=signed, cart=cart, verdict=verdict, payment=payment,
                              alternative=alternative, agent_trust=self.firewall.agent_trust[cart.agent_id])

    # ------------------------------------------------------------------ views
    def available_balance(self) -> int:
        """Money the owner can spend. Quarantined funds are excluded, so after a duress transfer
        the coercer sees the balance drop exactly as if the money had left."""
        return self.bank.total - self.bank.quarantine

    def account_view(self, decoy: bool = False) -> dict:
        snap = self.bank.snapshot()
        if decoy:
            # What a coercer sees after a duress transfer: the money looks gone, quarantine is hidden.
            snap["total"] = self.available_balance()
            snap.pop("quarantine")
        return snap

    def audit_verify(self):
        return self.audit.verify()

    # ------------------------------------------------------------------ helpers
    def _alert_owner(self, message: str, req: PaymentRequest | None) -> None:
        self.outbox.append({"ts": self.clock.now().isoformat(), "kind": "owner", "to": "Mia (app notification)",
                            "channel": "push", "message": message, "request_id": req.id if req else None})

    def _finish(self, req: PaymentRequest, outcome: PaymentOutcome) -> PaymentOutcome:
        self.audit.append("guardian", f"payment.{outcome.status}", {
            "request_id": req.id, "explanation": outcome.explanation, "tx_id": outcome.tx_id})
        return outcome

    def _explain(self, kind: str, req: PaymentRequest, decision: PolicyDecision, risk: RiskAssessment | None) -> str:
        who = {"user": "you", "guardian": "Guardian", "external_agent": "a shopping agent",
               "inbound_request": "someone else"}[req.initiator.value]
        head = f"{usd(req.amount)} to {req.payee.name}, requested by {who}. "
        if kind == "blocked":
            if decision.blocked_by == "no_envelope":
                # Show the envelopes that came closest to allowing it: they explain the real reason.
                closest = sorted(decision.checks, key=lambda c: (any("allow-list" in r for r in c.reasons),
                                                                 len(c.reasons)))[:2]
                main = [f"{c.envelope_name}: {', '.join(c.reasons)}" for c in closest]
                return head + decision.summary + " Closest: " + "; ".join(main) + "."
            return head + " ".join(risk.reasons)
        if kind == "confirm":
            return head + "Needs your approval: " + " ".join(risk.reasons)
        tail = " ".join(risk.reasons) if risk else "Approved by you."
        return head + f"Paid from '{decision.envelope_name}'. " + tail


def _req_log(req: PaymentRequest) -> dict:
    return {"request_id": req.id, "payee": req.payee.name, "account": req.payee.account, "amount": req.amount,
            "rail": req.rail.value, "category": req.category, "initiator": req.initiator.value,
            "has_source_message": bool(req.source_message)}
