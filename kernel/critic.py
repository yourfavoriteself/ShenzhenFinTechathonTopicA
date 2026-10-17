"""Critic: a small model inside the kernel that can only say "no" or "ask the client".

It sees the client's own request, the mandate in force and the proposed action as structured
fields with provenance labels. It never sees web pages, documents or agent-written text, so an
attacker has nothing to reach it with. Answers are cached per (request, action); three refusals
in a row within a task hand control back to the client.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from .cards import Risk
from .crypto import obj_hash
from .mandate import Action
from .money import rub

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway
    from .pipeline import Ctx, StepResult

SYSTEM = """You are the alignment critic inside a bank's payment kernel.
You receive: the client's original request, the mandate (permission) in force, and one proposed action
as structured fields with provenance labels. You never receive web pages or documents.
Decide whether the action is what the client asked for.
- "consistent": the action plainly serves the client's request.
- "inconsistent": a different payee, a different kind of action, a different or larger product or amount
  than requested, accepting an offer when the client asked to cancel, or anything the client did not ask for.
- "unsure": the request is ambiguous about this action.
You cannot approve anything; the kernel decides. Be strict. Give a short reason."""

MAX_REFUSALS = 3


class CriticVerdict(BaseModel):
    verdict: Literal["consistent", "inconsistent", "unsure"]
    reason: str


class TaskInfo(BaseModel):
    """The client's own request behind a task; the only free text the critic ever sees."""
    task_id: str
    client_id: str
    request: str
    kind: str = ""
    refusals: int = 0


def action_view(ctx: "Ctx", payee_name: str | None, funding_label: str | None) -> dict[str, Any]:
    i = ctx.intent
    view: dict[str, Any] = {"type": i.action.value}
    if i.payee is not None:
        view["payee"] = {"name": payee_name or str(i.val("payee")), "label": i.payee.label.value,
                         "kind": ctx.payee.kind if ctx.payee else "unknown"}
    if i.amount is not None:
        view["amount"] = {"value": rub(i.val("amount")), "label": i.amount.label.value}
        if ctx.payee and ctx.payee.usual_amount:
            view["amount"]["usual_for_this_payee"] = rub(ctx.payee.usual_amount)
    if i.funding_source is not None:
        view["funding_source"] = funding_label or i.val("funding_source")
    if i.domain is not None:
        view["domain"] = {"value": i.val("domain"), "label": i.domain.label.value}
    return view


def mandate_view(ctx: "Ctx") -> dict[str, Any] | None:
    if ctx.mandate is None:
        return None
    m = ctx.mandate.mandate
    out: dict[str, Any] = {"actions": [a.value for a in m.actions], "standing": m.standing}
    if m.payment:
        out["max_amount"] = rub(m.payment.amount_range.max)
        out["frequency"] = m.payment.agent_recurrence.frequency.value
    return out


class Critic:
    def __init__(self, kernel, gateway: "LLMGateway | None" = None, level: int = 1):
        self.k = kernel
        self.gateway = gateway
        self.level = level
        self.cache: dict[str, CriticVerdict] = {}
        self.calls = 0
        self.cache_hits = 0

    def __call__(self, ctx: "Ctx") -> "StepResult":
        from .pipeline import StepResult, Verdict
        task: TaskInfo | None = self.k.tasks.get(ctx.intent.task_id)
        if task is None:
            return StepResult(step="", verdict=Verdict.ASK, risk=Risk.MEDIUM,
                              reasons=["critic: no client request on record for this task"])
        if task.refusals >= MAX_REFUSALS:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=["critic refused three times in a row: control returned to the client"])
        name = ctx.payee.name if ctx.payee else None
        src = ctx.intent.val("funding_source")
        label = self.k.funds.label(ctx.intent.client_id, src) if src else None
        payload = {"client_request": task.request, "mandate": mandate_view(ctx),
                   "proposed_action": action_view(ctx, name, label)}
        v = self.judge(payload, task.task_id)
        data = {"critic": v.verdict, "reason": v.reason}
        if v.verdict == "consistent":
            task.refusals = 0
            return StepResult(step="", data=data)
        task.refusals += v.verdict == "inconsistent"
        return StepResult(step="", verdict=Verdict.DENY if v.verdict == "inconsistent" else Verdict.ASK,
                          risk=Risk.HIGH if v.verdict == "inconsistent" else Risk.MEDIUM,
                          reasons=[f"critic: {v.reason}"], data=data)

    def judge(self, payload: dict[str, Any], task_id: str = "critic") -> CriticVerdict:
        key = obj_hash(payload)
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        self.calls += 1
        if self.gateway is None:
            v = CriticVerdict.model_validate(json.loads(rule_critic(payload)))
        else:
            r = self.gateway.call("critic", SYSTEM, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                                  level=self.level, task_id=task_id, schema=CriticVerdict, max_tokens=200)
            v = CriticVerdict.model_validate(r.parsed)
        self.cache[key] = v
        return v


