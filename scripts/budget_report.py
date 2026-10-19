"""Stage evidence for analytics and budget: planted problems found, forecast quality, cash-gap backtest.

    python -m scripts.budget_report --out evidence/budget_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.generator import CLIENT, MAIN, build_world, export
from kernel import budget as B
from kernel.analytics import Usage, double_charges, leaks, recurring
from kernel.money import usd


def report(seed: int = 22) -> dict:
    w = build_world(seed)
    b, now = w.bank, w.clock.now()
    txs = b.transactions(CLIENT)
    series = recurring(txs, b.cards_on_file(CLIENT), w.merchants, now)
    lk = leaks(series, [Usage(**u.model_dump()) for u in w.usage], now)
    planted = {(x["kind"], x["merchant"]) for x in w.truth.leaks}
    found = {(x.kind, x.merchant) for x in lk.leaks}
    dc = double_charges(txs)
    q = B.evaluate_budget(txs, now)
    probes = B.probe_dates(txs, now)
    bt = B.backtest_cash_gaps(txs, b.cards_on_file(CLIENT), w.merchants, MAIN, b.accounts[MAIN].balance, probes,
                              [g.due for g in w.truth.cash_gaps])
    return {
        "world": {"seed": seed, "transactions": len(txs), "as_of": now.isoformat()},
        "leaks": {"planted": len(planted), "found": len(found & planted), "extra": len(found - planted),
                  "monthly_total": usd(lk.monthly_total), "yearly_total": usd(lk.yearly_total), "calc": lk.calc},
        "double_charge": {"planted": 1, "found": sum(1 for d in dc if [d.first, d.second] == w.truth.double_charge)},
        "budget_forecast": {"train": q.train, "test": q.test, "mape_by_envelope_pct": q.mape,
                            "overall_wape_pct": q.overall_wape},
        "cash_gaps": bt.model_dump(),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/budget_report.json"))
    a = ap.parse_args()
    r = report()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    export(build_world(), a.out.parent / "world.json")
    print(json.dumps(r, ensure_ascii=False, indent=1))
