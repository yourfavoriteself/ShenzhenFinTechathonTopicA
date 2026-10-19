"""Investing on a US exchange, inside the kernel: the broker port and the deterministic order checks.

Agents may only propose an order. The kernel fetches its own quote and asset record from the broker
and checks the order against the client's investment mandate: an allowlist of securities, buys in
dollars only, no shorts, options, crypto or margin, a size range, the investment envelope's cash,
a fresh quote and a price corridor, and a daily order limit per security. Money is integer cents.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

Side = Literal["buy", "sell"]
EQUITY = "us_equity"
OTC = {"OTC", "OTCMKTS"}
PLAIN_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")   # options carry digits, crypto pairs a slash
NY = ZoneInfo("America/New_York")
REGULAR, EXTENDED = (time(9, 30), time(16, 0)), (time(4, 0), time(20, 0))
DEAD = ("canceled", "rejected", "expired")


def market_session(at: datetime) -> Literal["regular", "extended", "closed"]:
    """US equities by New York wall time on weekdays (exchange holidays are the broker's to report)."""
    ny = at.astimezone(NY)
    if ny.weekday() >= 5:
        return "closed"
    if REGULAR[0] <= ny.time() < REGULAR[1]:
        return "regular"
    return "extended" if EXTENDED[0] <= ny.time() < EXTENDED[1] else "closed"


class Quote(BaseModel):
    symbol: str
    price_cents: int
    at: datetime
    source: str


class Asset(BaseModel):
    symbol: str
    name: str = ""
    asset_class: str = EQUITY
    tradable: bool = True
    fractionable: bool = True
    exchange: str = ""


class Order(BaseModel):
    id: str
    client_order_id: str = ""
    symbol: str
    side: Side
    notional_cents: int | None = None
    qty: str | None = None
    limit_price_cents: int | None = None
    status: str
    filled_qty: str = "0"
    filled_avg_price_cents: int | None = None
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    source: str = ""

    @property
    def filled(self) -> bool:
        return self.status == "filled"


class Position(BaseModel):
    symbol: str
    qty: str
    market_value_cents: int
    avg_entry_cents: int


class Account(BaseModel):
    cash_cents: int
    buying_power_cents: int
    status: str = "ACTIVE"


class MarketClock(BaseModel):
    is_open: bool
    next_open: datetime
    next_close: datetime


class Broker(Protocol):
    def account(self) -> Account: ...
    def clock(self) -> MarketClock: ...
    def asset(self, symbol: str) -> Asset | None: ...
    def quote(self, symbol: str) -> Quote | None: ...
    def submit(self, symbol: str, side: Side, notional_cents: int | None = None, qty: str | None = None,
               client_order_id: str = "", limit_price_cents: int | None = None,
               extended_hours: bool = False) -> Order: ...
    def order(self, order_id: str) -> Order: ...
    def positions(self) -> list[Position]: ...


class BrokerError(Exception):
    pass


# ------------------------------------------------------------------ the kernel's checks

class OrderLimits(BaseModel):
    """What the client's investment mandate allows."""
    allowlist: list[str]
    min_notional_cents: int = 100
    max_notional_cents: int
    max_quote_age: timedelta = timedelta(minutes=15)
    corridor_pct: float = 2.0
    max_orders_per_symbol_per_day: int = 1


class OrderRequest(BaseModel):
    """An agent's proposal: buys are in dollars, sells in shares."""
    symbol: str
    side: Side
    notional_cents: int | None = None
    qty: str | None = None
    cited_price_cents: int | None = None


def to_cents(value: float | str | Decimal | None) -> int | None:
    return None if value in (None, "") else int((Decimal(str(value)) * 100).quantize(Decimal(1)))


def held_qty(positions: list[Position], symbol: str) -> Decimal:
    return sum((Decimal(p.qty) for p in positions if p.symbol == symbol), Decimal(0))


def check_order(req: OrderRequest, limits: OrderLimits, *, asset: Asset | None, quote: Quote | None,
                account: Account, envelope_cash_cents: int, positions: list[Position], recent: list[Order],
                now: datetime, market_open: bool) -> list[str]:
    """Every reason the order is outside the mandate; an empty list means the kernel may place it."""
    return (_security(req, limits, asset) + _shape(req, limits, positions)
            + _money(req, account, envelope_cash_cents) + _price(req, limits, quote, now, market_open)
            + _frequency(req, limits, recent, now))


def _security(req: OrderRequest, limits: OrderLimits, asset: Asset | None) -> list[str]:
    if not PLAIN_TICKER.match(req.symbol):
        return [f"{req.symbol} is not a plain stock or fund ticker (no options, crypto or other derivatives)"]
    out = [] if req.symbol in limits.allowlist else [f"{req.symbol} is not on the client's list of securities"]
    if asset is None:
        return out + [f"the broker does not know {req.symbol}"]
    if asset.asset_class != EQUITY:
        out.append(f"{req.symbol} is {asset.asset_class}, only US stocks and funds are allowed")
    if not asset.tradable:
        out.append(f"{req.symbol} is not tradable")
    if asset.exchange.upper() in OTC:
        out.append(f"{req.symbol} trades over the counter")
    if req.side == "buy" and not asset.fractionable:
        out.append(f"{req.symbol} cannot be bought for a dollar amount")
    return out


def _shape(req: OrderRequest, limits: OrderLimits, positions: list[Position]) -> list[str]:
    if req.side == "buy":
        if req.notional_cents is None or req.qty is not None:
            return ["a buy must be a dollar amount"]
        if not limits.min_notional_cents <= req.notional_cents <= limits.max_notional_cents:
            return ["amount outside the allowed range"]
        return []
    if req.qty is None or req.notional_cents is not None:
        return ["a sell must be a number of shares"]
    qty = Decimal(req.qty)
    if qty <= 0:
        return ["the number of shares must be positive"]
    if qty > held_qty(positions, req.symbol):
        return [f"selling more {req.symbol} than the client holds (no short selling)"]
    return []


def _money(req: OrderRequest, account: Account, envelope_cash_cents: int) -> list[str]:
    if req.side != "buy" or req.notional_cents is None:
        return []
    out = []
    if req.notional_cents > envelope_cash_cents:
        out.append("not enough money in the investment envelope")
    if req.notional_cents > min(account.cash_cents, account.buying_power_cents):
        out.append("not enough cash at the broker (cash account, no margin)")
    if account.status.upper() != "ACTIVE":
        out.append(f"the broker account is {account.status.lower()}")
    return out


def _price(req: OrderRequest, limits: OrderLimits, quote: Quote | None, now: datetime, open_: bool) -> list[str]:
    if quote is None:
        return [f"no quote for {req.symbol}"]
    out = []
    if open_ and now - quote.at > limits.max_quote_age:
        minutes = int(limits.max_quote_age.total_seconds() // 60)
        out.append(f"the quote for {req.symbol} is older than {minutes} minutes")
    if quote.at > now:
        out.append("the quote is from after the decision moment")
    if req.cited_price_cents is not None:
        gap = abs(req.cited_price_cents - quote.price_cents) * 100 / quote.price_cents
        if gap > limits.corridor_pct:
            out.append(f"the cited price is {gap:.1f}% away from the market price")
    return out


def _frequency(req: OrderRequest, limits: OrderLimits, recent: list[Order], now: datetime) -> list[str]:
    since = now - timedelta(days=1)
    n = sum(1 for o in recent if o.symbol == req.symbol and o.submitted_at and o.submitted_at > since
            and o.status not in DEAD)
    if n < limits.max_orders_per_symbol_per_day:
        return []
    return [f"already {n} order(s) for {req.symbol} in the last 24 hours"]
