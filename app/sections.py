"""Data for the side sections of the app: what the assistant did this month, payments, trust, agents,
cost, security, red-team and compliance. Everything is read from the kernel, the bank and evidence/;
nothing here can change state."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kernel.money import usd
from kernel.passport import ROLE_ENDPOINTS, Role
from kernel.trust import AGENT_CAP, LEVEL1_LIMIT, PROMOTE_AFTER, START_LEVEL, STRIKE_LIMIT, AgentStatus

if TYPE_CHECKING:
    from .service import GuardianService

EVIDENCE = Path(__file__).resolve().parent.parent / "evidence"

# 智能体支付应用自律公约 (Payment & Clearing Association of China, 24 Aug 2026): requirement → how Guardian meets it
CODE = [
    ("三阶段 Three stages: agent-assisted → autonomous under set conditions → autonomous in broad scope",
     "Stage 1: every payment through a kernel card. Stage 2: standing permissions with limits, amounts, payees, "
     "period and money source. Stage 3 is deliberately not offered. The stage of every operation is in the "
     "journal and in Permissions.", "Permissions, Journal"),
    ("谁提供支付服务谁负责 Whoever provides the payment service is responsible; payments stay with a licensed "
     "institution", "Guardian is a bank module: only the bank's executor moves money; agents only propose intents.",
     "Architecture"),
    ("了解你的智能体 Know your agent (KYA)", "Agent passport: id, role, model and level, hash of instructions, "
     "recipe version, session key; checked at every pipeline step and written to the journal.", "Agents"),
    ("Clear authority boundaries; verify the client's payment intent", "Permission with limits, card rendered by the "
     "kernel, client's device signature over the card hash, critic that sees only the client's words.",
     "Chat cards"),
    ("Client can withdraw authorisation", "Revoke with one press, effective at once (child grants too).",
     "Permissions"),
    ("Prevent over-limit and unauthorised debits", "Amount range, total budget, frequency, single-use grants, "
     "envelopes as the money source.", "Permissions, Red-team"),
    ("Immutable records of authorisation, model decision and payment order", "Hash-chained, signed journal; "
     "append-only table in PostgreSQL; checkpoints anchored on chain.", "Journal, Security"),
    ("Human support, fallback, emergency stop, rollback", "Live analyst for scam-call holds, 'do it myself' on every "
     "card, kernel kill switch, 24-hour reversible hold.", "Security"),
    ("Stricter rules for the elderly and minors", "Elevated protection: every external payment needs a card, the "
     "scam-call ladder starts at the pause, a trusted person is told.", "Trust"),
    ("No abuse, algorithmic discrimination or excessive inducement to spend", "The assistant sells nothing: no "
     "credit, instalments or upgrades; advice comes only from the kernel's savings calculations; credit offers in "
     "model text are replaced.", "Red-team"),
    ("Assess effectiveness, safety and ethics before autonomous modes", "Evaluation set (effectiveness), red-team and "
     "security report (safety), ethics section in the documentation.", "Red-team"),
]

# Mastercard Verifiable Intent constraint types and Google AP2 mandates → Guardian's mandate
VI_AP2 = [
    ("Intent mandate (AP2)", "Standing permission: actions, allowed payees, amount range, period, money source, "
     "expiry, signed by the client's device"),
    ("Cart and payment mandates (AP2)", "One-time mandate per card; `payment.reference` = hash of the card shown"),
    ("Constraint types (VI)", "allowed_payees, amount_range, budget, agent_recurrence, reference; our additions: "
     "allowed_origins, funding_source"),
    ("Delegation", "Child grants derived from a mandate, rights only narrow, single use, held by one agent passport"),
    ("Signatures", "Ed25519 or ECDSA P-256 device key (WebCrypto, non-extractable) plus the kernel's countersignature; "
     "JSON, no SD-JWT selective disclosure yet"),
]


def _evidence(name: str) -> dict[str, Any] | None:
    p = EVIDENCE / name
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def month(svc: "GuardianService") -> dict[str, Any]:
    items = svc.items
    receipts = [i for i in items if i.type == "receipt"]
    cancelled = [c for c in svc.cancels.items.values() if c.status == "verified"]
    blocked = [b for b in svc.bank.blocks if b.client_id == svc.client]
    refunds = [t for t in svc.bank.transactions(svc.client) if t.status == "refunded"]
    cof = {c.merchant: c.amount for c in svc.bank.cof.values() if c.client_id == svc.client}
    saved_monthly = sum(c.amount or 0 for c in cancelled) + sum(cof.get(b.merchant, 0) for b in blocked
                                                               if b.merchant not in {c.merchant for c in cancelled})
    events = Counter(e.event for e in svc.k.journal.entries)
    goals = [{"name": g.goal.name, "target": usd(g.goal.target), "saved": usd(g.goal.saved),
              "monthly": usd(g.monthly), "date": g.projected_date.isoformat(),
              "pct": round(100 * g.goal.saved / g.goal.target, 1) if g.goal.target else 0} for g in svc.goals]
    return {
        "saved_per_month": usd(saved_monthly), "saved_per_year": usd(saved_monthly * 12),
        "refunded": usd(sum(t.amount for t in refunds)),
        "done": len(receipts), "notifications_only": sum(1 for r in receipts if r.data.get("notify_only")),
        "asked": events["card.shown"],
        "stopped": sum(1 for i in items if i.type == "denied") + len(svc.desk.cases)
        + sum(1 for h in svc.guard.holds.values() if h.status != "released"),
        "cancelled": [c.merchant_name for c in cancelled], "goals": goals,
    }


def payments(svc: "GuardianService") -> dict[str, Any]:
    subs = []
    for c in svc.bank.cof.values():
        if c.client_id != svc.client:
            continue
        status = "blocked" if svc.bank.is_blocked(svc.client, c.merchant, c.card_id) else \
            "trial" if c.trial_until and c.active else "active" if c.active else "cancelled"
        subs.append({"name": svc._name(c.merchant), "card": "*" + c.card_id[-4:], "amount": usd(c.amount),
                     "next": c.next_date.isoformat(), "status": status})
    bills = [{"subject": d.subject, "verdict": svc.bill_checks[d.id].verdict if d.id in svc.bill_checks else "-",
              "amount": usd(svc.bill_checks[d.id].amount) if d.id in svc.bill_checks and svc.bill_checks[d.id].amount
              else "-", "paid": d.id in svc.paid_docs} for d in svc.inbox() if d.kind == "bill"]
    agent = [{"date": t.ts.date().isoformat(), "to": t.counterparty_name or svc._name(t.counterparty),
              "amount": usd(t.amount), "from": t.envelope_id or t.account_id}
             for t in svc.bank.transactions(svc.client) if t.initiator == "agent"][-15:][::-1]
    return {"subscriptions": sorted(subs, key=lambda s: s["status"]), "bills": bills, "agent_payments": agent}


def trust(svc: "GuardianService") -> dict[str, Any]:
    t = svc.k.trust
    t.refresh()
    return {"elevated_protection": t.elevated_protection,
            "levels": [{"category": c.value, "level": t.level(c), "streak": ct.streak}
                       for c, ct in t.categories.items()],
            "agents": [{"agent": r.agent, "role": r.role, "model": r.model, "status": r.status.value,
                        "until": r.until.isoformat() if r.until else None, "strikes": r.strikes,
                        "verified": r.verified, "penalties": r.penalties, "cap": AGENT_CAP[r.status]}
                       for r in t.agents.values()],
            "history": [_trust_row(h) for h in t.history[-30:]][::-1],
            "promotions": sum(1 for h in t.history if h.level_after is not None and h.level_after > h.level_before),
            "demotions": sum(1 for h in t.history if h.level_after is not None and h.level_after < h.level_before),
            "quarantines": sum(1 for h in t.history if h.status_after == AgentStatus.QUARANTINED),
            "rules": f"Each kind of task starts at level {START_LEVEL}. Level 0: a card for everything. Level 1: "
                     f"alone up to {usd(LEVEL1_LIMIT)} inside a standing permission. Level 2: alone up to the "
                     "permission's limit. "
                     f"Up after {PROMOTE_AFTER} verified results in a row; down one level on a mistake; to 0 on a "
                     f"scope violation. Agents collect strikes (a scope violation is two); {STRIKE_LIMIT} strikes "
                     "mean 24 hours of quarantine, then probation."}


def _trust_row(h) -> dict[str, Any]:
    if h.category is None:
        what, change = h.event.split(".", 1)[1], f"{h.status_before.value} → {h.status_after.value}"
    else:
        what, change = h.event.replace("_", " "), f"{h.level_before} → {h.level_after}"
    moved = (h.category is None) or h.level_after != h.level_before
    return {"at": h.at.isoformat(), "category": h.category.value.replace("_", " ") if h.category else "agent",
            "agent": (h.agent or "").split(":")[0], "event": what, "change": change, "moved": moved,
            "strikes": h.strikes, "reason": h.reason}


def agents(svc: "GuardianService") -> dict[str, Any]:
    ps = list(svc.k.passports._passports.values())
    tokens: dict[str, int] = Counter()
    for r in svc.gw.log:
        tokens[r.task_id] += r.input_tokens + r.output_tokens
    rows = [{"id": p.id, "role": p.role.value, "model": p.model, "level": p.model_level, "task": p.task_id,
             "issued": p.issued_at.isoformat(), "expires": p.exp.isoformat(),
             "instructions": p.instructions_hash[:12], "tokens": tokens.get(p.task_id, 0)} for p in ps[-20:]][::-1]
    decisions = [{"at": str(e.ts), "outcome": e.event.split(".", 1)[1], "action": e.data.get("action"),
                  "payee": e.data.get("payee"), "risk": e.data.get("risk"), "stage": e.stage,
                  "reasons": e.data.get("reasons", [])[:2]}
                 for e in svc.k.journal.entries if e.event.startswith("decision.")][-12:][::-1]
    return {"passports": len(ps), "by_role": dict(Counter(p.role.value for p in ps)), "recent": rows,
            "critic": {"calls": svc.critic.calls, "cache_hits": svc.critic.cache_hits}, "decisions": decisions,
            "counterfactual_runs": svc.cf.runs,
            "roles": {r.value: sorted(e) for r, e in ROLE_ENDPOINTS.items() if r != Role.BROWSER}}


def cost(svc: "GuardianService") -> dict[str, Any]:
    rep = svc.gw.report()
    calls = sum(v["calls"] for v in rep["by_level"].values())
    cheap = sum(v["calls"] for k, v in rep["by_level"].items() if int(k) <= 1)
    tpl = sum(t.uses for t in svc.templates.for_client(svc.client))
    tasks = sum(1 for e in svc.k.journal.entries if e.event == "task.opened")
    kernel_only = max(0, tasks - len({r.task_id for r in svc.gw.log}))
    est = _evidence("costs.json")
    return {"mode": rep["mode"], "spent_usd": rep["spent_total_usd"], "cap_usd": rep["cap_usd"],
            "by_level": rep["by_level"], "model_calls": calls,
            "share_level_0_1": round(100 * (cheap + kernel_only) / max(1, calls + kernel_only), 1),
            "tasks": tasks, "tasks_without_any_model": kernel_only, "template_repeats": tpl,
            "months": est and [{"month": name, "calls": est[k]["calls"], "big_model_calls": est[k]["big_model_calls"],
                                "tokens": est[k]["tokens"], "usd": est[k]["cost_usd"]}
                               for name, k in (("first", "first_month"), ("next", "next_month"))],
            "repeat": est and [{"task": a["task"], "first_usd": a["cost_usd"], "next_usd": b["cost_usd"],
                                "first_levels": a["levels"], "next_levels": b["levels"]}
                               for a, b in zip(est["first_month"]["tasks"], est["next_month"]["tasks"])],
            "saving_pct": est and est["saving_pct"], "projection": est and est["projection"],
            "note": (est or {}).get("note", "Run make evidence for the month estimate.")}


def security(svc: "GuardianService") -> dict[str, Any]:
    labels: Counter = Counter()
    for e in svc.k.journal.entries:
        if e.event == "intent.proposed":
            labels.update(e.data.get("labels", {}).values())
    denied = [{"text": i.text[:160], "reasons": (i.data.get("reasons") or [])[:3]}
              for i in svc.items if i.type == "denied"][-8:][::-1]
    holds = [{"id": h.id, "level": h.level, "status": h.status, "duress": h.duress,
              "release_at": h.release_at.isoformat() if h.release_at else None,
              "amount": usd(h.intent.val("amount") or 0)} for h in svc.guard.holds.values()]
    v = svc.k.journal.verify()
    return {"cases": [{"title": c.title, "sender": c.sender, "indicators": c.indicators[:6],
                       "recommendation": c.recommendation} for c in svc.desk.cases],
            "suspicious_events": len(svc.k.memory.for_client(svc.client)),
            "provenance_labels": dict(labels), "denied": denied, "holds": holds,
            "alerts": [{"text": a.get("text", ""), "silent": a.get("silent")} for a in svc.alerts][-5:],
            "journal": {"ok": v.ok, "entries": v.checked, "head": (v.head_hash or "")[:16]},
            "graph_model": _evidence("risk_graph.json"), "anchors": _evidence("anchors.json")}


def redteam() -> dict[str, Any]:
    rt, ev = _evidence("redteam.json"), _evidence("eval_report.json")
    out: dict[str, Any] = {"available": rt is not None}
    if rt:
        def block(b):
            return {"attacks": b["attacks"], "stopped": b["blocked"], "by_outcome": b["by_outcome"],
                    "lost": usd(b["lost_cents"]), "without_kernel_succeeded": b["without_kernel"]["succeeded"],
                    "without_kernel_lost": usd(b["without_kernel"]["lost_cents"])}
        ad, lg = rt["adaptive"], rt["legacy_prototype"]
        out |= {"note": rt["note"], "static": block(rt["static"]), "retention": block(rt["retention"]),
                "adaptive": {"attacker": ad["attacker"], "rounds": ad["rounds"], "width": ad["width"],
                             "attempts": ad["with_kernel"]["attempts"],
                             "out_of_mandate": ad["with_kernel"]["out_of_mandate_successes"],
                             "without_kernel_goals_reached": ad["without_kernel"]["goals_reached"],
                             "goals": [{k: g[k] for k in ("goal", "title", "attempts", "successes", "success_curve")}
                                       for g in ad["goals"]]},
                "legacy": {"total": lg["attacks"], "stopped": lg["prevented"], "bounded": lg["bounded"],
                           "succeeded": lg["succeeded"]},
                "attacks": [{k: a[k] for k in ("id", "group", "goal", "title", "outcome", "stopped_by")}
                            | {"without_kernel_lost": usd(a["without_kernel"]["lost_cents"])} for a in rt["attacks"]]}
    if ev:
        out["eval"] = {"functional": {k: ev["functional"][k] for k in ("tasks", "passed", "pct", "by_scenario")},
                       "attacks": {k: ev["attacks"][k] for k in ("attacks", "stopped", "pct")}}
    return out


def compliance(svc: "GuardianService") -> dict[str, Any]:
    stages = Counter(e.stage for e in svc.k.journal.entries if e.event == "mandate.signed")
    return {"code": [{"requirement": r, "how": h, "where": w} for r, h, w in CODE],
            "vi_ap2": [{"theirs": a, "ours": b} for a, b in VI_AP2],
            "stages": {f"stage {k}": v for k, v in sorted(stages.items(), key=lambda x: x[0] or 0)},
            "stage_3": "not offered by design"}


def all_sections(svc: "GuardianService") -> dict[str, Any]:
    with svc.lock:
        return {"month": month(svc), "payments": payments(svc), "trust": trust(svc), "agents": agents(svc),
                "cost": cost(svc), "security": security(svc), "redteam": redteam(), "compliance": compliance(svc)}
