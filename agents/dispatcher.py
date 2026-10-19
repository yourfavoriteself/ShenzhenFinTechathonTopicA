"""Dispatcher: recognises what the client asks for. Level 0 is code (rules and saved templates);
only when no rule fits does a small model (level 1) classify the message by a strict schema."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from kernel.llm_gateway import LLMGateway

KINDS = ("pay", "pay_bills", "transfer", "cancel", "goal", "month_review", "forecast_quality", "find_leaks", "double_charges", "budget", "safe_to_spend", "can_afford", "cash_gap",
         "invest", "permissions", "help", "unknown")

SYSTEM = f"""You are the dispatcher of a personal finance assistant inside a bank.
Classify the client's message into one task kind: {", ".join(KINDS)}.
pay = pay a named merchant or person; transfer = send money to an account number;
pay_bills = pay utility bills from the inbox; find_leaks = find wasted
subscriptions; cancel = cancel one subscription (by name or by the amount charged);
double_charges = find and dispute duplicate charges; budget = build a monthly budget;
safe_to_spend = how much can be spent today; can_afford = whether a purchase is affordable;
cash_gap = upcoming shortfall before payday; goal = save a sum by a date;
invest = buy a stock or fund on the US exchange (ticker and dollar amount);
month_review = how last month went; forecast_quality = how accurate the budget forecast is; permissions = what the assistant is allowed to do.
Copy the payee name and amount exactly as the client wrote them. You never act and never see bank data."""


class Route(BaseModel):
    kind: Literal[KINDS]  # type: ignore[valid-type]
    payee_name: str | None = None
    amount: str | None = None
    item: str | None = None
    account: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


RULES: list[tuple[str, str]] = [
    (r"\b(double|twice|duplicate|charged two|дважды|двойн|дубл)", "double_charges"),
    (r"\b(cancel|unsubscribe|отмени|отпиши|отключи)\w*\b.*\b(subscri|membership|подписк|\d)|"
     r"^\s*(cancel|unsubscribe from|отмени(ть)?)\s+(my\s+)?[\w-]+\s*[.!]*$", "cancel"),
    (r"\b(leak|waste|subscriptions?\b.*(audit|check|review)|unused|утечк|лишн\w* подписк|подписки)", "find_leaks"),
    (r"\b(pay|оплати|заплати)\b.*\b(bills?|utilit|счет|счёт|счета|коммунал)", "pay_bills"),
    (r"\b(can i afford|afford|могу ли я (себе )?позволить|хватит ли)", "can_afford"),
    (r"\b(how much can i spend|safe to spend|spend today|сколько (я )?могу потратить)", "safe_to_spend"),
    (r"\b(cash gap|short before payday|enough until payday|разрыв|не хватит до зарплаты)", "cash_gap"),
    (r"\b(save up|saving for|save \$?\d[\d\s,.]*k?\b.*\bby|накопи|накопить|новая цель|goal)\b", "goal"),
    (r"\b(month review|month summary|last month|итоги месяца|прошлый месяц)", "month_review"),
    (r"\b(how accurate|forecast accura|точност|насколько точ)", "forecast_quality"),
    (r"\b(transfer|send|move|wire|переведи|перевести|перевод|отправь)\w*\b.*(\d{10,20}|safe account|"
     r"безопасн\w* сч|安全账户)", "transfer"),
    (r"\b(invest|buy|купи|инвест|вложи)\w*\b.*\$?\d.*\b(in|of|into|в)\s+[a-z]|\b(portfolio|портфель)\b", "invest"),
    (r"\b(budget|envelopes?|бюджет|конверт)", "budget"),
    (r"\b(permissions?|what can you do|allowed|разрешени|что ты можешь)", "permissions"),
    (r"^\s*(pay( for)?|оплати|заплати( за)?)\s+\S", "pay"),
    (r"^\s*(help|hi|hello|привет|помощь)\b", "help"),
    # Chinese: no word boundaries, so the patterns are anchored on the verbs themselves
    (r"(重复扣款|扣了两次|被扣两次)", "double_charges"),
    (r"取消.*(订阅|会员)", "cancel"),
    (r"(订阅.*(浪费|多余|检查)|浪费)", "find_leaks"),
    (r"(支付|缴纳|付).*(账单|水费|电费)", "pay_bills"),
    (r"(预算|信封)", "budget"),
    (r"^\s*(请)?(帮我)?(支付|付款|缴纳)\s*\S", "pay"),
    (r"(你能做什么|帮助|你好)", "help"),
]
_PAYEE_ZH = re.compile(r"^\s*(?:请)?(?:帮我)?(?:支付|付款|缴纳)\s*(?:一下)?\s*(.+?)\s*(?:的)?(?:订阅|会员)?\s*[。！!?？]*\s*$")
_PAYEE = re.compile(r"^\s*(?:please\s+)?(?:pay(?:\s+for)?|оплати|заплати(?:\s+за)?)\s+(?:the\s+|my\s+)?(.+?)"
                    r"(?:\s+(?:again|subscription|подписку))?\s*[.!?]*\s*$", re.I)
_CANCEL_PAYEE = re.compile(r"(?:[Cc]ancel|[Uu]nsubscribe from|[Оо]тмени(?:ть)?)\s+(?:my\s+)?(?:подписку\s+(?:на\s+)?)?"
                           r"([A-Z][A-Za-z0-9]+)")
_TICKER = re.compile(r"\b(?:in|of|into|в|акции)\s+([A-Z][A-Z0-9./]{0,20})\b")
_AMOUNT = re.compile(r"(?:\$\s*)?(\d(?:[\d\s ,]*\d)?(?:\.\d{1,2})?)(?:k\b|к\b|\s*тыс\w*)?"
                     r"\s*(?:\$|usd\b|dollars?\b|долл\w*|美元)?", re.I)


def rule_route(text: str) -> Route | None:
    low = text.lower().strip()
    for pattern, kind in RULES:
        if re.search(pattern, low):
            r = Route(kind=kind)
            if kind == "pay" and (m := _PAYEE.match(text) or _PAYEE_ZH.match(text)):
                r.payee_name = m.group(1).strip()
            if kind == "transfer":
                if m := re.search(r"(?<!\d)(\d{10,20})(?!\d)", text):
                    r.account = m.group(1)
                nums = [m for m in _AMOUNT.finditer(text) if m.group(1).strip() and len(re.sub(r"\D", "", m.group(1))) < 10]
                if nums:
                    raw = nums[0].group(0)
                    r.amount = nums[0].group(1).strip() + ("000" if re.search(r"(k|к|тыс)", raw, re.I) else "")
            if kind == "invest":
                if m := _TICKER.search(text):
                    r.item = m.group(1)
                if m := re.search(r"\$\s*(\d[\d,]*(?:\.\d{1,2})?)|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:\$|usd|dollars?|долл)",
                                  text, re.I):
                    r.amount = (m.group(1) or m.group(2)).strip()
            if kind == "goal":
                nums = [m for m in _AMOUNT.finditer(text) if m.group(1).strip()]
                if nums:
                    raw = nums[0].group(0)
                    r.amount = nums[0].group(1).strip() + ("000" if re.search(r"(k|к|тыс)", raw, re.I) else "")
            if kind == "cancel":
                nums = [m for m in _AMOUNT.finditer(text) if m.group(1).strip()
                        and not re.search(r"(card|карт\w*|\*)\s*$", text[:m.start()], re.I)]
                r.amount = nums[-1].group(1).strip() if nums else None
                if m := _CANCEL_PAYEE.search(text):
                    r.payee_name = m.group(1)
            if kind == "can_afford":
                nums = [m for m in _AMOUNT.finditer(text) if m.group(1).strip()]
                if nums:
                    raw = nums[-1].group(0)
                    r.amount = nums[-1].group(1).strip() + ("000" if re.search(r"(k|к|тыс)", raw, re.I)
                                                             and "." not in nums[-1].group(1) else "")
                r.item = re.sub(r"(?i).*\b(afford|позволить)\b\s*(a |an |the )?", "", text).split(" for ")[0]\
                    .split(" за ")[0].strip(" ?.") or None
            return r
    return None


class Dispatcher:
    def __init__(self, gateway: LLMGateway):
        self.gw = gateway

    def route(self, text: str, task_id: str) -> tuple[Route, int]:
        r = rule_route(text)
        if r is not None:
            return r, 0
        res = self.gw.call("dispatcher", SYSTEM, json.dumps({"message": text}, ensure_ascii=False), level=1,
                           task_id=task_id, schema=Route, max_tokens=200, min_confidence=0.6)
        return Route.model_validate(res.parsed), res.level


def stub_responder(req: dict) -> str:
    """Stand-in for the small model: a looser keyword match, otherwise "unknown"."""
    text = json.loads(req["user"])["message"].lower()
    for words, kind in ((("subscri", "подпис"), "find_leaks"), (("bill", "счет", "счёт"), "pay_bills"),
                        (("spend", "потрат"), "safe_to_spend"), (("money", "деньг", "salary", "зарплат"), "cash_gap")):
        if any(w in text for w in words):
            return json.dumps({"kind": kind, "confidence": 0.7})
    return json.dumps({"kind": "unknown", "confidence": 0.9})
