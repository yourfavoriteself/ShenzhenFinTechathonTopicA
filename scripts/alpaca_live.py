"""Live run against an Alpaca paper account: the same chat, kernel and broker step as the demo.

    python -m scripts.alpaca_live            # keys from ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY or ~/.alpaca_keys

The client asks to invest $10 a month in SPY and signs the card; the kernel places the order on
Alpaca (a market order in regular hours, a limit order in extended hours, otherwise queued for the
open) and reads it back. Then the agent's off-mandate orders (a meme stock, an option, crypto, an
order above the limit) go to the same kernel and must stop at the broker step. Writes
evidence/alpaca_paper.json with order ids and statuses and the broker call log without keys or the
account number.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from kernel.broker import BrokerError, market_session
from kernel.brokers_alpaca import KEYS_FILE, load_keys
from kernel.crypto import DeviceKey
from kernel.llm_gateway import LLMGateway

ASKS = ["Buy $10 of GME", "Invest $5 in SPY261218C00600000", "Buy $10 of BTC/USD", "Invest $45 in VTI"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/alpaca_paper.json"))
    a = ap.parse_args()
    if load_keys() is None:
        print(f"No Alpaca keys. Put \"KEY_ID SECRET\" into {KEYS_FILE} or set ALPACA_API_KEY_ID and "
              "ALPACA_API_SECRET_KEY.")
        return 2
    os.environ["GUARDIAN_BROKER"] = "alpaca"
    from app.service import GuardianService
    tmp = Path(tempfile.mkdtemp(prefix="alpaca-"))
    svc = GuardianService(gateway=LLMGateway(None, spend_path=tmp / "spend.json"), explain=False)
    inv = svc.k.investing
    dev = DeviceKey(seed="alpaca:device")
    svc.register_device(dev.alg, dev.public_b64)
    started = datetime.now(timezone.utc)
    try:
        clock = inv.broker.clock()
    except BrokerError as e:
        print(f"{e}. If the connection times out, the API may be blocked on this network: turn on a VPN.")
        return 1
    log: list[dict] = []

    def say(text: str) -> list:
        items = svc.chat(text)
        log.extend({"type": i.type, "text": i.text} for i in items if i.type != "user")
        return items

    items = say("Invest $10 a month in SPY")
    card = next(i for i in items if i.type == "card")
    c = card.data["card"]
    done = svc.confirm(card.data["pending_id"], dev.alg, dev.public_b64, dev.sign_mandate(c["standing_mandate_hash"]),
                       standing=True)
    log.extend({"type": i.type, "text": i.text} for i in done)
    for text in ASKS:
        say(text)
    orders = [o.model_dump(mode="json") for o in inv.refresh()]
    v = svc.k.journal.verify()
    report = {
        "run_at": started.isoformat(), "session": market_session(started),
        "market": {"is_open": clock.is_open, "next_open": clock.next_open.isoformat(),
                   "next_close": clock.next_close.isoformat()},
        "broker": "Alpaca paper trading (paper-api.alpaca.markets), quotes from the IEX feed",
        "limits": inv.limits.model_dump(mode="json"), "envelope_cents": inv.envelope_cents,
        "orders": orders, "chat": log,
        "broker_steps": [e.data for e in svc.k.journal.entries if e.event == "pipeline.broker"],
        "journal": {"entries": v.checked, "ok": v.ok, "head": v.head_hash},
        "calls": inv.broker.log,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    for row in log:
        print(f"[{row['type']}] {row['text']}")
    print(f"orders: {[(o['id'], o['symbol'], o['status']) for o in orders]}; journal ok {v.ok}; wrote {a.out}")
    return 0 if orders and v.ok else 1


if __name__ == "__main__":
    sys.exit(main())