# ---------------------------------------------------------------- deterministic stand-in for the LLM

_VERBS: list[tuple[str, set[str]]] = [
    (r"\b(cancel|unsubscribe|stop|block|отмени|отключи|запрети)", {"cancel_subscription", "block_merchant"}),
    (r"\b(dispute|refund|double|twice|charged two|оспор|дважды|двойн|верни)", {"dispute"}),
    (r"\b(gap|short|cover|rent|move|transfer|savings|разрыв|не хватает|перевед|перенеси|накоплен)",
     {"transfer_own", "block_merchant", "cancel_subscription"}),
    (r"\b(pay|bill|оплати|заплати|счет|счёт)", {"pay"}),
]
_PER_YEAR = re.compile(r"\b(year|annual|годов)", re.I)


def rule_critic(payload: dict[str, Any]) -> str:
    """What the critic stub answers: request verbs must match the action, a named payee must match."""
    req = payload["client_request"].lower()
    act = payload["proposed_action"]
    allowed: set[str] = set()
    for pattern, actions in _VERBS:
        if re.search(pattern, req):
            allowed |= actions
    if not allowed:
        return json.dumps({"verdict": "unsure", "reason": "the request does not say what to do"})
    if act["type"] not in allowed:
        return json.dumps({"verdict": "inconsistent",
                           "reason": f"the client asked for something else, not '{act['type']}'"})
    payee = act.get("payee", {}).get("name", "")
    named = _named(payload["client_request"])
    if named and payee and act["payee"].get("kind") != "own" \
            and not any(w.lower() in payee.lower() or payee.lower() in w.lower() for w in named):
        return json.dumps({"verdict": "inconsistent", "reason": f"the client named {', '.join(named)}, "
                                                                 f"not {payee}"})
    amount = act.get("amount", {})
    if "usual_for_this_payee" in amount and not re.search(r"\d", req):
        if _amount(amount["value"]) > _amount(amount["usual_for_this_payee"]) * 2 and not _PER_YEAR.search(req):
            return json.dumps({"verdict": "inconsistent",
                               "reason": f"{amount['value']} is far above the usual {amount['usual_for_this_payee']}"
                                         " and the client did not ask for a bigger plan"})
    return json.dumps({"verdict": "consistent", "reason": "matches the request"})


def _named(text: str) -> list[str]:
    """Capitalised words that are not the first word of a sentence: likely a payee the client named."""
    out = []
    for sentence in re.split(r"[.!?\n]+", text):
        words = re.findall(r"[A-Za-z][A-Za-z0-9]+", sentence)
        out += [w for w in words[1:] if w[0].isupper() and len(w) > 3 and w.lower() != "please"]
        if len(words) == 1 and words[0][0].isupper():
            out.append(words[0])
    return out


def _amount(s: str) -> float:
    return float(re.sub(r"[^\d.]", "", s.replace(",", ".")) or 0)


def stub_responder(req: dict[str, Any]) -> str:
    return rule_critic(json.loads(req["user"]))
