"""A deterministic stand-in for an Alpaca paper account, used in replay mode and in tests.

Prices follow a seeded walk per security and minute. The market is open 9:30 to 16:00 New York
time on weekdays (holidays are ignored), with extended hours from 4:00 to 20:00 for limit orders.
Market orders fill at the quote while the market is open; otherwise they wait as "accepted" and
fill at the next open when settle() runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from kernel.broker import Account, Asset, BrokerError, MarketClock, Order, Position, Quote, Side

NY = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)
PRE, POST = time(4, 0), time(20, 0)
SHARE = Decimal("0.000000001")

# symbol: (name, class, exchange, fractionable, base price in cents)
ASSETS: dict[str, tuple[str, str, str, bool, int]] = {
    "SPY": ("SPDR S&P 500 ETF Trust", "us_equity", "ARCA", True, 76_700),
    "VTI": ("Vanguard Total Stock Market ETF", "us_equity", "ARCA", True, 37_550),
    "QQQ": ("Invesco QQQ Trust", "us_equity", "NASDAQ", True, 71_850),
    "BND": ("Vanguard Total Bond Market ETF", "us_equity", "NASDAQ", True, 7_060),
    "GME": ("GameStop Corp", "us_equity", "NYSE", True, 2_650),
    "TLSS": ("Transportation and Logistics Systems", "us_equity", "OTC", False, 2),
    "SPY261218C00600000": ("SPY Dec 18 2026 600 Call", "us_option", "OPRA", False, 16_950),
    "BTC/USD": ("Bitcoin", "crypto", "CRYPTO", True, 6_500_000),
}


def _session(day: datetime) -> tuple[datetime, datetime]:
    return datetime.combine(day.date(), OPEN, NY), datetime.combine(day.date(), CLOSE, NY)


def is_open(at: datetime) -> bool:
    ny = at.astimezone(NY)
    start, end = _session(ny)
    return ny.weekday() < 5 and start <= ny < end


def is_extended(at: datetime) -> bool:
    ny = at.astimezone(NY)
    return ny.weekday() < 5 and PRE <= ny.time() < POST


def next_open(at: datetime) -> datetime:
    ny = at.astimezone(NY)
    day = ny if ny.time() < OPEN else ny + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return _session(day)[0]


def last_close(at: datetime) -> datetime:
    """The moment prices last moved: now while open, otherwise the previous session's close."""
    if is_open(at):
        return at
    ny = at.astimezone(NY)
    day = ny if ny.time() >= CLOSE else ny - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return _session(day)[1]


