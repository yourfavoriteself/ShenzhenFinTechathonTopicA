"""Guardian service: the chat the client talks to.

It routes each message (dispatcher), opens a task with the client's own words, prepares facts from
the bank with references, lets agents propose intents and hands every intent to the kernel. Cards,
receipts and refusals come back as chat items. Everything with money in it is computed by the
kernel; models only classify, extract, plan by reference and word the answer.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from typing import Any, Callable

from pydantic import BaseModel, Field

from agents import dispatcher as D, planner as P, reader as R
from agents.base import claim, propose, session
from data.generator import World, build_world
from kernel import budget as B
from kernel.analytics import Usage, double_charges, leaks, recurring
from kernel.cards import describe
from kernel.core import ConfirmationError
from kernel.critic import Critic, stub_responder as critic_stub
from kernel.documents import DocCheck, Doc, DocumentDesk
from kernel.executors import TX_REF
from kernel.intent import IntentProposal
from kernel.llm_gateway import LLMFailure, LLMGateway
from kernel.mandate import Action, new_id
from kernel.money import kop, rub
from kernel.passport import Role, SignedRequest
from kernel.pipeline import Decision, Outcome
from kernel.refcheck import guarded
from kernel.statements import Category
from kernel.templates import Template, TemplateStore
from sandbox.bank.wiring import connect, open_envelopes

MAIN_CARD = "card_1234"
MAIN_ACCOUNT, SAVINGS_ACCOUNT = "acc_main", "acc_save"
EXPLAIN_SYSTEM = """You word short answers for a bank client. Rewrite the draft in plain, friendly English,
at most three sentences. Use only numbers that appear in the facts; do not add advice about credit."""

HELP = ("I can pay a merchant (\"Pay for Melodify\"), pay your bills from the inbox, find leaks in your "
        "subscriptions, check for double charges, build a budget, tell you how much you can spend today, "
        "check whether you can afford something, and show what I am allowed to do.")


class Item(BaseModel):
    id: str = Field(default_factory=lambda: new_id("it"))
    seq: int = 0
    ts: datetime
    type: str            # user | text | card | receipt | denied | error | budget | safe | afford | gap | leaks
    text: str = ""       # | double | bills | case | options | permissions | notice
    data: dict[str, Any] = {}
    task_id: str | None = None


class CardMeta(BaseModel):
    pending_id: str
    task_id: str
    kind: str
    template: dict | None = None
    doc_id: str | None = None


class OptionMeta(BaseModel):
    id: str = Field(default_factory=lambda: new_id("opt"))
    kind: str
    title: str
    action: dict = {}
    amount: int = 0
    used: bool = False


class GuardianService:
    def __init__(self, world: World | None = None, gateway: LLMGateway | None = None, *, seed: int = 22,
                 explain: bool = True):
        self.world = world or build_world(seed)
        self.bank, self.clock, self.merchants = self.world.bank, self.world.clock, self.world.merchants
        self.client = self.world.profile.client_id
        self.k = connect(self.bank, self.merchants, seed="demo")
        self.gw = gateway or LLMGateway()
        for role, fn in (("dispatcher", D.stub_responder), ("planner", P.stub_responder),
                         ("reader", R.stub_responder), ("critic", critic_stub), ("explainer", _explain_stub)):
            self.gw.register_stub(role, fn)
        self.critic = Critic(self.k, self.gw)
        self.k.pipeline.critic = self.critic
        self.desk = DocumentDesk(self.k, self.merchants, self.k.memory)
        self.templates = TemplateStore()
        self.dispatcher, self.planner, self.reader = D.Dispatcher(self.gw), P.Planner(self.gw), R.Reader(self.gw)
        self.explain = explain
        self.items: list[Item] = []
        self.version = 0
        self.lock = threading.RLock()
        self.cards: dict[str, CardMeta] = {}
        self.options: dict[str, OptionMeta] = {}
        self.bill_checks: dict[str, DocCheck] = {}
        self.paid_docs: set[str] = set()
        self.plan: B.BudgetPlan | None = None
        for doc in self.inbox():
            self.desk.screen(self.client, doc)
        self._welcome()

    # ================================================================== public API (client side)

    def chat(self, text: str) -> list[Item]:
        with self.lock:
            start = len(self.items)
            self._emit("user", text)
            task = self.k.open_task(self.client, text)
            try:
                route, level = self.dispatcher.route(text, task.task_id)
            except LLMFailure:
                route, level = D.Route(kind="unknown"), 1
            task.kind = route.kind
            handler: Callable[..., None] = getattr(self, f"_on_{route.kind}")
            try:
                handler(task, route, level)
            except LLMFailure as e:
                self._emit("error", f"The model could not complete this task: {e}", task_id=task.task_id)
            return self.items[start:]

    def confirm(self, pending_id: str, alg: str, key: str, sig: str, *, standing: bool = False,
                typed_amount: int | None = None) -> list[Item]:
        with self.lock:
            start = len(self.items)
            meta = self.cards.get(pending_id)
            try:
                d = self.k.confirm(pending_id, alg, key, sig, standing=standing, typed_amount=typed_amount)
            except ConfirmationError as e:
                self._emit("error", f"Not confirmed: {e}", data={"pending_id": pending_id})
                return self.items[start:]
            self._decision(d, meta, confirmed=True, standing=standing)
            return self.items[start:]

    def decline(self, pending_id: str) -> list[Item]:
        with self.lock:
            self.k.decline(pending_id)
            meta = self.cards.pop(pending_id, None)
            return [self._emit("notice", "Cancelled. Nothing was paid.", data={"pending_id": pending_id},
                               task_id=meta.task_id if meta else None)]

    def revoke(self, mandate_id: str) -> list[Item]:
        with self.lock:
            ids = self.k.revoke(mandate_id)
            for t in self.templates.for_client(self.client):
                if t.mandate_id in ids:
                    t.mandate_id = None
            return [self._emit("notice", "Permission revoked. From now on I will ask you again.",
                               data={"revoked": ids})]

    def register_device(self, alg: str, public_key: str) -> None:
        with self.lock:
            self.k.mandates.register_device(self.client, public_key, alg)
            self.k.journal.append("client", "device.registered", {"alg": alg})

    def choose(self, option_id: str) -> list[Item]:
        with self.lock:
            start = len(self.items)
            o = self.options.get(option_id)
            if o is None or o.used:
                self._emit("error", "This option is no longer available.")
                return self.items[start:]
            o.used = True
            self._emit("user", o.title)
            getattr(self, f"_opt_{o.kind}")(o)
            return self.items[start:]

    def apply_budget(self) -> list[Item]:
        with self.lock:
            plan = self._plan()
            events = self._events()
            payday = self._payday()
            due = {env: -sum(e.amount for e in events if e.date < payday and e.envelope == env and e.amount < 0)
                   for env in B.ENV}
            funding = open_envelopes(self.bank, self.client, MAIN_ACCOUNT, plan, due, payday)
            self.k.journal.append("client", "budget.applied", {"funding": funding})
            rows = [{"id": e, "name": B.ENV[e].name, "amount": rub(v)} for e, v in funding.items()]
            return [self._emit("notice", f"Envelopes are open until payday {payday:%d %b}. Bills are funded first.",
                               data={"funding": rows})]

    def advance(self, days: int) -> list[Item]:
        """Demo time: move the sandbox bank forward (salary, merchant charges, dispute decisions)."""
        with self.lock:
            start = len(self.items)
            before, t0 = {d.id for d in self.inbox()}, self.clock.now()
            self.bank.advance(days=days)
            self.plan = None
            for doc in self.inbox():
                if doc.id not in before:
                    self.desk.screen(self.client, doc)
            now = self.clock.now()
            self.k.journal.append("operator", "clock.advanced", {"days": days, "now": now.isoformat()})
            self._emit("notice", f"Demo time moved forward {days} days: it is now {now:%d %b %Y, %H:%M}.")
            paid_in = [t for t in self.bank.transactions(self.client, since=t0) if t.category == Category.INCOME]
            if paid_in and self._envelopes():
                self.apply_budget()
                self.items[-1].text = "Income arrived: " + self.items[-1].text[0].lower() + self.items[-1].text[1:]
            return self.items[start:]

    def state(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock.now()
            envs = [{"id": e.id, "name": e.name, "balance": e.balance, "label": rub(e.balance)}
                    for e in self.bank.envelopes.values() if e.client_id == self.client]
            accounts = [{"id": a.id, "name": a.name, "balance": a.balance, "label": rub(a.balance)}
                        for a in self.bank.accounts.values() if a.client_id == self.client]
            stt = self._safe()
            fc = self._forecast()
            return {
                "client": self.world.profile.name, "now": now.isoformat(), "version": self.version,
                "accounts": accounts, "envelopes": envs,
                "safe_to_spend": {"per_day": rub(stt.per_day), "days": stt.days_to_payday,
                                  "payday": stt.payday.isoformat(), "calc": stt.calc},
                "gap": [{"date": g.date.isoformat(), "event": g.event, "shortfall": rub(g.shortfall)}
                        for g in fc.gaps[:1]],
                "mandates": self.mandates(),
                "inbox": [{"id": d.id, "subject": d.subject, "sender": d.sender, "kind": d.kind,
                           "date": d.ts.date().isoformat(), "flagged": bool(self.desk.screened.get(d.id) and
                                                                         self.desk.screened[d.id].events),
                           "paid": d.id in self.paid_docs} for d in self.inbox()],
                "templates": [{"payee": t.payee_name, "amount": rub(t.amount), "uses": t.uses}
                              for t in self.templates.for_client(self.client)],
                "trust": {c.value: self.k.trust.level(c) for c in self.k.trust.categories},
                "llm": self.gw.report(), "critic": {"calls": self.critic.calls, "cache_hits": self.critic.cache_hits},
                "cases": len(self.desk.cases),
            }

    def mandates(self) -> list[dict[str, Any]]:
        out = []
        for sm in self.k.mandates.active(self.client):
            m = sm.mandate
            names = {p: self._name(p) for p in (m.payment.allowed_payees if m.payment else [])}
            label = self.bank.label(self.client, m.funding_source) if m.funding_source else "-"
            can, cannot = describe(m, names, label)
            st = self.k.mandates.state(m.id)
            out.append({"id": m.id, "can": can, "cannot": cannot, "spent": rub(st.spent if st else 0),
                        "uses": len(st.uses) if st else 0, "exp": m.exp.isoformat(), "stage": int(m.stage),
                        "hash": sm.mandate_hash})
        return out

    def inbox(self) -> list[Doc]:
        now = self.clock.now()
        return [Doc(**d.model_dump()) for d in sorted(self.world.documents, key=lambda d: d.ts) if d.ts <= now]

    def since(self, seq: int) -> list[Item]:
        return [i for i in self.items if i.seq > seq]

    # ================================================================== scenarios

    def _on_help(self, task, route, level):
        self._emit("text", HELP, task_id=task.task_id)

    def _on_unknown(self, task, route, level):
        self._emit("text", "I did not understand that. " + HELP, task_id=task.task_id)

    def _on_permissions(self, task, route, level):
        ms = self.mandates()
        text = (f"You have {len(ms)} standing permission{'s' if len(ms) != 1 else ''}. Everything else I ask "
                "you about first." if ms else "You have not given me any standing permissions: I ask before every "
                                              "payment.")
        self._emit("permissions", text, data={"mandates": ms}, task_id=task.task_id)

    # ---------------------------------------------------------------- C1: pay a merchant

    def _on_pay(self, task, route, level):
        name = route.payee_name or ""
        tpl = self.templates.match(self.client, Action.PAY, task.request)
        if tpl is not None:
            return self._pay_from_template(task, tpl)
        facts = self._pay_facts(task, name)
        if not facts["payees"]:
            self._emit("text", f"I could not find \"{name}\" in your payments or in the bank's merchant directory. "
                               "I will not pay a payee I cannot verify.", task_id=task.task_id)
            return
        proposals, note = self.planner.plan(task.request, facts, task.task_id)
        if not proposals:
            self._emit("text", note or "I could not plan this payment.", task_id=task.task_id)
            return
        s = session(self.k, self.gw, Role.PLANNER, self.planner.last_level or P.Planner.LEVEL, P.SYSTEM,
                    self.client, task.task_id)
        for p in proposals:
            d = propose(self.k, s, p)
            meta = CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="pay",
                            template={"payee": p.payee.value if p.payee else None,
                                      "amount": p.amount.value if p.amount else None,
                                      "funding_source": p.funding_source.value if p.funding_source else None,
                                      "keywords": _keywords(name)})
            self._decision(d, meta)

    def _pay_facts(self, task, name: str) -> dict[str, Any]:
        words = set(_keywords(name))
        seen: dict[str, dict] = {}
        for m in self.merchants.by_token.values():
            names = [m.name] + m.aliases
            if any(set(_keywords(n)) & words for n in names) and m.kind != "employer":
                seen[m.token] = {"token": m.token, "name": m.name, "kind": m.kind, "category": m.category}
        for t in self.bank.transactions(self.client):
            if t.counterparty_name and set(_keywords(t.counterparty_name)) & words and t.counterparty not in seen:
                seen[t.counterparty] = {"token": t.counterparty, "name": t.counterparty_name, "kind": "person",
                                        "category": t.category}
        payees = []
        for c in seen.values():
            info = self.k.directory.payee(self.client, c["token"])
            if info is None:
                continue
            entry = {"name": info.name, "kind": info.kind, "verified": info.verified,
                     "paid_before": info.known_to_client,
                     "payee": {"value": info.token, "ref": self.k.bank_value(f"bank:payee:{info.token}", info.token,
                                                                             "bank's merchant directory")}}
            if info.usual_amount:
                entry["usual_amount"] = {"value": info.usual_amount, "display": rub(info.usual_amount),
                                         "ref": self.k.bank_value(f"bank:usual:{info.token}", info.usual_amount,
                                                                  "median of your last payments")}
            entry["category_envelope"] = B.ENV_OF.get(c["category"])
            payees.append(entry)
        env_wanted = {p["category_envelope"] for p in payees}
        return {"client_message_ref": f"client:{task.task_id}", "payees": payees,
                "funding_sources": self._sources(env_wanted)}

    def _sources(self, preferred: set[str | None]) -> list[dict]:
        out = []
        for e in self.bank.envelopes.values():
            if e.client_id == self.client:
                out.append({"name": f'envelope "{e.name}"', "available": rub(e.balance),
                            "matches_category": e.id in preferred,
                            "source": {"value": e.id, "ref": self.k.bank_value(f"bank:src:{e.id}", e.id,
                                                                               "your budget envelopes")}})
        out.sort(key=lambda s: not s["matches_category"])
        out.append({"name": "card *1234", "available": rub(self.bank.available(self.client, MAIN_CARD) or 0),
                    "matches_category": False,
                    "source": {"value": MAIN_CARD, "ref": self.k.bank_value(f"bank:src:{MAIN_CARD}", MAIN_CARD,
                                                                            "your cards")}})
        return out

    def _pay_from_template(self, task, tpl: Template):
        """Level 0: the saved template supplies the payment; no model is called."""
        src = tpl.funding_source
        if src not in self.bank.envelopes and self._envelopes() and B.ENV_OF.get(
                self.merchants.get(tpl.payee).category if self.merchants.get(tpl.payee) else None):
            src = B.ENV_OF[self.merchants.get(tpl.payee).category]
        p = IntentProposal(
            action=Action.PAY,
            payee=claim({"value": tpl.payee, "ref": self.k.bank_value(f"bank:template:{tpl.id}:payee", tpl.payee,
                                                                     "your saved payment template")}),
            amount=claim({"value": tpl.amount, "ref": self.k.bank_value(f"bank:template:{tpl.id}:amount", tpl.amount,
                                                                       "your saved payment template")}),
            funding_source=claim({"value": src, "ref": self.k.bank_value(f"bank:src:{src}", src, "your accounts")}),
            memo=f"{tpl.payee_name} (template)")
        tpl.uses += 1
        s = session(self.k, self.gw, Role.DISPATCHER, 0, D.SYSTEM, self.client, task.task_id)
        d = propose(self.k, s, p)
        self._decision(d, CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="pay",
                                   template={"payee": tpl.payee, "amount": tpl.amount, "funding_source": src,
                                             "keywords": tpl.keywords}),
                       template_used=tpl.id)

    # ---------------------------------------------------------------- C5 / C6: bills from the inbox

    def _on_pay_bills(self, task, route, level):
        bills = [d for d in self.inbox() if d.kind == "bill" and d.id not in self.paid_docs]
        if not bills:
            self._emit("text", "There are no unpaid bills in your inbox.", task_id=task.task_id)
            return
        rs = session(self.k, self.gw, Role.READER, R.Reader.LEVEL, R.SYSTEM, self.client, task.task_id)
        ds = session(self.k, self.gw, Role.DISPATCHER, 0, D.SYSTEM, self.client, task.task_id)
        rows = []
        checks = []
        for doc in bills:
            ex, req = self.reader.read(doc, task.task_id, rs)
            self.k.passports.authenticate(SignedRequest.model_validate(req))
            chk = self.desk.verify(self.client, doc, ex)
            self.bill_checks[doc.id] = chk
            checks.append((doc, chk))
            rows.append({"doc": doc.id, "subject": doc.subject, "sender": doc.sender_domain,
                         "amount": rub(chk.amount) if chk.amount else "-", "due": str(chk.due or "-"),
                         "verdict": chk.verdict, "fields": [f.model_dump(mode="json") for f in chk.fields]})
        ok = sum(1 for _, c in checks if c.verdict == "verified")
        self._emit("bills", f"I read {len(bills)} bills: {ok} check out against the bank's records"
                            f"{', ' + str(len(bills) - ok) + ' do not' if ok < len(bills) else ''}.",
                   data={"bills": rows}, task_id=task.task_id)
        for doc, chk in checks:
            if chk.verdict != "verified":
                self._emit("case", f"I will not pay \"{doc.subject}\". It looks like a fake bill.",
                           data={"reasons": chk.reasons, "case": chk.case.model_dump(mode="json") if chk.case else None},
                           task_id=task.task_id)
                continue
            src = self._bills_source()
            p = IntentProposal(action=Action.PAY, payee=claim({"value": chk.payee, "ref": chk.refs["payee"]}),
                               amount=claim({"value": chk.amount, "ref": chk.refs["amount"]}),
                               funding_source=claim({"value": src, "ref": self.k.bank_value(
                                   f"bank:src:{src}", src, "your accounts")}),
                               memo=doc.subject[:80], references=[chk.refs["payee"], chk.refs["amount"]])
            d = propose(self.k, ds, p)
            self._decision(d, CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="bill",
                                       doc_id=doc.id))

    def _bills_source(self) -> str:
        return "env_bills" if "env_bills" in self.bank.envelopes else MAIN_ACCOUNT

    # ---------------------------------------------------------------- C3: leaks

    def _on_find_leaks(self, task, route, level):
        lk = self._leaks()
        inbox = self.inbox()
        rows = []
        for x in lk.leaks:
            docs = [d.id for d in inbox if x.name.lower() in f"{d.subject} {d.body}".lower()
                    and not self.desk.screened[d.id].events]
            row = {"kind": x.kind, "name": x.name, "merchant": x.merchant, "monthly": rub(x.monthly),
                   "yearly": rub(x.yearly), "counted": x.counted, "note": x.note, "calc": x.calc,
                   "evidence": x.evidence + docs, "synthetic_usage": x.synthetic_usage}
            if x.counted:
                o = self._option("block", f"Block future {x.name} charges ({rub(x.monthly)} a month)",
                                 {"merchant": x.merchant, "card_id": x.card_id}, x.monthly)
                row["option"] = o.id
            rows.append(row)
        draft = (f"I found {len(lk.leaks)} problems in your subscriptions. Removing the wasted ones saves "
                 f"{rub(lk.monthly_total)} a month, {rub(lk.yearly_total)} a year.")
        facts = {"count": len(lk.leaks), "monthly": rub(lk.monthly_total), "yearly": rub(lk.yearly_total)}
        text, rc = self._say(task, facts, draft)
        self._emit("leaks", text, data={"leaks": rows, "calc": lk.calc, "refcheck": rc,
                                        "note": "Usage data is synthetic in this demo."}, task_id=task.task_id)

    # ---------------------------------------------------------------- C4: double charges

    def _on_double_charges(self, task, route, level):
        txs = self.bank.transactions(self.client)
        found = [dc for dc in double_charges(txs) if self.bank.tx(dc.second).status == "posted"]
        if not found:
            self._emit("text", "I found no double charges in your statement.", task_id=task.task_id)
            return
        s = session(self.k, self.gw, Role.DISPATCHER, 0, D.SYSTEM, self.client, task.task_id)
        for dc in found:
            a, b = self.bank.tx(dc.first), self.bank.tx(dc.second)
            evidence = {"merchant": dc.name, "amount": rub(dc.amount), "first": {"id": a.id, "ts": a.ts.isoformat()},
                        "second": {"id": b.id, "ts": b.ts.isoformat()}, "seconds_apart": dc.seconds_apart,
                        "card": f"*{dc.card_id[-4:]}" if dc.card_id else "-"}
            self._emit("double", f"{dc.name} charged you {rub(dc.amount)} twice, {dc.seconds_apart} seconds apart "
                                 f"on {a.ts:%d %b}. I prepared a dispute for the second charge.",
                       data=evidence, task_id=task.task_id)
            refs = [self.k.bank_value(f"{TX_REF}{b.id}", b.id, "bank statement"),
                    self.k.bank_value(f"{TX_REF}{a.id}", a.id, "bank statement")]
            p = IntentProposal(
                action=Action.DISPUTE,
                payee=claim({"value": dc.merchant, "ref": self.k.bank_value(f"bank:payee:{dc.merchant}", dc.merchant,
                                                                           "bank statement")}),
                amount=claim({"value": dc.amount, "ref": self.k.bank_value(f"bank:tx:{b.id}:amount", dc.amount,
                                                                          "bank statement")}),
                memo=f"duplicate charge: {a.id} and {b.id}, {dc.seconds_apart}s apart", references=refs)
            d = propose(self.k, s, p)
            self._decision(d, CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="dispute"))

    # ---------------------------------------------------------------- C8: budget, safe to spend, afford, gap

    def _on_budget(self, task, route, level):
        plan = self._plan()
        rows = [{"id": e.id, "name": e.name, "kind": e.kind, "planned": rub(e.planned), "average": rub(e.average),
                 "seasonal": round(e.seasonal_factor, 2)} for e in plan.envelopes]
        mandatory = sum(e.planned for e in plan.envelopes if e.kind in B.PROTECTED)
        flexible = sum(e.planned for e in plan.envelopes if e.kind == "flexible")
        draft = (f"Budget for {plan.month} from your last {len(plan.based_on)} months: income {rub(plan.income)}, "
                 f"mandatory {rub(mandatory)}, flexible {rub(flexible)}, savings {rub(plan.savings)}.")
        facts = {"month": plan.month, "months": len(plan.based_on), "income": rub(plan.income),
                 "mandatory": rub(mandatory), "flexible": rub(flexible), "savings": rub(plan.savings)}
        text, rc = self._say(task, facts, draft)
        self._emit("budget", text, data={"month": plan.month, "envelopes": rows, "income": rub(plan.income),
                                         "savings": rub(plan.savings), "cushion": rub(plan.cushion_target),
                                         "calc": plan.calc, "one_offs": len(plan.one_offs), "refcheck": rc,
                                         "applied": bool(self._envelopes())}, task_id=task.task_id)

    def _on_safe_to_spend(self, task, route, level):
        stt = self._safe()
        draft = (f"You can spend {rub(stt.per_day)} a day until payday on {stt.payday:%d %b} "
                 f"({stt.days_to_payday} days).")
        if stt.per_day == 0 and (fc := self._forecast()).gaps:
            g = fc.gaps[0]
            draft += f" Money is short: {g.event} {rub(g.amount)} on {g.date:%d %b} lacks {rub(g.shortfall)}."
        facts = {"per_day": rub(stt.per_day), "payday": stt.payday.isoformat(), "days": stt.days_to_payday,
                 "calc": stt.calc, "gaps": [g.model_dump(mode="json") for g in self._forecast().gaps[:1]]}
        text, rc = self._say(task, facts, draft)
        self._emit("safe", text, data={"per_day": rub(stt.per_day), "calc": stt.calc, "refcheck": rc,
                                       "per_envelope": {B.ENV[k].name: rub(v) for k, v in stt.per_envelope.items()}},
                   task_id=task.task_id)

    def _on_can_afford(self, task, route, level):
        price = _parse_rub(route.amount)
        if not price:
            self._emit("text", "How much does it cost?", task_id=task.task_id)
            return
        now, txs = self.clock.now(), self.bank.transactions(self.client)
        envs = self._envelopes()
        if envs:
            avail = {k: v for k, v in envs.items() if B.ENV[k].kind == "flexible"}
        else:
            plan, spent = self._plan(), B.spent_by_envelope(txs, B.month_key(now))
            avail = {e.id: max(0, e.planned - spent.get(e.id, 0)) for e in plan.envelopes if e.kind == "flexible"}
        a = B.can_afford(price, now, self.bank.accounts[MAIN_ACCOUNT].balance, self._events(),
                         B.daily_flexible(txs, now), self._slots(), avail)
        item = route.item or "it"
        if a.verdict == "yes":
            draft = f"Yes, {item} for {rub(price)} fits: " + a.calc + "."
        elif a.verdict == "after_payday":
            draft = f"Better after payday on {a.earliest:%d %b}: " + "; ".join(a.reasons) + "."
        else:
            draft = f"Not this month: {a.calc}."
        facts = a.model_dump(mode="json") | {"item": item, "price": rub(price)}
        text, rc = self._say(task, facts, draft)
        self._emit("afford", text, data={"verdict": a.verdict, "price": rub(price), "calc": a.calc,
                                         "reasons": a.reasons, "refcheck": rc,
                                         "sources": [(B.ENV[s].name if s in B.ENV else s, rub(v))
                                                     for s, v in a.sources]}, task_id=task.task_id)

    def _on_cash_gap(self, task, route, level):
        fc = self._forecast()
        if not fc.gaps:
            self._emit("text", f"No cash gaps until payday on {self._payday():%d %b}. Lowest balance: "
                               f"{rub(fc.min_balance)}.", task_id=task.task_id)
            return
        self._gap_item(fc, task.task_id)

    def _gap_item(self, fc: B.Forecast, task_id: str | None = None):
        g = fc.gaps[0]
        opts = B.gap_options(fc, self.clock.now(), self._envelopes() or None, self._leaks(),
                             self.bank.accounts[SAVINGS_ACCOUNT].balance,
                             daily_flex=B.daily_flexible(self.bank.transactions(self.client), self.clock.now()))
        rows = []
        for o in opts:
            kind = {"from_savings": "from_savings", "cancel_subscription": "block", "move_envelope": "move",
                    "spend_less": "info", "shift_goal": "info"}[o.kind]
            m = self._option(kind, o.title, o.action, o.amount)
            rows.append({"id": m.id, "title": o.title, "covers": o.covers, "kind": o.kind})
        text = (f"In {g.days_ahead} days, on {g.date:%d %b}, {g.event} {rub(g.amount)} is due and "
                f"{rub(g.shortfall)} will be missing. Ways to cover it with your own money (never a loan):")
        self._emit("gap", text, data={"gap": g.model_dump(mode="json"), "options": rows,
                                      "min_balance": rub(fc.min_balance)}, task_id=task_id)

    # ---------------------------------------------------------------- options chosen by the client

    def _opt_from_savings(self, o: OptionMeta):
        task = self.k.open_task(self.client, o.title, "cash_gap")
        dst = "env_bills" if "env_bills" in self.bank.envelopes else MAIN_ACCOUNT
        p = IntentProposal(
            action=Action.TRANSFER_OWN,
            payee=claim({"value": dst, "ref": self.k.bank_value(f"bank:own:{dst}", dst, "your accounts")}),
            amount=claim({"value": o.amount, "ref": self.k.bank_value(f"bank:option:{o.id}:amount", o.amount,
                                                                     "kernel forecast")}),
            funding_source=claim({"value": SAVINGS_ACCOUNT, "ref": self.k.bank_value(
                f"bank:src:{SAVINGS_ACCOUNT}", SAVINGS_ACCOUNT, "your accounts")}),
            memo="cover the cash gap")
        s = session(self.k, self.gw, Role.DISPATCHER, 0, D.SYSTEM, self.client, task.task_id)
        d = propose(self.k, s, p)
        self._decision(d, CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="gap"))

    def _opt_block(self, o: OptionMeta):
        task = self.k.open_task(self.client, o.title, "block")
        m = o.action["merchant"]
        p = IntentProposal(action=Action.BLOCK_MERCHANT, payee=claim(
            {"value": m, "ref": self.k.bank_value(f"bank:payee:{m}", m, "bank statement")}), memo=o.title[:80])
        s = session(self.k, self.gw, Role.DISPATCHER, 0, D.SYSTEM, self.client, task.task_id)
        d = propose(self.k, s, p)
        self._decision(d, CardMeta(pending_id=d.pending_id or "", task_id=task.task_id, kind="block"))

    def _opt_move(self, o: OptionMeta):
        """Moving money between envelopes is the client's own action; agents can only suggest it."""
        a = o.action
        try:
            self.bank.transfer_own(self.client, a["src"], a["dst"], a["amount"], initiator="client", memo="envelopes")
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"Could not move the money: {e}")
            return
        self.k.journal.append("client", "envelope.moved", a)
        self._emit("notice", f'Moved {rub(a["amount"])} from "{B.ENV[a["src"]].name}" to "{B.ENV[a["dst"]].name}".')

    def _opt_info(self, o: OptionMeta):
        self._emit("notice", f"Noted: {o.title}. I will remind you if the forecast changes.")

    # ================================================================== decisions -> chat items

    def _decision(self, d: Decision, meta: CardMeta | None, *, confirmed: bool = False, standing: bool = False,
                  template_used: str | None = None):
        task_id = meta.task_id if meta else None
        cost = self.gw.task_cost(task_id) if task_id else None
        steps = [{"step": s.step, "verdict": int(s.verdict), "reasons": s.reasons} for s in d.steps]
        common = {"steps": steps, "cost": cost, "template": template_used, "stage": d.stage}
        if d.outcome == Outcome.PENDING:
            assert meta is not None
            meta.pending_id = d.pending_id or ""
            self.cards[meta.pending_id] = meta
            self._emit("card", d.card.title, data={**common, "card": d.card.model_dump(mode="json"),
                                                  "pending_id": d.pending_id, "risk": int(d.card.risk)},
                       task_id=task_id)
            short = next((r for r in d.reasons if r.startswith("not enough money in envelope")), None)
            if short and meta.kind in ("pay", "bill"):
                self._overspend_options(d)
            return
        if d.outcome == Outcome.EXECUTED:
            self._emit("receipt", self._receipt_text(d, meta, confirmed), data={
                **common, "reference": d.receipt.reference if d.receipt else None, "notify_only": d.notify_only,
                "standing": standing}, task_id=task_id)
            if meta is not None:
                self.cards.pop(meta.pending_id, None)
                self._after_execute(d, meta, standing)
            return
        verb = "stopped" if d.outcome == Outcome.DENIED else "could not complete"
        self._emit("denied" if d.outcome == Outcome.DENIED else "error",
                   f"I {verb} this action: " + "; ".join(d.reasons[:3]), data=common, task_id=task_id)

    def _receipt_text(self, d: Decision, meta: CardMeta | None, confirmed: bool) -> str:
        e = next((x for x in reversed(self.k.journal.entries) if x.event == "decision.executed"), None)
        s = e.data if e else {}
        name = self._name(s.get("payee") or "")
        amount = rub(s["amount"]) if s.get("amount") else ""
        src = self.bank.label(self.client, s["funding_source"]) if s.get("funding_source") else ""
        what = {"pay": f"Paid {name} {amount} from {src}", "transfer_own": f"Moved {amount} to {name} from {src}",
                "block_merchant": f"Future charges from {name} are blocked",
                "dispute": f"Dispute opened for {amount} at {name}; the bank decides within 3 days"}.get(
            s.get("action", ""), "Done")
        if d.notify_only:
            what += " (within your standing permission, no confirmation needed)"
        return what + "."

    def _after_execute(self, d: Decision, meta: CardMeta, standing: bool):
        e = next(x for x in reversed(self.k.journal.entries) if x.event == "decision.executed")
        s = e.data
        if meta.kind == "pay" and meta.template and s.get("payee"):
            sm = self.k.mandates.find_standing(self.client, Action.PAY, s["payee"]) if standing else None
            mid = sm.mandate.id if sm else None
            existing = self.templates.match(self.client, Action.PAY, " ".join(meta.template["keywords"]))
            self.templates.save(Template(
                client_id=self.client, action=Action.PAY, payee=s["payee"], payee_name=self._name(s["payee"]),
                amount=s["amount"], funding_source=s["funding_source"], keywords=meta.template["keywords"],
                created=self.clock.now(), mandate_id=mid or (existing.mandate_id if existing else None)))
        if meta.doc_id:
            self.paid_docs.add(meta.doc_id)
        if meta.kind == "gap":
            fc = self._forecast()
            self._emit("notice", "The cash gap is closed. " + (f"Lowest balance until payday: {rub(fc.min_balance)}."
                                                              if not fc.gaps else "Another gap remains."),
                       task_id=meta.task_id)

    def _overspend_options(self, d: Decision):
        intent_amt = next((f for f in d.card.fields if f.name == "amount"), None)
        pending = self.k.pending[d.pending_id]
        src, amount = pending.intent.val("funding_source"), pending.intent.val("amount")
        have = max(0, self.bank.available(self.client, src) or 0)
        need = amount - have
        rows = []
        for env in ("env_fun", "env_shopping", "env_food", "env_transport", "env_health", "env_cushion"):
            e = self.bank.envelopes.get(env)
            if e and e.balance >= need and env != src:
                o = self._option("move", f'Move {rub(need)} from "{e.name}" to "{B.ENV[src].name}"',
                                 {"src": env, "dst": src, "amount": need}, need)
                rows.append({"id": o.id, "title": o.title})
                break
        text = (f'Only {rub(have)} is left in "{B.ENV[src].name}", the payment is {rub(amount)}. '
                "I cannot move money between envelopes myself.")
        self._emit("options", text, data={"options": rows, "field": intent_amt.value if intent_amt else None})

    # ================================================================== helpers

    def _emit(self, type_: str, text: str = "", *, data: dict | None = None, task_id: str | None = None) -> Item:
        self.version += 1
        it = Item(seq=self.version, ts=self.clock.now(), type=type_, text=text, data=data or {}, task_id=task_id)
        self.items.append(it)
        return it

    def _option(self, kind: str, title: str, action: dict, amount: int) -> OptionMeta:
        o = OptionMeta(kind=kind, title=title, action=action, amount=amount)
        self.options[o.id] = o
        return o

    def _say(self, task, facts: dict, draft: str) -> tuple[str, dict]:
        if not self.explain:
            return draft, {"ok": True, "unknown": [], "model": False}
        try:
            r = self.gw.call("explainer", EXPLAIN_SYSTEM, json.dumps({"facts": facts, "draft": draft},
                                                                     ensure_ascii=False, sort_keys=True),
                             level=1, task_id=task.task_id, max_tokens=200)
        except LLMFailure:
            return draft, {"ok": True, "unknown": [], "model": False}
        text, rc = guarded(r.text.strip(), facts | {"draft": draft}, draft)
        return text, {"ok": rc.ok, "unknown": rc.unknown, "model": True}

    def _welcome(self):
        name = self.world.profile.name.split()[0]
        self._emit("text", f"Hi {name}. I watch your money and act only within what you allow. " + HELP)
        fc = self._forecast()
        if fc.gaps:
            self._gap_item(fc)
        flagged = [d for d in self.inbox() if self.desk.screened[d.id].events]
        if flagged:
            self._emit("notice", f"{len(flagged)} messages in your inbox look suspicious; I will not act on them "
                                 "without telling you.", data={"docs": [d.id for d in flagged]})

    def _name(self, token: str) -> str:
        if m := self.merchants.get(token):
            return m.name
        if self.bank.owns(self.client, token):
            return self.bank.label(self.client, token)
        info = self.k.directory.payee(self.client, token)
        return info.name if info else token

    def _envelopes(self) -> dict[str, int]:
        return {e.id: e.balance for e in self.bank.envelopes.values() if e.client_id == self.client}

    def _plan(self) -> B.BudgetPlan:
        if self.plan is None:
            self.plan = B.autobudget(self.bank.transactions(self.client), self.clock.now())
        return self.plan

    def _slots(self):
        return B.income_schedule(self.bank.transactions(self.client))

    def _payday(self) -> date:
        return B.next_payday(self._slots(), self.clock.now())

    def _series(self):
        return recurring(self.bank.transactions(self.client), self.bank.cards_on_file(self.client), self.merchants,
                         self.clock.now())

    def _known_bills(self) -> list[B.KnownBill]:
        out = []
        for doc_id, c in self.bill_checks.items():
            if c.verdict == "verified" and c.due and c.amount and doc_id not in self.paid_docs:
                out.append(B.KnownBill(payee=c.payee, name=c.payee_name or c.payee, amount=c.amount, due=c.due,
                                       ref=doc_id))
        return out

    def _events(self) -> list[B.Event]:
        now = self.clock.now()
        until = date(now.year + (now.month == 12), now.month % 12 + 1, 28)
        events = B.upcoming(self._series(), self._slots(), now, until, self._known_bills()) + self._template_events(until)
        paid = {(t.counterparty, t.ts.year, t.ts.month) for t in self.bank.transactions(self.client)
                if t.initiator == "agent"}
        return [e for e in events if not (e.merchant and e.date.month == now.month and e.kind != "income"
                                          and (e.merchant, now.year, now.month) in paid)]

    def _template_events(self, until: date) -> list[B.Event]:
        """Payments the agent will make under standing permissions are known future outflows."""
        out = []
        today = self.clock.now().date()
        paid = {t.counterparty: t.ts.date() for t in self.bank.transactions(self.client) if t.initiator == "agent"}
        for t in self.templates.for_client(self.client):
            st = self.k.mandates.state(t.mandate_id) if t.mandate_id else None
            if st is None or st.revoked or st.signed.mandate.exp.date() < today:
                continue
            last = paid.get(t.payee, t.created.date())
            d = B._next(last, last.day)
            if d < today:
                d = today
            m = self.merchants.get(t.payee)
            while d <= until:
                out.append(B.Event(date=d, kind="subscription", name=t.payee_name, amount=-t.amount, merchant=t.payee,
                                   envelope=B.ENV_OF.get(m.category) if m else None, refs=[t.id]))
                d = B._next(d, last.day)
        return out

    def _forecast(self) -> B.Forecast:
        now = self.clock.now()
        txs = self.bank.transactions(self.client)
        return B.forecast(now, self.bank.accounts[MAIN_ACCOUNT].balance, self._events(), B.daily_flexible(txs, now),
                          self._payday())

    def _safe(self) -> B.SafeToSpend:
        envs = self._envelopes() or None
        return B.safe_to_spend(self.clock.now(), self.bank.accounts[MAIN_ACCOUNT].balance, self._events(),
                               self._plan().cushion_target, envs, self._payday())

    def _leaks(self):
        return leaks(self._series(), [Usage(**u.model_dump()) for u in self.world.usage], self.clock.now())


def _keywords(text: str) -> list[str]:
    stop = {"for", "the", "my", "a", "an", "please", "pay", "again", "subscription", "за", "оплати", "заплати"}
    return [w for w in re.findall(r"[a-zа-яё0-9]+", text.lower()) if w not in stop and len(w) > 2]


def _parse_rub(s: str | None) -> int | None:
    if not s:
        return None
    t = re.sub(r"[\s ]", "", s).replace(",", ".")
    try:
        return kop(float(t))
    except ValueError:
        return None


def _explain_stub(req: dict) -> str:
    return json.loads(req["user"])["draft"]
