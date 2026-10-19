"""Live run with real models: the month's cost, the functional evaluation and the model attacker.

    python -m scripts.live_report --out evidence/live_runs.json

Needs a key in the environment (ANTHROPIC_API_KEY, and ANTHROPIC_BASE_URL for a gateway). Spending stops
at GUARDIAN_BUDGET_USD (25 by default); the model attacker stops at $3. The stub and replay runs stay
the reproducible baseline; this file records what changes when the models are real.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from evalset import tasks as T
from evalset.run import run_functional
from kernel.llm_gateway import LLMGateway
from redteam import adaptive
from redteam.harness import Env

from . import cost_report


def _gateway() -> LLMGateway:
    return LLMGateway("live", spend_path=Path(tempfile.mkdtemp(prefix="live-")) / "spend.json")


def functional() -> dict:
    gw, stops = _gateway(), {}
    init, say = T.Chat.__init__, T.Chat.say

    def chat_init(self, env=None):
        init(self, env or Env(gw))

    def chat_say(self, text):
        items = say(self, text)
        for i in items:
            if i.type == "denied":
                stops.setdefault(text, i.text[:240])
        return items

    T.Chat.__init__, T.Chat.say = chat_init, chat_say
    try:
        rows = run_functional()
    finally:
        T.Chat.__init__, T.Chat.say = init, say
    return {"tasks": len(rows), "passed": sum(r["passed"] for r in rows),
            "calls": len(gw.log), "calls_by_level": {str(n): sum(1 for r in gw.log if r.level == n) for n in (1, 2, 3)},
            "cost_usd": round(sum(r.cost_usd for r in gw.log), 4),
            "failed": [{k: r[k] for k in ("id", "scenario", "title", "error")} for r in rows if not r["passed"]],
            "stops_seen": [{"request": k, "stopped": v} for k, v in stops.items()]}


def attacker() -> dict:
    r = adaptive.run(live=True, gateway=_gateway(), rounds=2, width=4)
    return {"goals": [{k: g.get(k) for k in ("goal", "title", "attempts", "successes", "outcomes", "attacker_stopped")}
                      for g in r["goals"]], "with_kernel": r["with_kernel"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/live_runs.json"))
    a = ap.parse_args()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cost = cost_report.report("live")
    fn = functional()
    att = attacker()
    gw = LLMGateway("live", spend_path=Path(tempfile.mkdtemp()) / "s.json")
    report = {
        "run_at": started, "ladder": {str(k): v.model for k, v in gw.ladder.items()},
        "cost": {"first_month_usd": cost["first_month"]["cost_usd"], "next_month_usd": cost["next_month"]["cost_usd"],
                 "saving_pct": cost["saving_pct"], "calls_by_level": cost["calls_by_level"],
                 "usd_per_month_1m_clients": cost["projection"]["usd_per_month"]},
        "functional": fn, "attacker": att,
        "spent_usd": round(cost["first_month"]["cost_usd"] + cost["next_month"]["cost_usd"] + fn["cost_usd"], 4),
    }
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (a.out.parent / "costs_live.json").write_text(json.dumps(cost, ensure_ascii=False, indent=1))
    print(json.dumps({"cost": report["cost"], "functional": f"{fn['passed']}/{fn['tasks']}",
                      "attacker": att["with_kernel"], "spent_usd": report["spent_usd"]}, indent=1))


if __name__ == "__main__":
    main()
