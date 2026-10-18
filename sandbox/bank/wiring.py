"""Connect a kernel to a sandbox bank: directory, funds, executor and result verification."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from kernel import budget as B
from kernel.core import Kernel
from kernel.executors import BankExecutor, BankRails, LocalRails, RailsError, StatementVerifier
from kernel.mandate import Action
from kernel.merchants import ClientDirectory, MerchantDirectory
from kernel.pipeline import StepResult, Verdict

from .bank import Envelope, SandboxBank


def connect(bank: SandboxBank, merchants: MerchantDirectory, *, rails: BankRails | None = None,
            seed: str = "demo", elevated_protection: bool = False) -> Kernel:
    k = Kernel(clock=bank.clock, seed=seed, directory=ClientDirectory(merchants, bank), funds=bank,
               executor=BankExecutor(rails or LocalRails(bank)), elevated_protection=elevated_protection)
    k.pipeline.verifier = StatementVerifier(bank, bank)
    k.pipeline.shadow = ShadowRun(bank)
    return k


class ShadowRun:
    """Pipeline step "shadow": the money movement runs first on a copy of the bank, and the kernel compares
    the effect with the intent. Exactly one debit to the payee for the amount (or a move between the client's
    own accounts) and nothing else; a movement the bank would refuse goes back to the client as a question."""

    def __init__(self, bank: SandboxBank):
        self.bank = bank

    def __call__(self, ctx) -> StepResult:
        i = ctx.intent
        if i.action not in (Action.PAY, Action.TRANSFER_OWN) or ctx.verdict == Verdict.DENY:
            return StepResult(step="")
        twin, c = self.bank.twin(), i.client_id
        payee, amount, src = i.val("payee"), i.val("amount"), i.val("funding_source")
        seen = {t.id for t in twin.transactions(c)}
        total = sum(a.balance for a in twin.accounts.values() if a.client_id == c)
        rails = LocalRails(twin)
        try:
            if i.action == Action.PAY:
                rails.pay(c, payee, amount, src, i.memo, "shadow")
            else:
                rails.transfer_own(c, src, payee, amount, i.memo)
        except RailsError as e:
            return StepResult(step="", verdict=Verdict.ASK, reasons=[f"a dry run in a copy of the bank failed: {e}"],
                              data={"shadow": "refused"})
        new = [t for t in twin.transactions(c) if t.id not in seen]
        spent = total - sum(a.balance for a in twin.accounts.values() if a.client_id == c)
        debits = [t for t in new if t.direction.value == "debit" and not t.counterparty.startswith(("acc_", "card_"))]
        if i.action == Action.PAY:
            ok = spent == amount and len(debits) == 1 and debits[0].counterparty == payee and debits[0].amount == amount
        else:
            ok = spent == 0 and not debits
        if not ok:
            return StepResult(step="", verdict=Verdict.DENY, reasons=["the dry run in a copy of the bank moved money "
                                                                      "differently from what was asked"],
                              data={"shadow": "mismatch", "spent": spent})
        return StepResult(step="", data={"shadow": "ok"})


def open_envelopes(bank: SandboxBank, client_id: str, account_id: str, plan: B.BudgetPlan,
                   due: dict[str, int], payday) -> dict[str, int]:
    """Create the plan's envelopes and fund them from the current balance until the next payday."""
    now = bank.clock.now()
    period_start = max((d for d in (now.date() - timedelta(days=i) for i in range(31))
                        if d.day in {10, 25}), default=now.date())
    spent = B.spent_by_envelope(bank.transactions(client_id, since=datetime.combine(
        period_start, time(0), tzinfo=now.tzinfo)), B.month_key(now))
    funding = B.initial_funding(plan, spent, due, bank.accounts[account_id].balance,
                                days_left=(payday - now.date()).days, days_in_period=(payday - period_start).days)
    bank.set_envelopes(client_id, account_id, [Envelope(id=e.id, client_id=client_id, account_id=account_id,
                                                        name=e.name, categories=e.categories,
                                                        balance=funding.get(e.id, 0)) for e in plan.envelopes])
    return funding