class SimBroker:
    def __init__(self, clock: Any, cash_cents: int = 0, seed: str = "guardian"):
        self._clock, self._seed = clock, seed
        self.cash_cents = cash_cents
        self.orders: dict[str, Order] = {}
        self._pos: dict[str, tuple[Decimal, int]] = {}   # symbol: (shares, cost in cents)

    # ------------------------------------------------------------------ prices

    def price_at(self, symbol: str, at: datetime) -> int:
        base = ASSETS[symbol][4]
        minute = int(at.timestamp() // 60)
        day = minute // 1440
        h = lambda s: int(hashlib.sha256(f"{self._seed}:{symbol}:{s}".encode()).hexdigest()[:8], 16) / 2**32  # noqa: E731
        drift = sum(h(f"d{d}") - 0.5 for d in range(day - 20, day + 1)) * 0.01   # about 1% a day, 20 days
        noise = (h(f"m{minute}") - 0.5) * 0.002
        return max(1, round(base * (1 + drift + noise)))

    # ------------------------------------------------------------------ Broker

    def account(self) -> Account:
        reserved = sum(o.notional_cents or 0 for o in self.orders.values()
                       if o.status == "accepted" and o.side == "buy")
        return Account(cash_cents=self.cash_cents, buying_power_cents=self.cash_cents - reserved)

    def clock(self) -> MarketClock:
        now = self._clock.now()
        nxt = next_open(now)
        close = _session(now.astimezone(NY))[1] if is_open(now) else _session(nxt)[1]
        return MarketClock(is_open=is_open(now), next_open=nxt, next_close=close)

    def asset(self, symbol: str) -> Asset | None:
        a = ASSETS.get(symbol)
        if a is None:
            return None
        return Asset(symbol=symbol, name=a[0], asset_class=a[1], exchange=a[2], fractionable=a[3], tradable=True)

    def quote(self, symbol: str) -> Quote | None:
        if symbol not in ASSETS:
            return None
        at = last_close(self._clock.now())
        return Quote(symbol=symbol, price_cents=self.price_at(symbol, at), at=at, source="sim:last_trade")

    def submit(self, symbol: str, side: Side, notional_cents: int | None = None, qty: str | None = None,
               client_order_id: str = "", limit_price_cents: int | None = None,
               extended_hours: bool = False) -> Order:
        if symbol not in ASSETS:
            raise BrokerError(f"asset {symbol} not found")
        if side == "buy" and (notional_cents or 0) > self.account().buying_power_cents:
            raise BrokerError("insufficient buying power")
        if side == "sell" and Decimal(qty or "0") > self._pos.get(symbol, (Decimal(0), 0))[0]:
            raise BrokerError("insufficient qty available for order")
        o = Order(id=f"sim-{len(self.orders) + 1:04d}", client_order_id=client_order_id, symbol=symbol, side=side,
                  notional_cents=notional_cents, qty=qty, status="accepted", submitted_at=self._clock.now(),
                  limit_price_cents=limit_price_cents, source="sim")
        self.orders[o.id] = o
        now = self._clock.now()
        if is_open(now) or (extended_hours and limit_price_cents is not None and is_extended(now)):
            if self._marketable(o, now):
                self._fill(o, now)
        return o.model_copy()

    def _marketable(self, o: Order, at: datetime) -> bool:
        if o.limit_price_cents is None:
            return True
        price = self.price_at(o.symbol, at)
        return price <= o.limit_price_cents if o.side == "buy" else price >= o.limit_price_cents

    def order(self, order_id: str) -> Order:
        if order_id not in self.orders:
            raise BrokerError(f"order {order_id} not found")
        return self.orders[order_id].model_copy()

    def positions(self) -> list[Position]:
        at = last_close(self._clock.now())
        return [Position(symbol=s, qty=str(q.normalize()), avg_entry_cents=round(cost / q),
                         market_value_cents=int((q * self.price_at(s, at)).quantize(Decimal(1), ROUND_DOWN)))
                for s, (q, cost) in sorted(self._pos.items()) if q > 0]

    # ------------------------------------------------------------------ simulation

    def settle(self) -> list[Order]:
        """Fill orders that waited for the market to open, at the opening price."""
        done = []
        for o in self.orders.values():
            if o.status == "accepted":
                at = next_open(o.submitted_at)
                if self._clock.now() >= at:
                    self._fill(o, at)
                    done.append(o.model_copy())
        return done

    def _fill(self, o: Order, at: datetime) -> None:
        price = self.price_at(o.symbol, at)
        if o.limit_price_cents is not None:
            price = min(price, o.limit_price_cents) if o.side == "buy" else max(price, o.limit_price_cents)
        shares, cost = self._pos.get(o.symbol, (Decimal(0), 0))
        if o.side == "buy":
            if o.notional_cents is not None:
                q, paid = (Decimal(o.notional_cents) / price).quantize(SHARE, ROUND_DOWN), o.notional_cents
            else:
                q = Decimal(o.qty or "0")
                paid = int((q * price).quantize(Decimal(1), ROUND_DOWN))
            self.cash_cents -= paid
            self._pos[o.symbol] = (shares + q, cost + paid)
        else:
            q = Decimal(o.qty or "0")
            self.cash_cents += int((q * price).quantize(Decimal(1), ROUND_DOWN))
            left = shares - q
            self._pos[o.symbol] = (left, round(cost * left / shares) if shares else 0)
        o.status, o.filled_qty, o.filled_avg_price_cents, o.filled_at = "filled", str(q.normalize()), price, at
