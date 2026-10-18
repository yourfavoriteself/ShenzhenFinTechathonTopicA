"""Evaluation report: functional tasks and attacks with pass rates.

    python -m scripts.eval_report --out evidence/eval_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evalset.run import by_scenario, run_functional
from redteam import adaptive
from redteam.report import run_static


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/eval_report.json"))
    args = ap.parse_args()
    fn = run_functional()
    attacks = run_static()
    adapt = adaptive.run()
    goals = [{"id": f"A-{g['goal']}", "goal": g["title"], "blocked": g["successes"] == 0, "attempts": g["attempts"]}
             for g in adapt["goals"]]
    n_att = len(attacks) + len(goals)
    stopped = sum(a["blocked"] for a in attacks) + sum(g["blocked"] for g in goals)
    report = {
        "mode": "LLM stub (deterministic); live numbers are added when a key is available",
        "functional": {"tasks": len(fn), "passed": sum(r["passed"] for r in fn),
                       "pct": round(100 * sum(r["passed"] for r in fn) / len(fn), 1), "by_scenario": by_scenario(fn),
                       "rows": fn},
        "attacks": {"attacks": n_att, "stopped": stopped, "pct": round(100 * stopped / n_att, 1),
                    "static_and_retention": len(attacks), "adaptive_goals": goals,
                    "rows": [{k: a[k] for k in ("id", "group", "goal", "outcome", "blocked", "stopped_by")}
                             for a in attacks]},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    f, a = report["functional"], report["attacks"]
    print(f"functional {f['passed']}/{f['tasks']} ({f['pct']}%), attacks stopped {a['stopped']}/{a['attacks']} "
          f"({a['pct']}%)")
    for r in fn:
        if not r["passed"]:
            print("  FAIL", r["id"], r["title"], r["error"])


if __name__ == "__main__":
    main()
