"""HTTP API and web UI for the sandbox demo.

    uvicorn guardian.api:app --port 8000      then open http://localhost:8000
"""

from __future__ import annotations

import copy
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import demo, redteam
from .audit import verify_chain
from .baiter import load_scenarios
from .core import DATA_DIR, Guardian
from .firewall import load_shop, scan_page
from .ledger import LedgerError
from .models import Initiator, Payee, PaymentRequest, Rail
from .profile import build_demo, pay_day

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Secure AI Financial Guardian", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Session:
    def __init__(self):
        self.g: Guardian = build_demo()
        self.decoy = False  # True after a duress PIN until the owner logs in with the normal PIN


S = Session()


def _dump(obj):
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


# ----------------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


# ----------------------------------------------------------------------------- state

@app.get("/api/state")
def state():
    g = S.g
    view = g.account_view(decoy=S.decoy)
    txs = []
    for t in reversed(g.bank.transactions[-40:]):
        d = t.model_dump(mode="json")
        if S.decoy and t.kind == "quarantine":
            d.update(kind="payment", status="executed")  # the coercer must see a normal transfer
        if S.decoy and t.kind in ("release",):
            continue
        txs.append(d)
    envs = []
    for e in g.bank.envelopes.values():
        r = e.rules
        rules = []
        if r.allowed_payees is not None:
            rules.append("only: " + ", ".join(g.bank.payees[p].name for p in r.allowed_payees if p in g.bank.payees))
        if r.allowed_categories is not None:
            rules.append("categories: " + ", ".join(r.allowed_categories))
        if r.allowed_rails is not None:
            rules.append("rails: " + ", ".join(x.value.replace("_", " ") for x in r.allowed_rails))
        if r.max_per_tx is not None:
            rules.append(f"max ${r.max_per_tx / 100:,.0f} per payment")
        if r.day_window:
            rules.append(f"days {r.day_window[0]}-{r.day_window[1]}")
        if r.locked_until:
            rules.append(f"locked until {r.locked_until:%b %d}")
        if r.new_payee_cap is not None:
            rules.append(f"new payees max ${r.new_payee_cap / 100:,.0f}")
        rules.append("unverified payees allowed" if r.allow_unverified_payees else "verified payees only")
        envs.append({"id": e.id, "name": e.name, "purpose": e.purpose, "balance": e.balance, "protected": e.protected,
                     "rules": rules})
    return {
        "clock": g.clock.now().isoformat(),
        "decoy": S.decoy,
        "owner": g.bank.owner,
        "total": view["total"],
        "quarantine": None if S.decoy else g.bank.quarantine,
        "unallocated": g.bank.unallocated,
        "available": g.available_balance(),
        "envelopes": envs,
        "transactions": txs,
        "pending": [{"id": k, "payee": r.payee.name, "amount": r.amount, "initiator": r.initiator.value,
                     "memo": r.memo} for k, r in g.pending.items()],
        "outbox": [] if S.decoy else list(reversed(g.outbox[-20:])),
        "incidents": [] if S.decoy else g.incidents,
        "blocklist": [] if S.decoy else g.blocklist.items(),
        "exposure": {"without_guardian": g.bank.total, "with_guardian": g.policy.worst_case_exposure()},
        "risk_model": g.autonomy.model.report,
        "autonomy_trust": g.autonomy.trust,
        "agent_trust": g.firewall.agent_trust,
        "llm": g.llm.available,
        "audit_entries": len(g.audit.entries),
    }


@app.post("/api/reset")
def reset():
    global S
    S = Session()
    return state()


class ClockIn(BaseModel):
    action: str  # payday | next_day | next_hour
    days: int = 1


@app.post("/api/clock")
def clock(body: ClockIn):
    g = S.g
    res = {}
    if body.action == "payday":
        res["allocations"] = pay_day(g)
    elif body.action == "next_day":
        g.clock.advance(days=body.days)
    elif body.action == "next_hour":
        g.clock.advance(hours=1)
    else:
        raise HTTPException(400, "unknown action")
    return {"result": res, "state": state()}


@app.post("/api/bills/run")
def bills():
    return {"outcomes": [_dump(o) for o in S.g.run_due_bills()], "state": state()}


# ----------------------------------------------------------------------------- payments

class PayIn(BaseModel):
    payee_name: str
    account: str
    amount: float
    rail: Rail = Rail.DOMESTIC_TRANSFER
    category: str = "other"
    memo: str = ""
    pin: str | None = None
    country: str = "AU"


@app.post("/api/pay")
def pay(body: PayIn):
    g = S.g
    n_inc = len(g.incidents)
    known = g.bank.find_payee_by_account(body.account)
    payee = known or Payee(name=body.payee_name, account=body.account, country=body.country)
    req = PaymentRequest(payee=payee, amount=int(round(body.amount * 100)), rail=body.rail, category=body.category,
                         memo=body.memo, initiator=Initiator.USER)
    out = g.request_payment(req, pin=body.pin or None)
    if len(g.incidents) > n_inc:
        S.decoy = True
        # Only the decoy is returned: the device may be in the attacker's hands.
        return {"outcome": {"status": "executed", "receipt": out.receipt, "explanation": "Transfer sent."},
                "state": state()}
    return {"outcome": _dump(out), "state": state()}


