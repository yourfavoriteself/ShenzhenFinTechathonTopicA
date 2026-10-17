"""Planner: builds intents for a new task from kernel facts. It sees only structured facts with
references (no raw documents, no account details, no secrets) and answers with references, never
with values of its own; the kernel re-resolves every reference."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel

from kernel.intent import IntentProposal
from kernel.llm_gateway import LLMGateway
from kernel.mandate import Action

from .base import claim

SYSTEM = """You are the planner of a personal finance assistant inside a bank.
You receive the client's request and facts prepared by the bank's kernel. Every fact has a "ref".
Choose the payee, amount and funding source by their refs. Never invent refs or values.
Prefer the client's budget envelope that matches the payee's category; otherwise the main card.
If no payee fits the request, return no intents and say why in "note"."""


class PlannedIntent(BaseModel):
    action: Literal["pay", "transfer_own", "block_merchant", "dispute"]
    payee_ref: str
    amount_ref: str | None = None
    funding_ref: str | None = None
    memo: str = ""


class Plan(BaseModel):
    intents: list[PlannedIntent]
    note: str = ""
    confidence: float = 1.0


class Planner:
    LEVEL = 2

    def __init__(self, gateway: LLMGateway):
        self.gw = gateway
        self.last_level: int | None = None

    def plan(self, request: str, facts: dict[str, Any], task_id: str) -> tuple[list[IntentProposal], str]:
        payload = {"client_request": request, "client_message_ref": facts.get("client_message_ref"),
                   "facts": {k: v for k, v in facts.items() if k != "client_message_ref"}}
        res = self.gw.call("planner", SYSTEM, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                           level=self.LEVEL, task_id=task_id, schema=Plan, max_tokens=600, effort="medium",
                           min_confidence=0.5)
        self.last_level = res.level
        plan = Plan.model_validate(res.parsed)
        by_ref = _index(facts)
        out = []
        for p in plan.intents:
            out.append(IntentProposal(action=Action(p.action), payee=claim(by_ref.get(p.payee_ref)),
                                      amount=claim(by_ref.get(p.amount_ref)) if p.amount_ref else None,
                                      funding_source=claim(by_ref.get(p.funding_ref)) if p.funding_ref else None,
                                      memo=p.memo[:80]))
        return out, plan.note


def _index(facts: dict[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(x):
        if isinstance(x, dict):
            if "ref" in x and "value" in x:
                out[x["ref"]] = x
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(facts)
    return out


def stub_responder(req: dict) -> str:
    """Stand-in for the planner model: best name match, usual amount, matching envelope or the card."""
    p = json.loads(req["user"])
    words = set(re.findall(r"[a-z0-9]+", p["client_request"].lower()))
    best = None
    for c in p["facts"].get("payees", []):
        name = set(re.findall(r"[a-z0-9]+", c["name"].lower()))
        if name & words and (best is None or c.get("verified")):
            best = c
    if best is None:
        return json.dumps({"intents": [], "note": "no payee in your history or the bank's directory matches"})
    srcs = p["facts"].get("funding_sources", [])
    src = next((s for s in srcs if s.get("matches_category")), srcs[0] if srcs else None)
    return json.dumps({"intents": [{"action": "pay", "payee_ref": best["payee"]["ref"],
                                    "amount_ref": best["usual_amount"]["ref"] if best.get("usual_amount") else None,
                                    "funding_ref": src["source"]["ref"] if src else None,
                                    "memo": f"{best['name']} monthly payment"}],
                       "note": "", "confidence": 0.9})
