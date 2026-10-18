"""Evidence for C10: mule-account model trained and tested on different synthetic transfer graphs.

    python -m scripts.risk_graph_report --out evidence/risk_graph.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from kernel.clock import MSK
from risk_graph.model import evaluate

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/risk_graph.json"))
    a = ap.parse_args()
    r = evaluate(datetime(2026, 10, 17, 10, tzinfo=MSK))
    r["note"] = ("synthetic graphs from our own generator (risk_graph/generator.py); real bank data will score lower, "
                 "the numbers show the pipeline works, not field accuracy")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print(json.dumps(r, ensure_ascii=False, indent=1))
