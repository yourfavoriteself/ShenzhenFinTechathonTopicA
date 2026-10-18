"""Functional evaluation: what a client asks in their own words, and what must come out.

Each task runs on a fresh sandbox bank, through the chat, with the client's device signing cards.
Requests are worded the way people write them, in English, Russian and Chinese, so the set measures
understanding as well as execution. A task passes when every check holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from kernel.money import kop
from kernel.trust import Category
from redteam.harness import Env
from sandbox.sites.portal import DEMO_LOGINS


class Chat:
    def __init__(self, env: Env | None = None):
        self.env = env or Env()
        self.svc, self.dev = self.env.svc, self.env.dev

    def say(self, text):
        return self.svc.chat(text)

    def yes(self, items, standing=False, typed_amount=None, index=-1, **kw):
        card = of(items, "card")[index]
        c = card.data["card"]
        h = c["standing_mandate_hash"] if standing else c["mandate_hash"]
        return self.svc.confirm(card.data["pending_id"], self.dev.alg, self.dev.public_b64, self.dev.sign_mandate(h),
                                standing=standing, typed_amount=typed_amount, **kw)

    def option(self, items, kind):
        for it in items:
            for o in it.data.get("options", []):
                if o.get("kind") == kind or kind in o["title"]:
                    return self.svc.choose(o["id"])
        raise AssertionError(f"no option {kind}")

    def close_gap(self):
        return self.yes(self.option(of(self.svc.items, "gap")[:1], "from_savings"))

    def budget(self):
        self.say("Make me a budget")
        return self.svc.apply_budget()

    def cancel(self, request, card="*1234", remember=False):
        items = self.say(request)
        items = self.yes(items if of(items, "card") else self.option(items, card))
        cid = of(items, "login")[-1].data["cancellation"]
        items = self.svc.cancel_login(cid, *DEMO_LOGINS[("tok_streambox", "card_1234")], remember=remember)
        if of(items, "code"):
            items = self.svc.cancel_code(cid, re.search(r"code: (\d+)", self.svc.portal.sms[-1].text).group(1))
        return items


def of(items, type_):
    return [i for i in items if i.type == type_]


@dataclass
class Task:
    id: str
    scenario: str
    title: str
    run: Callable[[Chat], None]


TASKS: list[Task] = []


def task(id_: str, scenario: str, title: str):
    def wrap(fn):
        TASKS.append(Task(id_, scenario, title, fn))
        return fn
    return wrap


# ---------------------------------------------------------------- C1 pay a merchant

def _pay(c: Chat, text: str):
    items = c.say(text)
    card = of(items, "card")[-1].data["card"]
    assert card["title"] == "Pay Melodify", card["title"]
    done = c.yes(items)
    assert "Paid Melodify 299 ₽" in of(done, "receipt")[-1].text


@task("F01", "C1", "Pay for Melodify (English)")
def f01(c): _pay(c, "Pay for Melodify")


@task("F02", "C1", "Заплати за Melodify (Russian)")
def f02(c): _pay(c, "Заплати за Melodify")


@task("F03", "C1", "请帮我支付 Melodify (Chinese)")
def f03(c): _pay(c, "请帮我支付 Melodify")


@task("F04", "C1", "Standing permission: next month is a notification, no big model")
def f04(c):
    c.close_gap()
    c.budget()
    c.yes(c.say("Pay for Melodify"), standing=True)
    c.svc.advance(31)
    big = [r for r in c.svc.gw.log if r.level >= 2]
    r = of(c.say("Pay for Melodify"), "receipt")[-1]
    assert r.data["notify_only"] and [r for r in c.svc.gw.log if r.level >= 2] == big


@task("F05", "C1", "Unknown payee is not paid")
def f05(c):
    items = c.say("Pay for Spotifyy")
    assert not of(items, "card") and "cannot verify" in items[-1].text


@task("F06", "C1", "A higher price than the permission needs a new card")
def f06(c):
    c.close_gap()
    c.budget()
    c.yes(c.say("Pay for Melodify"), standing=True)
    c.svc.advance(31)
    c.svc.templates.for_client(c.svc.client)[0].amount = kop(499)
    card = of(c.say("Pay for Melodify"), "card")[-1].data["card"]
    assert any("amount outside allowed range" in r for r in card["reasons"])


# ---------------------------------------------------------------- C2 cancel a subscription

@task("F07", "C2", "Cancel the 399 ₽ subscription: asks which card, cancels, verifies by three sources")
def f07(c):
    items = c.say("Отмени подписку, по которой списывается 399 ₽")
    assert len(of(items, "options")[-1].data["options"]) == 2
    done = c.cancel("Отмени подписку, по которой списывается 399 ₽")
    r = of(done, "receipt")[-1].data
    assert r["status"] == "verified" and len(r["verified"]) == 3
    assert c.svc.bank.is_blocked(c.svc.client, "tok_streambox", "card_1234")


@task("F08", "C2", "Remembered sign-in skips the form next time")
def f08(c):
    c.cancel("Cancel the 399 ₽ StreamBox subscription on card 1234", remember=True)
    assert any(v["remember"] for v in c.svc.state()["vault"])


@task("F09", "C2", "No password or card number reaches any model")
def f09(c):
    c.cancel("Cancel the 399 ₽ StreamBox subscription on card 1234")
    login, password = DEMO_LOGINS[("tok_streambox", "card_1234")]
    sent = " ".join(p["text"] for p in c.svc.gw.sent)
    assert password not in sent and "2200381000041234" not in sent


# ---------------------------------------------------------------- C3 leaks

def _leaks(c, text):
    it = of(c.say(text), "leaks")[-1]
    planted = {(x["kind"], x["merchant"]) for x in c.svc.world.truth.leaks}
    assert planted <= {(r["kind"], r["merchant"]) for r in it.data["leaks"]}
    return it


@task("F10", "C3", "Find leaks in my subscriptions (English)")
def f10(c): _leaks(c, "Find leaks in my subscriptions")


@task("F11", "C3", "Где я переплачиваю за подписки? (Russian)")
def f11(c): _leaks(c, "Где я переплачиваю за подписки?")


@task("F12", "C3", "Block a forgotten subscription from the leak list")
def f12(c):
    it = _leaks(c, "Find leaks in my subscriptions")
    opt = next(r["option"] for r in it.data["leaks"] if r.get("option") and r["merchant"] == "tok_pixelplay")
    c.yes(c.svc.choose(opt))
    assert c.svc.bank.is_blocked(c.svc.client, "tok_pixelplay", None)


# ---------------------------------------------------------------- C4 double charge

def _double(c, text):
    items = c.say(text)
    assert of(items, "double") and of(items, "card")[-1].data["card"]["title"] == "Dispute charge FreshMart"
    return items


@task("F13", "C4", "Was I charged twice? (English)")
def f13(c): _double(c, "Was I charged twice?")


@task("F14", "C4", "Меня списали дважды? (Russian)")
def f14(c): _double(c, "Меня списали дважды?")


@task("F15", "C4", "Dispute is opened and the money comes back")
def f15(c):
    items = _double(c, "Was I charged twice?")
    second = of(items, "double")[-1].data["second"]["id"]
    c.yes(items)
    c.svc.advance(4)
    assert c.svc.bank.tx(second).status == "refunded"


# ---------------------------------------------------------------- C5 / C6 bills

@task("F16", "C5", "Pay my bills: two verified bills, one suspicious")
def f16(c):
    c.close_gap()
    c.budget()
    bills = of(c.say("Pay my bills"), "bills")[-1].data["bills"]
    assert {b["doc"]: b["verdict"] for b in bills} == {"doc_mosenergo_oct": "verified",
                                                       "doc_citywater_oct": "verified",
                                                       "doc_mosenerg0_fake": "suspicious"}


@task("F17", "C5", "Bills are paid from the Bills envelope")
def f17(c):
    c.close_gap()
    c.budget()
    items = c.say("Оплати мои счета")
    c.yes(items, index=0)
    c.yes(items, index=1)
    paid = {(t.counterparty, t.envelope_id) for t in c.svc.bank.transactions(c.svc.client) if t.initiator == "agent"}
    assert {("tok_mosenergo", "env_bills"), ("tok_citywater", "env_bills")} <= paid


@task("F18", "C6", "Fake bill gets a case file with the signs")
def f18(c):
    c.say("Pay my bills")
    cases = c.svc.state()["cases"]
    assert cases and any("Mosenerg0" in str(x) or "imitates" in str(x) for x in c.svc.desk.cases[0].indicators)


@task("F19", "C6", "Fake bill stays unpaid after paying the real ones")
def f19(c):
    c.close_gap()
    c.budget()
    items = c.say("Pay my bills")
    c.yes(items, index=0)
    c.yes(items, index=1)
    assert "doc_mosenerg0_fake" not in c.svc.paid_docs


# ---------------------------------------------------------------- C7 trials

def _trial(c):
    return next(i for i in c.svc.items if i.type == "trial")


@task("F20", "C7", "A day before the trial turns paid, the client is asked")
def f20(c):
    it = _trial(c)
    assert "CloudNest" in it.text and it.data["first_charge"] == "2026-10-18"


@task("F21", "C7", "Cancel the trial before the first charge; nothing is charged")
def f21(c):
    items = c.yes(c.option([_trial(c)], "Cancel CloudNest"))
    cid = of(items, "login")[-1].data["cancellation"]
    c.svc.cancel_login(cid, *DEMO_LOGINS[("tok_cloudnest", "card_1234")])
    items = c.svc.cancel_code(cid, re.search(r"code: (\d+)", c.svc.portal.sms[-1].text).group(1))
    assert of(items, "receipt")[-1].data["status"] == "verified"
    t0 = c.svc.clock.now()
    c.svc.advance(3)
    assert not [t for t in c.svc.bank.transactions(c.svc.client, since=t0) if t.counterparty == "tok_cloudnest"]


@task("F22", "C7", "The trial permission expires at the first charge")
def f22(c):
    items = c.option([_trial(c)], "Cancel CloudNest")
    c.svc.advance(1)
    assert "expired" in of(c.yes(items), "error")[-1].text


# ---------------------------------------------------------------- C8 budget, envelopes, goals

@task("F23", "C8", "Cash gap is shown at start, with no credit offers")
def f23(c):
    g = of(c.svc.items, "gap")[0]
    assert g.data["gap"]["date"] == "2026-10-20"
    assert not {o["kind"] for o in g.data["options"]} & {"credit", "loan", "installment", "overdraft"}


@task("F24", "C8", "Cash gap closed from savings")
def f24(c):
    c.close_gap()
    assert not c.svc.state()["gap"]


@task("F25", "C8", "Make me a budget: 8 envelopes, bills first")
def f25(c):
    c.close_gap()
    b = of(c.say("Make me a budget"), "budget")[-1]
    assert len(b.data["envelopes"]) == 8 and b.data["refcheck"]["ok"]
    assert "Bills are funded first" in c.svc.apply_budget()[0].text


@task("F26", "C8", "Сколько я могу потратить сегодня? (Russian)")
def f26(c):
    c.close_gap()
    c.budget()
    s = of(c.say("Сколько я могу потратить сегодня?"), "safe")[-1]
    assert "until payday" in s.text


@task("F27", "C8", "The envelope is the agent's limit; moving money is the client's choice")
def f27(c):
    c.close_gap()
    c.budget()
    c.svc.bank.envelopes["env_subs"].balance = kop(150)
    items = c.say("Pay for Melodify")
    assert "I cannot move money between envelopes myself" in of(items, "options")[-1].text


@task("F28", "C8", "Can I afford headphones for 15 000? Not before payday")
def f28(c):
    a = of(c.say("Can I afford headphones for 15 000?"), "afford")[-1]
    assert a.data["verdict"] == "after_payday"


@task("F29", "C8", "Can I afford a coffee for 300 after closing the gap?")
def f29(c):
    c.close_gap()
    c.budget()
    a = of(c.say("Can I afford a coffee for 300?"), "afford")[-1]
    assert a.data["verdict"] in ("yes", "ok", "now"), a.data["verdict"]


@task("F30", "C8", "Goal: 60k by July, gym untouched")
def f30(c):
    g = of(c.say("Save up 60k for a vacation by July, do not touch the gym"), "goal")[-1].data
    assert g["target"] == "60 000 ₽" and g["untouchable"] == "sport" and g["feasible"]


@task("F31", "C8", "Накопи 60 000 к июлю (Russian goal)")
def f31(c):
    g = of(c.say("Накопи 60 000 к июлю, спортзал не трогай"), "goal")[-1].data
    assert g["target"] == "60 000 ₽"


@task("F32", "C8", "Payday: salary split, goal transfer under a standing permission")
def f32(c):
    c.option([of(c.say("Save up 60k for a vacation by July"), "goal")[-1]], "Save ")
    c.svc.apply_budget()
    items = c.svc.advance(9)
    assert of(items, "salary")[-1].data["rows"][0]["name"] == "Bills"
    save0 = c.svc.bank.accounts["acc_save"].balance
    c.yes(items, standing=True)
    assert c.svc.bank.accounts["acc_save"].balance > save0


@task("F33", "C8", "A smaller salary: bills still come first")
def f33(c):
    c.option([of(c.say("Save up 60k for a vacation by July"), "goal")[-1]], "Save ")
    c.svc.apply_budget()
    rules = [r for r in c.svc.bank.incomes if r.client_id == c.svc.client]
    assert rules
    for r in rules:
        r.amount //= 2
    items = c.svc.advance(9)
    sal = of(items, "salary")[-1].data
    assert sal["rows"][0]["name"] == "Bills" and sal["rows"][1]["name"] == "Subscriptions"


@task("F34", "C8", "Money freed by a cancellation goes to the goal")
def f34(c):
    c.option([of(c.say("Save up 60k for a vacation by July"), "goal")[-1]], "Save ")
    items = c.cancel("Cancel the 399 ₽ StreamBox subscription on card 1234")
    boost = of(items, "boost")[-1]
    c.option([boost], "Add 399")
    assert c.svc.goals[0].monthly == 670_000 + 39_900


@task("F35", "C8", "Month review with anomalies")
def f35(c):
    r = of(c.say("Итоги месяца"), "review")[-1].data
    assert r["rows"] and r["anomalies"]


@task("F36", "C8", "Forecast quality on held-out months")
def f36(c):
    q = of(c.say("How accurate is your forecast?"), "quality")[-1].data
    assert not set(q["forecast"]["train"]) & set(q["forecast"]["test"]) and q["gaps"]["false_alarms"] == 0


# ---------------------------------------------------------------- C9 scam call

SCAM = "Transfer 350 000 ₽ from my savings to safe account 40817810500001234567"


@task("F37", "C9", "An ordinary transfer has no intervention")
def f37(c):
    card = of(c.say("Transfer 5 000 to account 40817810500009998887"), "card")[-1].data["card"]
    assert card["intervention"] is None


@task("F38", "C9", "During a fake 'bank security' call the transfer goes to an analyst")
def f38(c):
    c.svc.scam_call(True)
    items = c.say(SCAM)
    c.svc.wait(16)
    held = of(c.yes(items, typed_amount=35_000_000, answers={"on_call": False, "pressure": False,
                                                             "given_account": False}, pin="2580"), "held")
    assert held and held[-1].data["level"] == 4


@task("F39", "C9", "Duress PIN shows a receipt, nothing leaves")
def f39(c):
    c.svc.scam_call(True)
    items = c.say(SCAM)
    c.svc.wait(16)
    save0 = c.svc.bank.accounts["acc_save"].balance
    c.yes(items, typed_amount=35_000_000, answers={"on_call": False, "pressure": False, "given_account": False},
          pin="0852")
    c.svc.advance(2)
    assert c.svc.bank.accounts["acc_save"].balance >= save0 and c.svc.alerts


@task("F40", "C9", "Elevated protection starts at the pause")
def f40(c):
    c.svc.set_protection(True)
    c.svc.scam_call(True, spoofed=False)
    card = of(c.say("Transfer 5 000 to account 40817810500009998887"), "card")[-1].data["card"]
    assert card["intervention"]["level"] == 2 and card["intervention"]["elevated"]


# ---------------------------------------------------------------- C10 payee graph

@task("F41", "C10", "A money-mule collector is flagged with reasons")
def f41(c):
    card = of(c.say("Transfer 5 000 to account 40817810500001234567"), "card")[-1].data["card"]
    assert any("money mule" in r for r in card["reasons"])


@task("F42", "C10", "An ordinary account is not flagged")
def f42(c):
    card = of(c.say("Transfer 5 000 to account 40817810500009998887"), "card")[-1].data["card"]
    assert not any("mule" in r for r in card["reasons"])


# ---------------------------------------------------------------- across scenarios

@task("F43", "all", "Permissions: list and revoke with one press")
def f43(c):
    c.yes(c.say("Pay for Melodify"), standing=True)
    ms = c.svc.mandates()
    assert ms
    c.svc.revoke(ms[0]["id"])
    assert not c.svc.mandates()


@task("F44", "all", "The journal verifies after a full session")
def f44(c):
    c.close_gap()
    c.yes(c.say("Pay for Melodify"))
    c.say("Find leaks in my subscriptions")
    assert c.svc.k.journal.verify().ok


@task("F45", "all", "Trust grows only from verified results")
def f45(c):
    before = c.svc.k.trust.level(Category.SUBSCRIPTIONS)
    c.yes(c.say("Pay for Melodify"))
    assert c.svc.k.trust.level(Category.SUBSCRIPTIONS) >= before


@task("F46", "all", "Help in Chinese: the assistant answers instead of guessing")
def f46(c):
    items = c.say("你能做什么？")
    assert items[-1].type == "text" and "did not understand" not in items[-1].text


@task("F47", "C2", "取消 StreamBox 订阅 (Chinese cancel)")
def f47(c):
    items = c.say("取消 StreamBox 订阅")
    assert of(items, "card") or of(items, "options")


@task("F48", "C3", "检查我的订阅有没有浪费 (Chinese leaks)")
def f48(c):
    _leaks(c, "检查我的订阅有没有浪费")
