"""Scam-call intervention ("safe account", 安全账户): a ladder of steps, all decided by the kernel.

A client on the phone with a fake bank officer asks to move everything to a "safe account".
Every value comes from the client, so provenance checks pass; this is the instructed-victim case.
The kernel scores deterministic signals (an ongoing call from an unknown number reported by the
telecom, scam wording, a new payee, the share of all money, savings moved to the current account
just before, related suspicious events, payee risk on the transfer graph) and picks a level:

  1. explain the scheme on the card and ask to type the amount;
  2. pause: a cooling-off period, three questions and the PIN before the card can be signed;
  3. reversible delay: the signed payment waits 24 hours and the trusted person is told;
  4. live analyst: the payment waits for a human at the bank.

Clients under elevated protection (elderly, minors) start at level 2. Blocking the account or
calling the police is never done by the kernel or an agent: only the analyst can, outside the
system. A second, duress PIN makes the app show an ordinary receipt while the payment goes to an
analyst and the trusted person gets a silent alert.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, Field

from .cards import Risk
from .mandate import MONEY_ACTIONS, Action, new_id

if TYPE_CHECKING:
    from .core import Kernel
    from .intent import Intent
    from .passport import Passport

SCAM_WORDS = re.compile(
    r"safe account|secure account|safety account|protected account|security (service|department|officer)|"
    r"bank security|central bank|police|investigator|your money is at risk|don.?t tell|keep (it|this) secret|"
    r"безопасн\w* сч[её]т|защищ\w* сч[её]т|служб\w* безопасности|центробанк|центральн\w* банк|полици|следовател|"
    r"никому не говор|安全账户|公安|银行安全", re.I)
PAUSE = timedelta(minutes=15)
DELAY = timedelta(hours=24)
LEVELS = {1: "explain", 2: "pause and questions", 3: "reversible delay, trusted person told", 4: "live analyst"}
EXPLAIN = ("Banks, the police and the central bank never ask anyone to move money to a \"safe account\". "
           "If someone on the phone told you to do this, it is a scam: hang up and call the number on the back "
           "of your card yourself.")
QUESTIONS = {
    "on_call": "Is someone on the phone with you now, or did someone call you about this transfer?",
    "pressure": "Did someone say your money is at risk, or ask you to keep this secret from the bank or family?",
    "given_account": "Did someone else give you this account number?",
}


class CallSignal(BaseModel):
    """What the telecom tells the bank: an ongoing call and whether the number looks spoofed."""
    number: str
    caller_id: str | None = None
    started: datetime
    known_contact: bool = False
    spoofed: bool = False
    ended: datetime | None = None


class Assessment(BaseModel):
    intent_id: str
    level: int
    score: int
    signals: list[str]
    elevated: bool = False
    wait_until: datetime | None = None
    answers: dict[str, bool] = {}
    duress: bool = False
    cleared: bool = False

    def card(self) -> dict[str, Any]:
        out: dict[str, Any] = {"level": self.level, "name": LEVELS[self.level], "signals": self.signals,
                               "explain": EXPLAIN, "elevated": self.elevated}
        if self.level >= 2:
            out |= {"questions": QUESTIONS, "pin": True,
                    "wait_until": self.wait_until.isoformat() if self.wait_until else None}
        if self.level >= 3:
            out["after"] = ("after you sign, the payment waits 24 hours and can be cancelled; your trusted person "
                            "is told" if self.level == 3 else "after you sign, the payment waits for a bank analyst")
        return out


class Hold(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hold"))
    client_id: str
    intent: Any
    passport_id: str
    level: int
    created: datetime
    release_at: datetime | None          # None: waits for an analyst
    status: str = "held"                 # held | released | cancelled | rejected
    duress: bool = False
    case: dict | None = None
    decided_by: str | None = None
    note: str = ""


class Duress:
    """Normal and duress PINs, compared in constant time; the duress PIN behaves like the normal one."""

    MAX_FAILED = 5

    def __init__(self, normal: str, duress: str):
        if normal == duress:
            raise ValueError("duress PIN must differ from the normal PIN")
        self._salt = secrets.token_bytes(16)
        self._normal, self._duress = self._h(normal), self._h(duress)
        self.failed = 0

    def _h(self, pin: str) -> bytes:
        return hashlib.scrypt(pin.encode(), salt=self._salt, n=2 ** 12, r=8, p=1, dklen=32)

    def check(self, pin: str | None) -> str:
        if self.failed >= self.MAX_FAILED:
            return "locked"
        h = self._h(pin or "")
        is_normal, is_duress = hmac.compare_digest(h, self._normal), hmac.compare_digest(h, self._duress)
        if is_duress:
            return "duress"
        if is_normal:
            self.failed = 0
            return "normal"
        self.failed += 1
        return "invalid"


class InterventionDesk:
    def __init__(self, kernel: "Kernel", total_funds: Callable[[str], int], recent_own_inflow: Callable[[str], int],
                 notify: Callable[[str, str, bool], None] | None = None):
        self.k = kernel
        self.total_funds, self.recent_own_inflow = total_funds, recent_own_inflow
        self.notify = notify or (lambda client_id, text, silent: None)
        self.calls: dict[str, CallSignal] = {}
        self.assessments: dict[str, Assessment] = {}
        self.holds: dict[str, Hold] = {}
        self.pins: dict[str, Duress] = {}
        self.graph_risk: Callable[[Any], tuple[int, list[str]]] | None = None

    # ------------------------------------------------------------------ telecom

    def call_started(self, client_id: str, signal: CallSignal) -> None:
        self.calls[client_id] = signal
        self.k.memory.record(ts=signal.started, client_id=client_id, kind="call",
                             summary=f"call from {signal.caller_id or signal.number}"
                                     + (" (number looks spoofed)" if signal.spoofed else ""), source="telecom")
        self.k.journal.append("telecom", "call.started", {"client": client_id, "number": signal.number,
                                                          "spoofed": signal.spoofed})

    def call_ended(self, client_id: str) -> None:
        if c := self.calls.get(client_id):
            c.ended = self.k.clock.now()

    def on_call(self, client_id: str) -> CallSignal | None:
        c = self.calls.get(client_id)
        return c if c and c.ended is None and not c.known_contact else None

    # ------------------------------------------------------------------ assessment

    def assess(self, intent: "Intent", payee_known: bool, request: str = "") -> Assessment:
        cid, now = intent.client_id, self.k.clock.now()
        score, signals = 0, []
        if call := self.on_call(cid):
            score += 2
            signals.append(f"you are on a call with {call.caller_id or 'an unknown number'} that started at "
                           f"{call.started:%H:%M}")
            if call.spoofed:
                score += 1
                signals.append("the telecom marks the caller's number as spoofed")
        if words := sorted({m.group(0).lower() for m in SCAM_WORDS.finditer(request)}):
            score += 2
            signals.append("the request uses scam wording: " + ", ".join(words[:3]))
        if not payee_known:
            score += 1
            signals.append("first transfer to this recipient")
        amount, total = intent.val("amount") or 0, self.total_funds(cid)
        if total and amount >= 0.5 * total:
            score += 1 + (amount >= 0.8 * total)
            signals.append(f"{round(amount / total * 100)}% of all your money")
        if (moved := self.recent_own_inflow(cid)) > 0:
            score += 1
            signals.append(f"{moved // 100:,} ₽ moved from savings to the current account in the last 24 hours"
                           .replace(",", " "))
        week = [e for e in self.k.memory.for_client(cid) if e.kind == "call" and e.ts >= now - timedelta(days=7)]
        if week and not self.on_call(cid):
            score += 1
            signals.append(f"a suspicious call on {week[-1].ts:%d %b}")
        if self.graph_risk:
            pts, why = self.graph_risk(intent)
            score += pts
            signals += why
        elevated = self.k.trust.elevated_protection
        level = 0 if score < 2 else 1 if score < 4 else 2 if score < 6 else 3 if score < 8 else 4
        if elevated and level:
            level = max(level, 2)
        a = Assessment(intent_id=intent.id, level=level, score=score, signals=signals, elevated=elevated,
                       wait_until=now + PAUSE if level >= 2 else None)
        return a

    def check(self, ctx):
        """Pipeline risk check for money leaving the client."""
        from .pipeline import StepResult, Verdict
        i = ctx.intent
        if i.action not in MONEY_ACTIONS or i.action == Action.TRANSFER_OWN:
            return StepResult(step="")
        prev = self.assessments.get(i.id)
        if prev and prev.cleared:
            return StepResult(step="", data={"intervention": prev.level, "cleared": True})
        known = bool(ctx.payee and ctx.payee.known_to_client)
        a = self.assess(i, known, self.k.provenance.message(i.task_id))
        if prev:
            a.level, a.answers, a.duress = max(a.level, prev.level), prev.answers, prev.duress
            a.wait_until = prev.wait_until or a.wait_until
        if ctx.confirmed and a.level >= 3:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=[f"possible scam call, level {a.level}: the payment must wait in a hold"])
        if not a.level:
            return StepResult(step="")
        self.assessments[i.id] = a
        self.k.journal.append("kernel", "intervention.assessed", {"intent": i.id, "level": a.level, "score": a.score,
                                                                  "signals": a.signals})
        return StepResult(step="", verdict=Verdict.ASK, risk=Risk.HIGH,
                          reasons=[f"possible scam call, level {a.level} ({LEVELS[a.level]})"] + a.signals,
                          data={"intervention": a.level})

    # ------------------------------------------------------------------ confirmation

    def before_sign(self, intent: "Intent", answers: dict[str, bool] | None, pin: str | None) -> Assessment | None:
        """Called by Kernel.confirm. Raises ValueError with a client-facing reason if the card cannot be signed yet."""
        a = self.assessments.get(intent.id)
        if a is None or a.level < 2:
            return a
        now = self.k.clock.now()
        if a.wait_until and now < a.wait_until:
            raise ValueError(f"pause: this card can be signed after {a.wait_until:%H:%M}. If someone is on the "
                             "phone with you, hang up and call the bank yourself")
        if not answers or set(answers) != set(QUESTIONS):
            raise ValueError("answer the three questions first")
        a.answers = answers
        if any(answers.values()) and a.level < 3:
            a.level = 3
            self.k.journal.append("client", "intervention.escalated", {"intent": intent.id, "level": 3,
                                                                       "answers": answers})
        pins = self.pins.get(intent.client_id)
        res = pins.check(pin) if pins else "normal"
        if res in ("invalid", "locked"):
            raise ValueError("wrong PIN" if res == "invalid" else "PIN locked, call the bank")
        if res == "duress":
            a.duress, a.level = True, 4
        self.k.journal.append("client", "intervention.answered", {"intent": intent.id, "level": a.level,
                                                                  "answers": answers})
        return a

    def hold(self, a: Assessment, intent: "Intent", passport: "Passport") -> Hold:
        now = self.k.clock.now()
        h = Hold(client_id=intent.client_id, intent=intent, passport_id=passport.id, level=a.level, created=now,
                 release_at=now + DELAY if a.level == 3 else None, duress=a.duress)
        if a.level == 4:
            h.case = {"id": new_id("case"), "title": "Possible scam call: transfer waits for an analyst",
                      "signals": a.signals, "answers": a.answers, "duress": a.duress,
                      "human_only": ["block the account", "contact the police", "call the client back"]}
        self.holds[h.id] = h
        amount = intent.val("amount") or 0
        if a.duress:
            self.notify(intent.client_id, f"SILENT ALERT: a transfer of {amount // 100:,} ₽ may be made under duress. "
                                          "It is frozen. Please call your relative.".replace(",", " "), True)
        elif a.level >= 3:
            wait = "is holding it for 24 hours" if a.level == 3 else "has stopped it until an analyst talks to them"
            self.notify(intent.client_id, f"Your relative asked to transfer {amount // 100:,} ₽ to a new recipient "
                                          f"during a phone call. The bank {wait}. Please call them."
                        .replace(",", " "), False)
        self.k.journal.append("kernel", "intervention.held", {"hold": h.id, "intent": intent.id, "level": a.level,
                                                              "release_at": h.release_at.isoformat() if h.release_at
                                                              else None, "duress": a.duress})
        return h

    # ------------------------------------------------------------------ after the hold

    def cancel(self, hold_id: str, by: str) -> Hold:
        h = self.holds[hold_id]
        if h.status == "held":
            h.status, h.decided_by = "cancelled", by
            self.k.journal.append(by, "intervention.cancelled", {"hold": h.id})
        return h

    def analyst(self, hold_id: str, approve: bool, analyst: str, note: str = "") -> Hold:
        """A human at the bank decides. Only people may block accounts or call the police."""
        h = self.holds[hold_id]
        if h.status != "held":
            return h
        h.decided_by, h.note = f"analyst:{analyst}", note
        self.k.journal.append("analyst", "intervention.analyst_decided", {"hold": h.id, "approve": approve,
                                                                          "analyst": analyst, "note": note})
        if approve:
            h.release_at = self.k.clock.now()
        else:
            h.status = "rejected"
        return h

    def due(self) -> list[Hold]:
        now = self.k.clock.now()
        return [h for h in self.holds.values() if h.status == "held" and h.release_at and h.release_at <= now]

    def release(self, h: Hold):
        h.status = "released"
        self.assessments[h.intent.id].cleared = True
        self.k.journal.append("kernel", "intervention.released", {"hold": h.id})
        from .passport import Role
        s = self.k.passports.issue(Role.PLANNER, "kernel-code", 0, "release held transfer", h.client_id,
                                   h.intent.task_id)
        return self.k.pipeline.run(h.intent, s.passport, confirmed=True)
