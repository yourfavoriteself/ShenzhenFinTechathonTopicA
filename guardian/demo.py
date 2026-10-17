"""'A month in Mia's life': the end-to-end scenario used for the demo video and as
execution evidence. Every step runs through the real pipeline against the sandbox bank.

    python -m guardian.demo --out evidence
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .audit import verify_chain
from .baiter import load_scenarios
from .core import DATA_DIR, Guardian
from .models import Initiator, Payee, PaymentRequest, Rail
from .money import usd
from .profile import DURESS_PIN, NORMAL_PIN, build_demo, pay_day


def at(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def run(out_dir: Path | None = None) -> dict:
    g = build_demo()
    scen = load_scenarios(DATA_DIR / "scam_scripts.json")
    steps: list[dict] = []

    def step(when: str, title: str, feature: str, result: str, protected: int = 0, **extra):
        steps.append({"n": len(steps) + 1, "when": when, "title": title, "feature": feature, "result": result,
                      "money_protected": protected, **extra})

    total_before = 260000
    exposure_before = total_before

    # 1. Pay day and budget autopilot
    alloc = pay_day(g)
    step("Oct 1 09:00", "Scholarship and salary arrive", "Purpose-Bound Money",
         f"{usd(260000)} split into {len({a['envelope'] for a in alloc})} envelopes. Worst-case loss to an unknown "
         f"payee drops from {usd(exposure_before)} to {usd(g.policy.worst_case_exposure())}.",
         allocations=alloc, exposure_before=exposure_before, exposure_after=g.policy.worst_case_exposure())

    # 2. Rent paid autonomously
    rent = g.run_due_bills()[0]
    step("Oct 1 09:01", "Rent paid on time, without asking", "Risk-priced autonomy", rent.explanation,
         status=rent.status, p_error=rent.risk.p_error, expected_loss=rent.risk.expected_loss, el_limit=rent.risk.el_limit)

    # 3. Fake landlord email
    g.clock.set(at(1, 14, 20))
    a, out = g.handle_message(scen["rent_redirect"]["opening"], scen["rent_redirect"]["sender"])
    step("Oct 1 14:20", "Fake landlord email asks to pay rent to a 'new account'", "Scam Shield + PBM",
         f"Verdict: {a.verdict} ({a.score}). Payment {out.status}. {'; '.join(a.signals)}.",
         protected=a.payment_request.amount, status=out.status)

    # 4. Shopping agents
    g.clock.set(at(2, 11))
    mandate = g.create_mandate("*", "USB-C laptop charger, 65W, under $60", ["charger"], ["electronics"], 6000)
    honest = g.agent_shop("honest", mandate)
    step("Oct 2 11:00", "Shopping agent buys a charger inside the signed mandate", "Agent-of-Agents Firewall",
         f"Cart {usd(honest.cart.total)} approved; one-time token bound to {honest.cart.items[0].seller}; "
         f"payment {honest.payment.status}.", status=honest.payment.status)
    naive = g.agent_shop("naive", mandate)
    failed = [c.name for c in naive.verdict.checks if not c.ok]
    hidden = len([f for f in naive.verdict.findings if f.severity == "high"])
    step("Oct 2 11:05", "Manipulated agent tries a bundle with a hidden subscription", "Agent-of-Agents Firewall",
         f"Cart {usd(naive.cart.total)} rejected ({', '.join(failed)}). {hidden} hidden instructions found on the "
         f"seller page. Note: the cart was within the $60 budget, so a plain spending limit would have approved it. "
         f"Clean alternative offered: {naive.alternative.items[0].name} {usd(naive.alternative.total)}.",
         protected=naive.cart.total, agent_trust=naive.agent_trust)

    # 5. Bills, then the owner reverts one -> autonomy shrinks
    g.clock.set(at(3, 9))
    bills = g.run_due_bills()
    stream = next(b for b in bills if "StreamFlix" in b.explanation)
    rev = g.revert(stream.tx_id)
    step("Oct 3 09:00", "Bills paid; Mia reverses StreamFlix (she cancelled it)", "Earned autonomy",
         f"{len(bills)} bills paid autonomously. After the reversal, Guardian's autonomy limit for "
         f"'subscriptions' halves (trust {rev['new_trust']}).", trust=rev["new_trust"])

    # 6. Consulate impersonation + Scam Baiter + closed loop
    g.clock.set(at(5, 16, 40))
    a, out = g.handle_message(scen["consulate_impersonation"]["opening"], scen["consulate_impersonation"]["sender"])
    step("Oct 5 16:40", "'Consulate' demands a AUD 2,400 'security deposit'", "Scam Shield",
         f"Verdict: {a.verdict} ({a.score}); payment {out.status}.", protected=240000, status=out.status)
    report, added = g.bait("consulate_impersonation")
    step("Oct 5 16:45", "Scam Baiter keeps the scammer busy", "Scam Baiter",
         f"{len(report.turns)} messages, ~{report.scammer_minutes_wasted} scammer-minutes wasted. "
         f"Indicators: {report.indicators_in_opening} in the first message -> {len(added)} after baiting "
         f"(2 mule IBANs, a wallet, phone, handle, phishing link). All added to the blocklist; police report exported.",
         indicators=added, transcript=[t.model_dump() for t in report.turns], police_report=g.police_report(report))
    g.clock.set(at(6, 10))
    mule = g.request_payment(PaymentRequest(
        payee=Payee(name="Inspection Department", account="LT97 7044 0600 0831 5520", country="LT"), amount=150000,
        rail=Rail.INTERNATIONAL_TRANSFER, category="government_fee", memo="case CASE-7781"), pin=NORMAL_PIN)
    step("Oct 6 10:00", "Next day the gang tries the backup account", "Closed loop",
         f"Payment to the second mule account {mule.status}: it was never in any message Mia received, "
         f"it was harvested by the baiter.", protected=150000, status=mule.status)

    # 7. Crypto investment scam
    g.clock.set(at(6, 20))
    a, out = g.handle_message(scen["crypto_investment"]["opening"], scen["crypto_investment"]["sender"])
    step("Oct 6 20:00", "'Student crypto club' promises 3x returns", "Scam Shield",
         f"Verdict: {a.verdict} ({a.score}); payment {out.status}.", protected=50000, status=out.status)

    # 8. Legit request from a friend
    g.clock.set(at(7, 13))
    a, out = g.handle_message("Hey Mia, it's Tom. You owe me $24 for the pizza on Friday, my PayID is "
                              "tom.lee@uni-mail.example. No rush!", "Tom (classmate)")
    ok = g.confirm(out.pending_id, approve=True)
    step("Oct 7 13:00", "Friend asks for $24 for pizza", "Low friction",
         f"Verdict: {a.verdict}. Requests from others always need one tap; Mia approved and it was {ok.status}.",
         status=ok.status)

    # 9. Account takeover at night
    g.clock.set(at(8, 3, 12))
    ato = g.request_payment(PaymentRequest(payee=Payee(name="Fast Cash", account="payid:fastcash88@mail.example"),
                                           amount=120000, rail=Rail.DOMESTIC_TRANSFER, category="unknown"))
    step("Oct 8 03:12", "Stolen session tries to send $1,200 at 3 a.m.", "PBM + autonomy",
         ato.explanation, protected=120000, status=ato.status)

    # 10. Duress
    g.clock.set(at(9, 22, 40))
    before = g.bank.total
    d = g.request_payment(PaymentRequest(payee=Payee(name="J. Smith", account="BSB 012-345 ACC 99887766"),
                                         amount=80000, rail=Rail.DOMESTIC_TRANSFER, category="personal",
                                         memo="loan"), pin=DURESS_PIN)
    decoy_view = g.account_view(decoy=True)
    step("Oct 9 22:40", "Mia is forced to transfer $800 and enters her duress PIN", "Duress Mode",
         f"The coercer sees '{d.receipt['title']}' ({d.receipt['reference']}) and the balance drop to "
         f"{usd(decoy_view['total'])}. In reality {usd(g.bank.quarantine)} is frozen inside the bank "
         f"({'nothing left the bank' if g.bank.total == before else 'WARNING: money left the bank'}) and "
         f"{len(g.duress.contacts)} trusted contacts got a silent alert.",
         protected=80000, receipt=d.receipt, alerts=[m for m in g.outbox if m["kind"] == "duress"])
    g.clock.set(at(10, 9))
    res = g.resolve_incident()
    step("Oct 10 09:00", "Mia is safe and releases the frozen money", "Duress Mode",
         f"{usd(res['released'])} returned to her envelopes.")

    # 11. Audit integrity
    v = g.audit_verify()
    tampered = [e.model_copy(deep=True) for e in g.audit.entries]
    target = next(e for e in tampered if e.event == "payment.executed")
    target.data["amount"] = 1
    bad = verify_chain(tampered, g.audit.public_key)
    step("Oct 10 09:05", "Auditor verifies the flight recorder", "Tamper-evident audit log",
         f"{v.checked} signed entries verified (head {v.head_hash[:16]}...). A copy with one edited amount fails at "
         f"entry #{bad.first_bad_seq}: {bad.reason}.")

    summary = {
        "steps": len(steps),
        "scam_and_attack_money_protected": sum(s["money_protected"] for s in steps),
        "bank_total_end": g.bank.total,
        "audit_entries": v.checked,
        "audit_ok": v.ok,
        "risk_model": g.autonomy.model.report,
        "blocklist_size": len(g.blocklist.items()),
    }
    result = {"summary": summary, "steps": steps}
    if out_dir:
        write_evidence(g, result, out_dir)
    return result


def write_evidence(g: Guardian, result: dict, out_dir: Path) -> Path:
    run_dir = out_dir / "demo_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    g.audit.export_jsonl(run_dir / "audit_log.jsonl")
    (run_dir / "audit_public_key.txt").write_text(g.audit.public_key + "\n")
    (run_dir / "audit_verification.json").write_text(g.audit_verify().model_dump_json(indent=2))
    (run_dir / "audit_checkpoint.json").write_text(json.dumps(g.audit.checkpoint(), indent=2))
    (run_dir / "steps.json").write_text(json.dumps(result, indent=2, default=str))
    (run_dir / "ledger_final.json").write_text(json.dumps({
        "snapshot": g.bank.snapshot(),
        "transactions": [t.model_dump(mode="json") for t in g.bank.transactions]}, indent=2))
    (run_dir / "blocklist.json").write_text(json.dumps(g.blocklist.items(), indent=2))
    (run_dir / "outbox_alerts.json").write_text(json.dumps(g.outbox, indent=2))
    lines = ["# Execution evidence: 'A month in Mia's life'", "",
             "Sandbox bank, simulated clock, fictional persona. Generated by `python -m guardian.demo --out evidence`.",
             "", f"* Steps: {result['summary']['steps']}",
             f"* Money protected from scams and attacks: {usd(result['summary']['scam_and_attack_money_protected'])}",
             f"* Audit log: {result['summary']['audit_entries']} signed entries, verification ok = "
             f"{result['summary']['audit_ok']}", f"* Risk model: {json.dumps(result['summary']['risk_model'])}", "",
             "| # | When | Event | Module | Result |", "|---|---|---|---|---|"]
    for s in result["steps"]:
        lines.append(f"| {s['n']} | {s['when']} | {s['title']} | {s['feature']} | {s['result'].replace('|', '/')} |")
    (run_dir / "REPORT.md").write_text("\n".join(lines) + "\n")
    return run_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("evidence"))
    args = ap.parse_args()
    result = run(args.out)
    for s in result["steps"]:
        print(f"[{s['when']}] {s['title']}\n    {s['feature']}: {s['result']}\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
