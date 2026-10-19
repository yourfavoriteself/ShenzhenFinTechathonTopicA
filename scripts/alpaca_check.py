"""Check the Alpaca paper account without placing orders.

    python -m scripts.alpaca_check      # keys from ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY or ~/.alpaca_keys
"""

from __future__ import annotations

import sys

from kernel.broker import BrokerError
from kernel.brokers_alpaca import KEYS_FILE, AlpacaBroker, load_keys


def dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def main() -> int:
    if load_keys() is None:
        print(f"No Alpaca keys. Put \"KEY_ID SECRET\" into {KEYS_FILE} or set ALPACA_API_KEY_ID and "
              "ALPACA_API_SECRET_KEY.")
        return 2
    broker = AlpacaBroker.from_keys(timeout=10.0)
    try:
        acct, clock, spy = broker.account(), broker.clock(), broker.quote("SPY")
    except BrokerError as e:
        print(f"{e}. If the connection times out, the API may be blocked on this network: turn on a VPN.")
        return 1
    print(f"account: {acct.status}, cash {dollars(acct.cash_cents)}, buying power {dollars(acct.buying_power_cents)}")
    print(f"market: {'open' if clock.is_open else 'closed'}, next open {clock.next_open:%Y-%m-%d %H:%M %Z}, "
          f"next close {clock.next_close:%Y-%m-%d %H:%M %Z}")
    print(f"SPY: {dollars(spy.price_cents)} at {spy.at:%Y-%m-%d %H:%M:%S %Z} ({spy.source})" if spy
          else "SPY: no quote")
    return 0


if __name__ == "__main__":
    sys.exit(main())
