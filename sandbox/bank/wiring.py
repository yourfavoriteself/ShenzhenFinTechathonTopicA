"""Connect a kernel to a sandbox bank: directory, funds, executor and result verification."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from kernel import budget as B
from kernel.core import Kernel
from kernel.executors import BankExecutor, BankRails, LocalRails, StatementVerifier
from kernel.merchants import ClientDirectory, MerchantDirectory

from .bank import Envelope, SandboxBank


def connect(bank: SandboxBank, merchants: MerchantDirectory, *, rails: BankRails | None = None,
            seed: str = "demo", elevated_protection: bool = False) -> Kernel:
    k = Kernel(clock=bank.clock, seed=seed, directory=ClientDirectory(merchants, bank), funds=bank,
               executor=BankExecutor(rails or LocalRails(bank)), elevated_protection=elevated_protection)
    k.pipeline.verifier = StatementVerifier(bank, bank)
    return k


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
