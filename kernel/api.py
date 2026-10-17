"""HTTP surface of the kernel.

Agents reach it only with requests signed by their session key (`/agent/{endpoint}`); the client app
reads cards and answers them with a device signature; the operator holds the kill switch. Nothing
here bypasses the pipeline: the API only translates HTTP into `Kernel` calls.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .cards import describe
from .core import ConfirmationError, Kernel
from .llm_gateway import LLMGateway
from .passport import SignedRequest
from .pipeline import Decision


class ConfirmBody(BaseModel):
    device_alg: str
    device_key: str
    device_sig: str
    standing: bool = False
    typed_amount: int | None = None


class HaltBody(BaseModel):
    reason: str


def create_app(kernel: Kernel, *, gateway: LLMGateway | None = None, operator_token: str | None = None) -> FastAPI:
    app = FastAPI(title="Guardian kernel", version="0.2.0")
    app.state.kernel = kernel
    token = operator_token or os.environ.get("GUARDIAN_OPERATOR_TOKEN", "")

    def operator(x_operator_token: str | None) -> None:
        if not token or not x_operator_token or not hmac.compare_digest(token, x_operator_token):
            raise HTTPException(403, "operator token required")

    agent = APIRouter(prefix="/agent", tags=["agents"])

    @agent.post("/propose_intent")
    def propose_intent(req: SignedRequest) -> Decision:
        if req.endpoint != "propose_intent":
            raise HTTPException(400, "endpoint in the signed request does not match the URL")
        return kernel.submit(req)

    client = APIRouter(tags=["client"])

    @client.get("/cards/{pending_id}")
    def card(pending_id: str) -> dict[str, Any]:
        p = kernel.pending.get(pending_id)
        if p is None:
            raise HTTPException(404, "no such card")
        return p.card.model_dump(mode="json")

    @client.post("/cards/{pending_id}/confirm")
    def confirm(pending_id: str, body: ConfirmBody) -> Decision:
        try:
            return kernel.confirm(pending_id, body.device_alg, body.device_key, body.device_sig,
                                  standing=body.standing, typed_amount=body.typed_amount)
        except ConfirmationError as e:
            raise HTTPException(409, str(e)) from e

    @client.post("/cards/{pending_id}/decline")
    def decline(pending_id: str) -> dict[str, str]:
        kernel.decline(pending_id)
        return {"status": "declined"}

    @client.get("/clients/{client_id}/mandates")
    def mandates(client_id: str, standing_only: bool = True) -> list[dict[str, Any]]:
        out = []
        for sm in kernel.mandates.active(client_id, standing_only):
            m = sm.mandate
            names = {}
            if m.payment:
                for t in m.payment.allowed_payees:
                    info = kernel.directory.payee(client_id, t)
                    names[t] = info.name if info else t
            can, cannot = describe(m, names, kernel.funds.label(client_id, m.funding_source or ""))
            st = kernel.mandates.state(m.id)
            out.append({"id": m.id, "hash": sm.mandate_hash, "standing": m.standing, "stage": int(m.stage),
                        "exp": m.exp.isoformat(), "spent": st.spent if st else 0, "can": can, "cannot": cannot})
        return out

    @client.post("/mandates/{mandate_id}/revoke")
    def revoke(mandate_id: str) -> dict[str, list[str]]:
        if kernel.mandates.get(mandate_id) is None:
            raise HTTPException(404, "no such mandate")
        return {"revoked": kernel.revoke(mandate_id)}

    audit = APIRouter(prefix="/journal", tags=["audit"])

    @audit.get("")
    def journal(since: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in kernel.journal.entries[since:since + limit]]

    @audit.get("/verify")
    def verify() -> dict[str, Any]:
        return kernel.journal.verify().model_dump()

    @audit.get("/checkpoint")
    def checkpoint() -> dict[str, Any]:
        if not kernel.journal.entries:
            raise HTTPException(404, "journal is empty")
        return {**kernel.journal.checkpoint(), "public_key": kernel.journal.public_key}

    ops = APIRouter(prefix="/operator", tags=["operator"])

    @ops.post("/halt")
    def halt(body: HaltBody, x_operator_token: str | None = Header(default=None)) -> dict[str, str]:
        operator(x_operator_token)
        kernel.halt(body.reason)
        return {"status": "halted"}

    @ops.post("/resume")
    def resume(x_operator_token: str | None = Header(default=None)) -> dict[str, str]:
        operator(x_operator_token)
        kernel.resume()
        return {"status": "running"}

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "halted": kernel.halted, "kernel_key": kernel.key.public_b64,
                "journal_key": kernel.journal.public_key, "now": kernel.clock.now().isoformat()}

    @app.get("/llm/report")
    def llm_report() -> dict[str, Any]:
        if gateway is None:
            raise HTTPException(404, "no LLM gateway attached")
        return gateway.report()

    for r in (agent, client, audit, ops):
        app.include_router(r)
    return app
