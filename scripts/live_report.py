"""Live run with real models: the month's cost, the functional evaluation and the model attacker.

    python -m scripts.live_report --out evidence/live_runs.json
    python -m scripts.live_report --attacker-only --rounds 5 --width 6   # attacker on the level 2 model only

With --attacker-only the attacker runs alone on whatever model level 2 is (for example an open model via
GUARDIAN_MODEL_L2=openai_compat:deepseek-v4-pro:2:8), one goal per thread, and the result is added to the
existing report under "attacker_open_model".

Needs a key in the environment (ANTHROPIC_API_KEY, and ANTHROPIC_BASE_URL for a gateway). Spending stops
at GUARDIAN_BUDGET_USD (25 by default); the model attacker stops at $3. The stub and replay runs stay
the reproducible baseline; this file records what changes when the models are real.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
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


def open_attacker(rounds: int, width: int) -> dict:
    gws = [_gateway() for _ in adaptive.GOALS]

    def one(i: int) -> dict:
        g = adaptive.run_goal(adaptive.GOALS[i], lambda env: adaptive.ModelAttacker(env), rounds, width, gws[i])
        print(json.dumps({k: g.get(k) for k in ("goal", "attempts", "successes", "outcomes", "attacker_stopped")}),
              flush=True)
        return g

    with ThreadPoolExecutor(len(gws)) as pool:
        goals = list(pool.map(one, range(len(gws))))
    calls = [r for gw in gws for r in gw.log]
    return {"model": gws[0].ladder[2].model, "rounds": rounds, "width": width,
            "goals": [{k: g.get(k) for k in ("goal", "title", "attempts", "rounds", "successes", "success_curve",
                                             "within_mandate_executions", "outcomes", "closest", "attacker_stopped")}
                      for g in goals],
            "with_kernel": {"out_of_mandate_successes": sum(g["successes"] for g in goals),
                            "within_mandate_executions": sum(g["within_mandate_executions"] for g in goals),
                            "attempts": sum(g["attempts"] for g in goals)},
            "attacker_calls": sum(1 for r in calls if r.level >= 2), "critic_calls": sum(1 for r in calls if r.level == 1),
            "cost_usd": round(sum(r.cost_usd for r in calls), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/live_runs.json"))
    ap.add_argument("--attacker-only", action="store_true")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--width", type=int, default=6)
    a = ap.parse_args()
    if a.attacker_only:
        report = json.loads(a.out.read_text()) if a.out.exists() else {}
        att = open_attacker(a.rounds, a.width)
        report["attacker_open_model"] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **att}
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(json.dumps({"model": att["model"], **att["with_kernel"], "cost_usd": att["cost_usd"],
                          "stopped": [g["attacker_stopped"] for g in att["goals"] if g.get("attacker_stopped")]}, indent=1))
        return
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
