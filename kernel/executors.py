"""Executors: the only code that moves money, each call backed by a single-use grant.

They reach the bank through `BankRails`: in-process for tests and replay, or the sandbox bank's
HTTP API with an executor token (`HttpRails`). The kernel then reads the bank's statements back to
confirm the result instead of trusting the executor's word.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .intent import Intent
from .mandate import Action, Grant
from .ports import Receipt
from .statements import DataSource

TX_REF = "bank:tx:"


class RailsError(Exception):
    pass


class BankRails(Protocol):
    def pay(self, client_id: str, payee: str, amount: int, funding_source: str, memo: str, ref: str) -> dict: ...
    def transfer_own(self, client_id: str, src: str, dst: str, amount: int, memo: str) -> dict: ...
    def block_merchant(self, client_id: str, merchant: str, card_id: str | None) -> dict: ...
    def open_dispute(self, client_id: str, tx_id: str, reason: str) -> dict: ...


class LocalRails:
    """Direct calls into an in-process sandbox bank."""

    def __init__(self, bank: Any):
        self.bank = bank

    def _do(self, fn, *a, **kw) -> Any:
        from sandbox.bank.bank import BankError
        try:
            return fn(*a, **kw)
        except BankError as e:
            raise RailsError(str(e)) from e

    def pay(self, client_id, payee, amount, funding_source, memo, ref):
        t = self._do(self.bank.pay, client_id, payee, amount, funding_source, memo=memo, ref=ref, initiator="agent")
        return {"tx_id": t.id}

    def transfer_own(self, client_id, src, dst, amount, memo):
        ts = self._do(self.bank.transfer_own, client_id, src, dst, amount, initiator="agent", memo=memo)
        return {"tx_ids": [t.id for t in ts]}

    def block_merchant(self, client_id, merchant, card_id):
        self._do(self.bank.block_merchant, client_id, merchant, card_id)
        return {"blocked": merchant}

    def open_dispute(self, client_id, tx_id, reason):
        d = self._do(self.bank.open_dispute, client_id, tx_id, reason)
        return {"dispute_id": d.id, "resolve_on": d.resolve_on.isoformat()}


class HttpRails:
    def __init__(self, client: httpx.Client, token: str):
        self.client = client
        self.headers = {"X-Executor-Token": token}

    def _post(self, path: str, body: dict) -> dict:
        r = self.client.post(path, json=body, headers=self.headers)
        if r.status_code != 200:
            raise RailsError(r.json().get("detail", f"HTTP {r.status_code}"))
        return r.json()

    def pay(self, client_id, payee, amount, funding_source, memo, ref):
        return self._post("/executor/payments", {"client_id": client_id, "payee": payee, "amount": amount,
                                                 "funding_source": funding_source, "memo": memo, "ref": ref})

    def transfer_own(self, client_id, src, dst, amount, memo):
        return self._post("/executor/transfers", {"client_id": client_id, "src": src, "dst": dst, "amount": amount,
                                                  "memo": memo})

    def block_merchant(self, client_id, merchant, card_id):
        return self._post("/executor/blocks", {"client_id": client_id, "merchant": merchant, "card_id": card_id})

    def open_dispute(self, client_id, tx_id, reason):
        return self._post("/executor/disputes", {"client_id": client_id, "tx_id": tx_id, "reason": reason})


class BankExecutor:
    def __init__(self, rails: BankRails):
        self.rails = rails

    def execute(self, grant: Grant, intent: Intent) -> Receipt:
        c = intent.client_id
        try:
            match grant.action:
                case Action.PAY:
                    r = self.rails.pay(c, grant.payee, grant.amount, grant.funding_source, intent.memo, grant.id)
                    return Receipt(ok=True, reference=r["tx_id"], details=r)
                case Action.TRANSFER_OWN:
                    r = self.rails.transfer_own(c, grant.funding_source, grant.payee, grant.amount, intent.memo)
                    return Receipt(ok=True, reference=",".join(r["tx_ids"]) or "internal", details=r)
                case Action.BLOCK_MERCHANT:
                    r = self.rails.block_merchant(c, grant.payee, None)
                    return Receipt(ok=True, reference=f"block:{grant.payee}", details=r)
                case Action.DISPUTE:
                    tx = next((x.removeprefix(TX_REF) for x in intent.references if x.startswith(TX_REF)), None)
                    if tx is None:
                        return Receipt(ok=False, details={"error": "no transaction reference to dispute"})
                    r = self.rails.open_dispute(c, tx, intent.memo or "duplicate charge")
                    return Receipt(ok=True, reference=r["dispute_id"], details=r)
                case _:
                    return Receipt(ok=False, details={"error": f"{grant.action.value} is not a bank operation"})
        except RailsError as e:
            return Receipt(ok=False, details={"error": str(e)})


class StatementVerifier:
    """Confirms a payment from the bank's own records: right payee, amount and grant reference."""

    def __init__(self, data: DataSource, bank: Any | None = None):
        self.data = data
        self.bank = bank

    def __call__(self, intent: Intent, receipt: Receipt) -> bool:
        if intent.action == Action.PAY:
            return any(t.id == receipt.reference and t.counterparty == intent.val("payee")
                       and t.amount == intent.val("amount") for t in self.data.transactions(intent.client_id))
        if intent.action == Action.BLOCK_MERCHANT and self.bank is not None:
            return self.bank.is_blocked(intent.client_id, intent.val("payee"), None)
        if intent.action == Action.DISPUTE and self.bank is not None:
            return receipt.reference in self.bank.disputes
        return receipt.ok
