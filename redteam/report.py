"""Run every attack and summarise: with the kernel, and without it (the agent's proposal executed as is)."""

from __future__ import annotations

from typing import Any

from .harness import BLOCKED, Env
from .static import RETENTION, STATIC


def run_static(attacks=None) -> list[dict[str, Any]]:
    out = []
    for a in attacks or STATIC + RETENTION:
        r = a.run(Env())
        out.append({"id": a.id, "group": a.group, "goal": a.goal, "title": a.title, "source": a.source,
                    "outcome": r.outcome, "blocked": r.outcome in BLOCKED, "lost_kop": r.lost,
                    "out_of_mandate": r.out_of_mandate, "stopped_by": r.stopped_by, "detail": r.detail,
                    "without_kernel": {"outcome": a.no_kernel, "lost_kop": a.no_kernel_loss}})
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, int] = {}
    for r in rows:
        by[r["outcome"]] = by.get(r["outcome"], 0) + 1
    return {"attacks": len(rows), "blocked": sum(r["blocked"] for r in rows),
            "blocked_pct": round(100 * sum(r["blocked"] for r in rows) / max(1, len(rows)), 1),
            "by_outcome": by, "lost_kop": sum(r["lost_kop"] for r in rows),
            "out_of_mandate": sum(r["out_of_mandate"] for r in rows),
            "without_kernel": {"succeeded": len(rows), "lost_kop": sum(r["without_kernel"]["lost_kop"] for r in rows)}}


def legacy() -> dict[str, Any]:
    """The 21 attacks of the earlier prototype engine (guardian/), kept as regression evidence."""
    from guardian.redteam import run
    return run()["summary"]
