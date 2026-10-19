"""The Guardian kernel: the only component that can move money.

Agents call it with signed requests; it resolves provenance, runs the decision pipeline, renders
confirmation cards, turns the client's signed "Yes" into a mandate and executes through single-use
grants. Everything it decides goes to the signed journal.
"""

from __future__ import annotations

from typing import Any

from .cards import Risk
from .clock import SimClock
from .critic import TaskInfo
from .crypto import KeyPair
from .intent import Intent, IntentProposal, ProvenanceStore
from .journal import Journal
from .mandate import Action, MandateError, MandateService
from .passport import AuthError, Passport, PassportRegistry, SignedRequest
from .pipeline import Decision, Outcome, PendingConfirmation, Pipeline, StepResult, Verdict
from .ports import Directory, Executor, Funds, InMemoryDirectory, InMemoryFunds
from .provenance import Labeled
from .secrets import Vault
from .suspicious_memory import SuspiciousMemory
from .trust import AgentStatus, TrustChange, TrustEvent, TrustLedger, agent_key, category_for


class ConfirmationError(Exception):
    pass


class Kernel:
    def __init__(self, *, clock: SimClock | None = None, seed: str | None = "demo",
                 directory: Directory | None = None, funds: Funds | None = None,
                 executor: Executor | None = None, elevated_protection: bool = False):
        self.clock = clock or SimClock()
        self.key = KeyPair.from_seed(f"{seed}:kernel") if seed else KeyPair()
        self.journal = Journal(KeyPair.from_seed(f"{seed}:journal") if seed else KeyPair(), self.clock)
        self.mandates = MandateService(self.key, self.clock)
        self.passports = PassportRegistry(self.key, self.clock)
        self.trust = TrustLedger(self.clock, elevated_protection)
        self.trust.listeners.append(self._on_trust)
        self.provenance = ProvenanceStore()
        self.directory: Directory = directory or InMemoryDirectory()
        self.funds: Funds = funds or InMemoryFunds()
        self.executor = executor
        self.investing: Any = None          # InvestDesk when a broker is connected
        self.pipeline = Pipeline(self)
        self.vault = Vault(self.clock)
        self.memory = SuspiciousMemory()
        self.pipeline.risk_checks.append(self.memory.check)
        self.tasks: dict[str, TaskInfo] = {}
        self.pending: dict[str, PendingConfirmation] = {}
        self.intervention: Any = None
        self.halted: str | None = None

    # ------------------------------------------------------------------ tasks

    def open_task(self, client_id: str, request: str, kind: str = "") -> TaskInfo:
        """Record the client's own words for a task: provenance for values and the critic's only input text."""
        from .mandate import new_id
        task = TaskInfo(task_id=new_id("task"), client_id=client_id, request=request, kind=kind)
        self.tasks[task.task_id] = task
        self.provenance.put_client_message(task.task_id, request)
        self.journal.append("client", "task.opened", {"task": task.task_id, "kind": kind})
        return task

    # ------------------------------------------------------------------ agents

    def submit(self, req: SignedRequest, presented: Passport | None = None) -> Decision:
        """Entry point for agents: authenticate (KYA), resolve provenance, run the pipeline."""
        try:
            passport = self.passports.authenticate(req, presented)
        except AuthError as e:
            self.journal.append("kernel", "auth.rejected", {"passport": req.passport_id, "reason": str(e)},
                                passport_id=req.passport_id)
            return Decision(outcome=Outcome.DENIED, intent_id="-", verdict=Verdict.DENY, risk=2,
                            reasons=[f"agent authentication failed: {e}"],
                            steps=[StepResult(step="agent", verdict=Verdict.DENY, reasons=[str(e)])])
        proposal = IntentProposal.model_validate(req.body["intent"])
        intent = self.resolve(proposal, passport)
        self.journal.append("agent", "intent.proposed", intent.summary(), passport_id=passport.id)
        return self.pipeline.run(intent, passport)

    def resolve(self, proposal: IntentProposal, passport: Passport) -> Intent:
        fields = {name: self.provenance.resolve(claim) for name in ("payee", "amount", "funding_source", "domain")
                  if (claim := getattr(proposal, name)) is not None}
        return Intent(client_id=passport.client_id, task_id=passport.task_id, proposed_by=passport.id,
                      action=proposal.action, mandate_id=proposal.mandate_id, memo=proposal.memo,
                      not_after=proposal.not_after,
                      references=proposal.references, **fields)

    def bank_value(self, ref: str, value: Any, source: str) -> str:
        """Publish a bank-trusted value to agents under a reference they can cite."""
        from .provenance import bank
        return self.provenance.put(ref, bank(value, source))

    def publish(self, ref: str, value: Labeled) -> str:
        return self.provenance.put(ref, value)

    # ------------------------------------------------------------------ client

    def confirm(self, pending_id: str, device_alg: str, device_key: str, device_sig: str, *,
                standing: bool = False, typed_amount: int | None = None, answers: dict[str, bool] | None = None,
                pin: str | None = None) -> Decision:
        """The client pressed "Yes" on a card; the device signed the hash of the chosen mandate."""
        pending = self.pending.get(pending_id)
        if pending is None:
            raise ConfirmationError("unknown or already handled confirmation")
        mandate = pending.standing if standing else pending.once
        if mandate is None:
            raise ConfirmationError("no standing option was offered on this card")
        if mandate.exp <= self.clock.now():
            self.pending.pop(pending_id, None)
            self.journal.append("client", "confirmation.expired", {"card": pending_id})
            raise ConfirmationError(f"this permission expired at {mandate.exp:%d %b %Y, %H:%M}; nothing was done")
        if pending.card.type_amount and typed_amount != pending.intent.val("amount"):
            raise ConfirmationError("typed amount does not match")
        held = None
        if self.intervention is not None:
            try:
                held = self.intervention.before_sign(pending.intent, answers, pin)
            except ValueError as e:
                self.journal.append("client", "confirmation.rejected", {"card": pending_id, "reason": str(e)})
                raise ConfirmationError(str(e)) from e
        try:
            signed = self.mandates.issue(mandate, device_alg, device_key, device_sig)
        except MandateError as e:
            self.journal.append("client", "confirmation.rejected", {"card": pending_id, "reason": str(e)})
            raise ConfirmationError(str(e)) from e
        del self.pending[pending_id]
        self.journal.append("client", "mandate.signed", {"card": pending_id, "mandate": signed.mandate.id,
                                                         "hash": signed.mandate_hash, "standing": standing},
                            stage=int(signed.mandate.stage))
        intent = pending.intent.model_copy(update={"mandate_id": signed.mandate.id})
        passport = self.passports.get(pending.passport_id)
        if held is not None and held.level >= 3:
            h = self.intervention.hold(held, intent, passport)
            return Decision(outcome=Outcome.HELD, intent_id=intent.id, verdict=Verdict.ASK, risk=Risk.HIGH,
                            reasons=[f"held: {h.status}"], hold=h.model_dump(mode="json", exclude={"intent"}),
                            stage=int(signed.mandate.stage))
        return self.pipeline.run(intent, passport, confirmed=True)

    def decline(self, pending_id: str) -> None:
        if self.pending.pop(pending_id, None) is not None:
            self.journal.append("client", "confirmation.declined", {"card": pending_id})

    def revoke(self, mandate_id: str) -> list[str]:
        revoked = self.mandates.revoke(mandate_id)
        self.journal.append("client", "mandate.revoked", {"mandates": revoked})
        return revoked

    def reverse(self, intent_id: str, reason: str = "") -> dict[str, Any]:
        """The client says an action taken by an agent was wrong: a penalty for the agent and the kind of task."""
        done = next((e for e in reversed(self.journal.entries)
                     if e.event == "decision.executed" and e.data.get("intent") == intent_id), None)
        if done is None:
            raise ConfirmationError("no executed action with this id")
        passport = self.passports.get(done.passport_id or "")
        payee = self.directory.payee(passport.client_id, done.data["payee"]) \
            if passport is not None and isinstance(done.data.get("payee"), str) else None
        category = category_for(Action(done.data["action"]), payee.kind if payee else None)
        self.journal.append("client", "action.reversed", {"intent": intent_id, "reason": reason},
                            passport_id=done.passport_id)
        level = self.trust.record(category, TrustEvent.CLIENT_REVERSAL, passport)
        return {"category": category.value, "level": level, "agent": agent_key(passport) if passport else None}

    def _on_trust(self, c: TrustChange) -> None:
        self.journal.append("kernel", "trust.changed", c.model_dump(mode="json", exclude_none=True),
                            passport_id=c.passport_id)
        if c.status_after == AgentStatus.QUARANTINED:
            revoked = self.passports.revoke_agent(c.agent or "", agent_key)
            self.journal.append("kernel", "agent.passports_revoked", {"agent": c.agent, "passports": revoked})

    def halt(self, reason: str) -> None:
        """Kill switch: every further action is denied until resumed."""
        self.halted = reason
        self.journal.append("operator", "kernel.halted", {"reason": reason})

    def resume(self) -> None:
        self.halted = None
        self.journal.append("operator", "kernel.resumed", {})
