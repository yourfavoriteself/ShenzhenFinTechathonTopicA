"""Run the evaluation set: functional tasks and attacks, with pass rates by scenario."""

from __future__ import annotations

import time
import traceback
from typing import Any

from .tasks import TASKS, Chat


def run_functional(tasks=None) -> list[dict[str, Any]]:
    rows = []
    for t in tasks or TASKS:
        t0 = time.perf_counter()
        try:
            t.run(Chat())
            ok, why = True, ""
        except Exception as e:  # noqa: BLE001  a failed task is a result, not a crash
            frame = traceback.extract_tb(e.__traceback__)[-1]
            ok, why = False, f"{type(e).__name__}: {str(e)[:200]} (line {frame.lineno})"
        rows.append({"id": t.id, "scenario": t.scenario, "title": t.title, "passed": ok, "error": why,
                     "seconds": round(time.perf_counter() - t0, 2)})
    return rows


def by_scenario(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = out.setdefault(r["scenario"], {"tasks": 0, "passed": 0})
        s["tasks"] += 1
        s["passed"] += r["passed"]
    for s in out.values():
        s["pct"] = round(100 * s["passed"] / s["tasks"], 1)
    return out
