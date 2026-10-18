"""Red-team report: static and retention attacks, adaptive search, and the 21 legacy attacks.

    python -m scripts.redteam_report --out evidence/redteam.json [--live]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from redteam import adaptive
from redteam.report import legacy, run_static, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/redteam.json"))
    ap.add_argument("--live", action="store_true", help="model attacker through the gateway, capped at 300 RUB")
    args = ap.parse_args()
    rows = run_static()
    static = [r for r in rows if r["group"] == "static"]
    retention = [r for r in rows if r["group"] == "retention"]
    report = {
        "note": "Attacks run against the real kernel on a fresh sandbox bank each. The attacker controls an agent "
                "(valid passport, any intent, any cited reference, any memo). The client is attentive: signs a card "
                "only if it shows the payee and amount they asked for. 'Without kernel' is the unguarded baseline: "
                "the agent's proposal is executed as is.",
        "static": summary(static), "retention": summary(retention),
        "adaptive": adaptive.run(live=args.live),
        "legacy_prototype": legacy(),
        "attacks": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    s, r, a = report["static"], report["retention"], report["adaptive"]["with_kernel"]
    print(f"static {s['blocked']}/{s['attacks']}, retention {r['blocked']}/{r['attacks']}, adaptive out-of-mandate "
          f"{a['out_of_mandate_successes']} in {a['attempts']} attempts")


if __name__ == "__main__":
    main()
