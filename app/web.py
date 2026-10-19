"""Web app for the client: chat, cards signed by the device key, budget, permissions, inbox, journal.

    uvicorn --factory app.web:create_web_app --port 8000

The kernel's agent API is mounted under /kernel. Chat items stream over SSE (/api/events).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kernel.api import create_app

from .sections import all_sections
from .service import GuardianService

WEB = Path(__file__).resolve().parent.parent / "web"


class ChatBody(BaseModel):
    text: str


class DeviceBody(BaseModel):
    alg: str
    public_key: str


class ConfirmBody(BaseModel):
    alg: str
    public_key: str
    sig: str
    standing: bool = False
    typed_amount: int | None = None
    answers: dict[str, bool] | None = None
    pin: str | None = Field(default=None, pattern=r"^\d{4,6}$")


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class CodeBody(BaseModel):
    code: str = Field(min_length=3, max_length=8, pattern=r"^\s*\d+\s*$")


class ForgetBody(BaseModel):
    name: str = Field(max_length=100)


class AdvanceBody(BaseModel):
    days: int


class WaitBody(BaseModel):
    minutes: int = Field(ge=1, le=180)


class CallBody(BaseModel):
    active: bool = True
    spoofed: bool = True


class ProtectionBody(BaseModel):
    elevated: bool


class AnalystBody(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=300)


def _dump(items) -> list[dict]:
    return [i.model_dump(mode="json") for i in items]


def create_web_app(service: GuardianService | None = None) -> FastAPI:
    svc = service or GuardianService()
    app = FastAPI(title="Guardian", version="0.3.0")
    app.state.service = svc
    app.mount("/kernel", create_app(svc.k, gateway=svc.gw))

    @app.get("/api/state")
    def state():
        return svc.state()

    @app.get("/api/sections")
    def sections():
        return all_sections(svc)

    @app.get("/api/items")
    def items(since: int = 0):
        return _dump(svc.since(since))

    @app.post("/api/chat")
    def chat(b: ChatBody):
        text = b.text.strip()
        if not text or len(text) > 2000:
            raise HTTPException(400, "empty or too long message")
        return _dump(svc.chat(text))

    @app.post("/api/device")
    def device(b: DeviceBody):
        if b.alg not in ("Ed25519", "ES256"):
            raise HTTPException(400, "unsupported key type")
        svc.register_device(b.alg, b.public_key)
        return {"ok": True}

    @app.post("/api/cards/{pending_id}/confirm")
    def confirm(pending_id: str, b: ConfirmBody):
        return _dump(svc.confirm(pending_id, b.alg, b.public_key, b.sig, standing=b.standing,
                                 typed_amount=b.typed_amount, answers=b.answers, pin=b.pin))

    @app.post("/api/cards/{pending_id}/decline")
    def decline(pending_id: str):
        return _dump(svc.decline(pending_id))

    @app.post("/api/options/{option_id}")
    def choose(option_id: str):
        return _dump(svc.choose(option_id))

    @app.post("/api/budget/apply")
    def apply_budget():
        return _dump(svc.apply_budget())

    @app.post("/api/mandates/{mandate_id}/revoke")
    def revoke(mandate_id: str):
        if svc.k.mandates.state(mandate_id) is None:
            raise HTTPException(404, "unknown permission")
        return _dump(svc.revoke(mandate_id))

    @app.post("/api/cancellations/{cid}/login")
    def cancel_login(cid: str, b: LoginBody):
        if cid not in svc.cancels.items:
            raise HTTPException(404, "unknown cancellation")
        return _dump(svc.cancel_login(cid, b.login, b.password, b.remember))

    @app.post("/api/cancellations/{cid}/code")
    def cancel_code(cid: str, b: CodeBody):
        if cid not in svc.cancels.items:
            raise HTTPException(404, "unknown cancellation")
        return _dump(svc.cancel_code(cid, b.code))

    @app.post("/api/vault/forget")
    def forget(b: ForgetBody):
        return _dump(svc.forget_secret(b.name))

    @app.post("/api/time/advance")
    def advance(b: AdvanceBody):
        if not 1 <= b.days <= 62:
            raise HTTPException(400, "1 to 62 days")
        return _dump(svc.advance(b.days))

    @app.post("/api/time/wait")
    def wait(b: WaitBody):
        return _dump(svc.wait(b.minutes))

    @app.post("/api/demo/call")
    def call(b: CallBody):
        return _dump(svc.scam_call(b.active, b.spoofed))

    @app.post("/api/demo/faulty_agent")
    def faulty_agent():
        return _dump(svc.faulty_agent())

    @app.post("/api/protection")
    def protection(b: ProtectionBody):
        return _dump(svc.set_protection(b.elevated))

    @app.post("/api/holds/{hold_id}/cancel")
    def hold_cancel(hold_id: str):
        return _dump(svc.hold_cancel(hold_id))

    @app.post("/api/holds/{hold_id}/analyst")
    def analyst(hold_id: str, b: AnalystBody):
        """Stands in for the bank's case-management screen: a human analyst decides."""
        if hold_id not in svc.guard.holds:
            raise HTTPException(404, "unknown hold")
        return _dump(svc.analyst_decide(hold_id, b.approve, b.note))

    @app.get("/api/journal")
    def journal(limit: int = 40):
        v = svc.k.journal.verify()
        entries = [{"seq": e.seq, "ts": e.ts, "actor": e.actor, "event": e.event, "stage": e.stage,
                    "hash": e.hash[:12]} for e in svc.k.journal.entries[-limit:]]
        return {"ok": v.ok, "checked": v.checked, "head": (v.head_hash or "")[:16], "entries": entries[::-1]}

    @app.get("/api/cases")
    def cases():
        return [c.model_dump(mode="json") for c in svc.desk.cases]

    @app.get("/api/events")
    async def events(request: Request, since: int = 0, once: bool = False):
        async def stream():
            seq, version = since, -1
            while True:
                new = svc.since(seq)
                for it in new:
                    yield f"event: item\ndata: {json.dumps(it.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                    seq = it.seq
                if svc.version != version:
                    version = svc.version
                    yield f"event: state\ndata: {json.dumps({'version': version})}\n\n"
                if once or await request.is_disconnected():
                    return
                await asyncio.sleep(0.4)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
