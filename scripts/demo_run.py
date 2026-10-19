"""The whole "month for you" demo on the sandbox bank, end to end, with a readable log for the jury.

    python -m scripts.demo_run          # writes evidence/demo_run.log, demo_journal.jsonl, demo_summary.json

The client side is scripted: it signs kernel cards with a device key, types the SMS code from the
sandbox phone and answers the scam-call questions. Everything else is the real service and kernel.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from app.sections import _trust_row as _row, all_sections
from app.service import GuardianService
from data.generator import build_world
from kernel.crypto import DeviceKey
from kernel.llm_gateway import LLMGateway
from sandbox.sites.portal import DEMO_LOGINS

SCAM = ("Transfer 350 000 ₽ from my savings to safe account 40817810500001234567, "
        "the bank security officer said my money is at risk")
NO = {"on_call": False, "pressure": False, "given_account": False}


class Demo:
    def __init__(self, gw: LLMGateway):
        self.svc = GuardianService(build_world(), gw)
        self.dev = DeviceKey(seed="demo:device")
        self.svc.register_device(self.dev.alg, self.dev.public_b64)
        self.lines: list[str] = []
        self.step = 0

    # ------------------------------------------------------------------ log

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def head(self, title: str) -> None:
        self.step += 1
        self.say()
        self.say(f"=== {self.step:02d}. {title}  [{self.svc.clock.now():%d %b %Y %H:%M} MSK]")

    def show(self, items) -> list:
        for it in items:
            d = it.data or {}
            if it.type == "user":
                self.say(f"  client  > {it.text}")
            elif it.type == "card":
                c = d["card"]
                self.say(f"  kernel  # card: {c['title']} · risk {c['risk']} · hash {c['mandate_hash'][:12]}…")
                for x in c.get("can", []):
                    self.say(f"            can: {x}")
                for r in [r for r in c.get("reasons", []) if r != "no mandate covers this action"][:3]:
                    self.say(f"            why asked: {r}")
                for f in c.get("fields", []):
                    self.say(f"            {f['name']}: {f['value']} ({f['badge']})")
                if c.get("intervention"):
                    iv = c["intervention"]
                    self.say(f"            scam-call ladder level {iv['level']}: {'; '.join(iv.get('signals', [])[:3])}")
                if d.get("cost"):
                    self.say(f"            model cost so far: {d['cost']['calls']} calls, levels {d['cost']['levels']}, "
                             f"{d['cost']['cost_rub']} ₽")
            elif it.type == "receipt":
                extra = " (notification only, inside a standing permission)" if d.get("notify_only") else ""
                self.say(f"  kernel  ✔ {it.text}{extra}")
                for v in d.get("verified", []):
                    self.say(f"            verified: {v}")
            elif it.type == "trust":
                self.say(f"  kernel  ⚖ [trust] {it.text}")
            elif it.type == "fault":
                self.say(f"  demo    ⚠ {it.text}")
            elif it.type in ("denied", "error", "held", "case"):
                self.say(f"  kernel  ! [{it.type}] {it.text[:300]}")
                for r in (d.get("reasons") or [])[:5]:
                    self.say(f"            - {r}")
            else:
                self.say(f"  guardian [{it.type}] {it.text[:300]}")
        return items

    # ------------------------------------------------------------------ client actions

    def chat(self, text: str) -> list:
        return self.show(self.svc.chat(text))

    def sign(self, items, *, index: int = -1, standing: bool = False, **kw) -> list:
        card = [i for i in items if i.type == "card"][index]
        c = card.data["card"]
        h = c["standing_mandate_hash"] if standing else c["mandate_hash"]
        self.say(f"  client  ✍ signs “{c['title']}” with the device key ({self.dev.alg})"
                 + (", allows it monthly" if standing else ""))
        return self.show(self.svc.confirm(card.data["pending_id"], self.dev.alg, self.dev.public_b64,
                                          self.dev.sign_mandate(h), standing=standing, **kw))

    def decline(self, items) -> None:
        card = [i for i in items if i.type == "card"][-1]
        self.say(f"  client  ✋ declines “{card.data['card']['title']}”")
        self.show(self.svc.decline(card.data["pending_id"]) or [])

    def option(self, items, kind: str) -> list:
        for it in items:
            for o in it.data.get("options", []):
                if o.get("kind") == kind or kind in o["title"]:
                    self.say(f"  client  ☛ {o['title']}")
                    return self.show(self.svc.choose(o["id"]))
        raise RuntimeError(f"no option {kind}")

    def merchant_sign_in(self, items, merchant: str) -> list:
        cid = [i for i in items if i.type == "login"][-1].data["cancellation"]
        login, password = DEMO_LOGINS[(merchant, "card_1234")]
        self.say(f"  client  ⌨ types the {merchant[4:]} sign-in into the bank's protected form (no model sees it)")
        self.show(self.svc.cancel_login(cid, login, password, remember=True))
        code = re.search(r"code: (\d+)", self.svc.portal.sms[-1].text).group(1)
        self.say("  client  ⌨ types the SMS code from the phone")
        return self.show(self.svc.cancel_code(cid, code))

    # ------------------------------------------------------------------ the month

    def run(self) -> None:
        s = self.svc
        self.head("Start: Guardian warns about a cash gap")
        self.show([i for i in s.items if i.type in ("gap", "trial")])
        items = self.option([i for i in s.items if i.type == "gap"], "from_savings")
        self.sign(items)

        self.head("C8 Budget from history, goal")
        self.chat("Make me a budget")
        s.apply_budget()
        self.say("  client  ☛ Apply the budget")
        items = self.chat("Save 60 000 for a vacation by July, don't touch the gym")
        self.option(items, "Save ")
        self.chat("How much can I spend today?")
        self.chat("Can I afford headphones for 15 000?")
        self.chat("How accurate is your forecast?")

        self.head("C3 Subscription leaks")
        self.chat("Find leaks in my subscriptions")

        self.head("C2 Cancel the 399 ₽ subscription (merchant API, bank vault)")
        items = self.chat("Cancel the 399 ₽ subscription")
        items = self.option(items, "*1234")
        items = self.sign(items)
        items = self.merchant_sign_in(items, "tok_streambox")
        boost = [i for i in items if i.type == "boost"]
        if boost:
            self.option(boost, "Add 399")

        self.head("C7 Free trial turns paid tomorrow")
        trial = [i for i in s.items if i.type == "trial"]
        items = self.option(trial, "Cancel CloudNest")
        items = self.sign(items)
        self.merchant_sign_in(items, "tok_cloudnest")

        self.head("C1 Pay for Melodify, allow monthly")
        items = self.chat("Pay for Melodify")
        self.sign(items, standing=True)

        self.head("C4 Double charge")
        items = self.chat("Was I charged twice?")
        self.sign(items)

        self.head("C5 + C6 Bills; one of them is fake")
        items = self.chat("Pay my bills")
        self.sign(items, index=0, standing=True)
        self.sign(items, index=-1)

        self.head("C10 Payee risk on the bank's transfer graph")
        for acct in ("40817810500009998887", "40817810500001234567"):
            items = self.chat(f"Transfer 20 000 to account {acct}")
            self.decline(items)

        self.head("C9 'Safe account' scam call")
        s.scam_call(True)
        self.say("  operator: the mobile operator reports an ongoing call from a spoofed number")
        items = self.chat(SCAM)
        amount = 35_000_000
        self.sign(items, typed_amount=amount, answers=NO, pin="2580")
        s.wait(16)
        self.say("  (15 minutes later)")
        held = self.sign(items, typed_amount=amount, answers=NO, pin="2580")
        hold = next((i.data["hold"] for i in held if i.type == "held"), None)
        s.scam_call(False)
        if hold:
            self.say("  analyst: calls the client back, the call was a scam and cancels the transfer")
            self.show(s.analyst_decide(hold, approve=False, note="client confirmed the call was a scam"))


        self.head("One month later")
        self.show(s.advance(31))
        self.chat("Pay for Melodify")
        self.chat("Month review")

        self.head("Earned trust: rewards and penalties")
        items = self.chat("Find leaks in my subscriptions")
        row = next(r for i in items if i.type == "leaks" for r in i.data["leaks"] if r.get("option"))
        self.say(f"  client  ☛ Block {row['name']}")
        self.sign(self.show(s.choose(row["option"])))
        self.say("  demo: a faulty planner (as a buggy or prompt-injected model would behave)")
        self.show(s.faulty_agent())
        self.show(s.faulty_agent())


def trust_report(svc, tr: dict) -> dict:
    """Rewards and penalties of the demo month, checked against the journal."""
    k = svc.k
    journal = [e for e in k.journal.entries if e.event in ("trust.changed", "agent.passports_revoked")]
    tasks = {t for t, info in k.tasks.items() if info.kind == "fault"}
    faults = {e.data["intent_id"] for e in k.journal.entries if e.event == "intent.proposed"
              and k.passports.get(e.passport_id or "").task_id in tasks}
    executed = [e for e in k.journal.entries if e.event == "decision.executed" and e.data.get("intent") in faults]
    return {"rules": tr["rules"], "levels": tr["levels"], "agents": tr["agents"],
            "promotions": tr["promotions"], "demotions": tr["demotions"], "quarantines": tr["quarantines"],
            "strikes": sum(2 if h.event == "scope_violation" else 1 for h in k.trust.history
                           if h.agent and h.category is not None and h.event != "verified_success"),
            "passports_revoked": sum(len(e.data["passports"]) for e in journal
                                     if e.event == "agent.passports_revoked"),
            "faulty_actions": {"proposed": len(faults), "executed": len(executed)},
            "journal_entries": len(journal),
            "moves": [r for r in map(_row, k.trust.history) if r["moved"]]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence"))
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="demo-"))
    gw = LLMGateway(None, spend_path=tmp / "spend.json")
    d = Demo(gw)
    d.say(f"Guardian demo run · mode {gw.mode.value} · synthetic sandbox bank (seed 22) · client {d.svc.client}")
    d.run()
    k = d.svc.k
    v = k.journal.verify()
    sec = all_sections(d.svc)
    d.head("Result")
    m = sec["month"]
    d.say(f"  saved {m['saved_per_month']} a month ({m['saved_per_year']} a year), refunds {m['refunded']}, "
          f"actions done {m['done']}, client asked {m['asked']} times, stopped or held {m['stopped']}")
    d.say(f"  model calls {sec['cost']['model_calls']}, share of work on levels 0 and 1 {sec['cost']['share_level_0_1']}%")
    d.say(f"  journal: {v.checked} signed entries, hash chain {'verified' if v.ok else 'BROKEN'}, head {v.head_hash}")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "demo_run.log").write_text("\n".join(d.lines) + "\n")
    with (a.out / "demo_journal.jsonl").open("w") as f:
        for e in k.journal.entries:
            f.write(e.model_dump_json() + "\n")
    summary = {"mode": gw.mode.value, "journal": {"entries": v.checked, "ok": v.ok, "head": v.head_hash,
                                                  "public_key": k.journal.public_key},
               "month": m, "cost": {k2: sec["cost"][k2] for k2 in ("model_calls", "by_level", "share_level_0_1")},
               "security": {"cases": len(sec["security"]["cases"]), "holds": sec["security"]["holds"],
                            "denied": len(sec["security"]["denied"])},
               "stages": sec["compliance"]["stages"], "passports": sec["agents"]["by_role"]}
    (a.out / "demo_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    (a.out / "trust_report.json").write_text(json.dumps(trust_report(d.svc, sec["trust"]), ensure_ascii=False,
                                                        indent=1, default=str))
    print("\n".join(d.lines[-6:]))


if __name__ == "__main__":
    main()