class ConfirmIn(BaseModel):
    pending_id: str
    approve: bool
    pin: str | None = None


@app.post("/api/confirm")
def confirm(body: ConfirmIn):
    g = S.g
    n_inc = len(g.incidents)
    out = g.confirm(body.pending_id, body.approve, pin=body.pin or None)
    if len(g.incidents) > n_inc:
        S.decoy = True
        return {"outcome": {"status": "executed", "receipt": out.receipt, "explanation": "Transfer sent."},
                "state": state()}
    return {"outcome": _dump(out), "state": state()}


class TxIn(BaseModel):
    tx_id: str


@app.post("/api/revert")
def revert(body: TxIn):
    try:
        return {"result": S.g.revert(body.tx_id), "state": state()}
    except LedgerError as e:
        raise HTTPException(400, str(e))


class LoginIn(BaseModel):
    pin: str


@app.post("/api/session/login")
def login(body: LoginIn):
    mode = S.g.duress.check_pin(body.pin)
    if mode == "normal":
        S.decoy = False
    elif mode in ("invalid", "locked"):
        raise HTTPException(401, "Wrong PIN" if mode == "invalid" else "Locked")
    return state()


@app.get("/api/backoffice")
def backoffice():
    """Bank-side fraud console: always shows the truth, even while the app shows a decoy."""
    g = S.g
    return {"quarantine": g.bank.quarantine, "bank_total": g.bank.total, "incidents": g.incidents,
            "alerts": [m for m in g.outbox if m["kind"] == "duress"], "decoy_session": S.decoy}


@app.post("/api/duress/resolve")
def resolve():
    if S.decoy:
        raise HTTPException(403, "Log in with the normal PIN first")
    open_inc = [i for i, inc in enumerate(S.g.incidents) if not inc.get("resolved")]
    if not open_inc:
        raise HTTPException(400, "No open incident")
    return {"result": S.g.resolve_incident(open_inc[-1]), "state": state()}


# ----------------------------------------------------------------------------- scam shield and baiter

@app.get("/api/scenarios")
def scenarios():
    data = load_scenarios(DATA_DIR / "scam_scripts.json")
    items = [{"id": s["id"], "title": s["title"], "sender": s["sender"], "text": s["opening"], "kind": "scam"}
             for s in data.values()]
    items.append({"id": "friend_pizza", "title": "Friend asks for pizza money", "sender": "Tom (classmate)",
                  "text": "Hey Mia, it's Tom. You owe me $24 for the pizza on Friday, my PayID is "
                          "tom.lee@uni-mail.example. No rush!", "kind": "legit"})
    return items


class MessageIn(BaseModel):
    text: str
    sender: str


@app.post("/api/message")
def message(body: MessageIn):
    a, out = S.g.handle_message(body.text, body.sender)
    return {"analysis": _dump(a), "outcome": _dump(out) if out else None, "state": state()}


class BaitIn(BaseModel):
    scenario_id: str


@app.post("/api/bait")
def bait(body: BaitIn):
    report, added = S.g.bait(body.scenario_id)
    return {"report": _dump(report), "added": added, "police_report": S.g.police_report(report), "state": state()}


# ----------------------------------------------------------------------------- agent shopping

class ShopIn(BaseModel):
    agent: str = "honest"
    description: str = "USB-C laptop charger, 65W"
    keywords: list[str] = ["charger"]
    categories: list[str] = ["electronics"]
    max_total: float = 60


@app.post("/api/shop")
def shop(body: ShopIn):
    g = S.g
    m = g.create_mandate("*", body.description, body.keywords, body.categories, int(round(body.max_total * 100)))
    r = g.agent_shop(body.agent, m)
    return {"result": _dump(r), "state": state()}


@app.get("/api/shop/pages")
def shop_pages():
    pages = load_shop(DATA_DIR / "shop")
    return [{"name": n, "html": h, "findings": [_dump(f) for f in scan_page(n, h)]} for n, h in pages.items()]


# ----------------------------------------------------------------------------- audit

@app.get("/api/audit")
def audit(n: int = 200):
    return {"public_key": S.g.audit.public_key, "entries": [_dump(e) for e in S.g.audit.tail(n)],
            "verification": _dump(S.g.audit_verify())}


class TamperIn(BaseModel):
    seq: int | None = None
    mode: str = "edit"  # edit | delete | reorder


@app.post("/api/audit/tamper")
def tamper(body: TamperIn):
    """Verify a tampered *copy* of the log. The real log is never modified."""
    entries = copy.deepcopy(S.g.audit.entries)
    if len(entries) < 3:
        raise HTTPException(400, "Not enough entries yet")
    seq = body.seq if body.seq is not None else len(entries) // 2
    if body.mode == "delete":
        entries.pop(seq)
    elif body.mode == "reorder":
        entries[seq], entries[seq + 1] = entries[seq + 1], entries[seq]
    else:
        entries[seq].data["tampered"] = True
    return {"mode": body.mode, "seq": seq, "verification": _dump(verify_chain(entries, S.g.audit.public_key)),
            "original": _dump(S.g.audit_verify())}


# ----------------------------------------------------------------------------- story and red team

@app.post("/api/story")
def story():
    return demo.run()


@app.post("/api/redteam")
def run_redteam():
    return redteam.run()
