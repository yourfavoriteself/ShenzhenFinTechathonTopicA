"""What one client's month costs in model tokens: the first month, when plans are built, and the next one,
when templates and standing permissions do the work. The projection to a million clients uses the second.

    python -m scripts.cost_report --out evidence/costs.json

In stub and replay modes tokens are counted on the real prompts and the stub answers, and priced at the
gateway's price list; a live run replaces the estimate with measured usage.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from app.service import GuardianService
from data.generator import build_world
from kernel.crypto import DeviceKey
from kernel.llm_gateway import LLMGateway

MONTH = ["Pay for Melodify", "Find leaks in my subscriptions", "Was I charged twice?", "Pay my bills",
         "How much can I spend today?", "Can I afford headphones for $181.05?", "Month review"]
CLIENTS = 1_000_000


class Run:
    def __init__(self, gw: LLMGateway):
        self.svc = GuardianService(build_world(), gw)
        self.gw = gw
        self.dev = DeviceKey(seed="cost:device")
        self.svc.register_device(self.dev.alg, self.dev.public_b64)

    def sign_all(self, items, standing: bool):
        out = []
        for it in [i for i in items if i.type == "card"]:
            c = it.data["card"]
            st = standing and c.get("standing_offer")
            h = c["standing_mandate_hash"] if st else c["mandate_hash"]
            out += self.svc.confirm(it.data["pending_id"], self.dev.alg, self.dev.public_b64,
                                    self.dev.sign_mandate(h), standing=bool(st))
        return out

    def task(self, text: str, standing: bool = True) -> dict:
        n = len(self.gw.log)
        items = self.svc.chat(text)
        if text.startswith("Pay for"):
            self.sign_all(items, standing)
        rs = self.gw.log[n:]
        return {"task": text, "calls": len(rs), "levels": sorted({r.level for r in rs}),
                "input_tokens": sum(r.input_tokens for r in rs), "output_tokens": sum(r.output_tokens for r in rs),
                "cost_usd": round(sum(r.cost_usd for r in rs), 6),
                "big_model_calls": sum(1 for r in rs if r.level >= 2)}


def month(run: Run) -> dict:
    rows = [run.task(t) for t in MONTH]
    return {"tasks": rows, "calls": sum(r["calls"] for r in rows),
            "big_model_calls": sum(r["big_model_calls"] for r in rows),
            "tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 6)}


def report(mode: str = "stub") -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="cost-"))
    gw = LLMGateway(mode, spend_path=tmp / "spend.json", runs_dir=None if mode != "stub" else tmp / "runs")
    run = Run(gw)
    first = month(run)
    run.svc.advance(31)
    second = month(run)
    per_client = second["cost_usd"]
    by_level: dict[int, int] = {}
    for r in gw.log:
        by_level[r.level] = by_level.get(r.level, 0) + 1
    return {
        "mode": mode, "estimated": any(r.estimated for r in gw.log),
        "ladder": {lvl: spec.model for lvl, spec in gw.ladder.items()},
        "ladder_source": gw.config.source,
        "first_month": first, "next_month": second,
        "saving_pct": round(100 * (1 - second["cost_usd"] / first["cost_usd"]), 1) if first["cost_usd"] else 0,
        "calls_by_level": by_level,
        "projection": {"clients": CLIENTS, "tasks_per_client_per_month": len(MONTH),
                       "usd_per_client_per_month": per_client,
                       "usd_per_month": round(per_client * CLIENTS, 2),
                       "assumption": "every client runs the same seven tasks each month, as in the second month; "
                                     "level-0 work (rules, templates, kernel code) costs no tokens"},
        "note": "Token counts on real prompts with stub answers, priced at the gateway's list prices; "
                "a live run replaces this estimate." if mode == "stub" else "Measured on a live run.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="stub", choices=["stub", "live"])
    ap.add_argument("--out", type=Path, default=Path("evidence/costs.json"))
    a = ap.parse_args()
    r = report(a.mode)
    a.out.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print(json.dumps({k: r[k] for k in ("mode", "saving_pct", "projection")}, ensure_ascii=False, indent=1))
    print("first month $" + str(r["first_month"]["cost_usd"]) + ", next month $" + str(r["next_month"]["cost_usd"]))


if __name__ == "__main__":
    main()
