"""HTTP API of the sandbox bank. Executor endpoints need the executor token held by the kernel;
merchant endpoints are what the sandbox merchant websites call; the client never calls this directly."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .bank import BankError, SandboxBank


class PaymentIn(BaseModel):
    client_id: str
    payee: str
    amount: int
    funding_source: str
    memo: str = ""
    ref: str | None = None


class TransferIn(BaseModel):
    client_id: str
    src: str
    dst: str
    amount: int
    memo: str = ""


class BlockIn(BaseModel):
    client_id: str
    merchant: str
    card_id: str | None = None


class DisputeIn(BaseModel):
    client_id: str
    tx_id: str
    reason: str


class CancelIn(BaseModel):
    client_id: str
    card_id: str | None = None


def create_bank_app(bank: SandboxBank, *, executor_token: str | None = None,
                    merchant_token: str | None = None) -> FastAPI:
    app = FastAPI(title="Sandbox bank", version="0.2.0")
    ex_token = executor_token or os.environ.get("BANK_EXECUTOR_TOKEN", "")
    mc_token = merchant_token or os.environ.get("BANK_MERCHANT_TOKEN", "")

    def check(expected: str, got: str | None) -> None:
        if not expected or not got or not hmac.compare_digest(expected, got):
            raise HTTPException(403, "token required")

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except BankError as e:
            raise HTTPException(409, str(e)) from e

    ex = APIRouter(prefix="/executor")

    @ex.post("/payments")
    def pay(p: PaymentIn, x_executor_token: str | None = Header(default=None)):
        check(ex_token, x_executor_token)
        t = run(bank.pay, p.client_id, p.payee, p.amount, p.funding_source, memo=p.memo, ref=p.ref, initiator="agent")
        return {"tx_id": t.id}

    @ex.post("/transfers")
    def transfer(p: TransferIn, x_executor_token: str | None = Header(default=None)):
        check(ex_token, x_executor_token)
        ts = run(bank.transfer_own, p.client_id, p.src, p.dst, p.amount, initiator="agent", memo=p.memo)
        return {"tx_ids": [t.id for t in ts]}

    @ex.post("/blocks")
    def block(p: BlockIn, x_executor_token: str | None = Header(default=None)):
        check(ex_token, x_executor_token)
        run(bank.block_merchant, p.client_id, p.merchant, p.card_id)
        return {"blocked": p.merchant}

    @ex.post("/disputes")
    def dispute(p: DisputeIn, x_executor_token: str | None = Header(default=None)):
        check(ex_token, x_executor_token)
        d = run(bank.open_dispute, p.client_id, p.tx_id, p.reason)
        return {"dispute_id": d.id, "resolve_on": d.resolve_on.isoformat()}

    mc = APIRouter(prefix="/merchant")

    @mc.post("/{merchant}/cancel")
    def cancel(merchant: str, p: CancelIn, x_merchant_token: str | None = Header(default=None)):
        check(mc_token, x_merchant_token)
        return {"cancelled": bank.cancel_card_on_file(p.client_id, merchant, p.card_id)}

    @app.get("/health")
    def health():
        return {"ok": True, "now": bank.clock.now().isoformat()}

    app.include_router(ex)
    app.include_router(mc)
    return app
