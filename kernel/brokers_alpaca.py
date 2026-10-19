"""Alpaca paper trading over its REST API: the kernel's executor for orders on the US market.

Keys come from the environment (ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY) or ~/.alpaca_keys
("KEY_ID SECRET" on one line). They travel only in request headers: never in the call log,
files or the journal.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

import httpx

from .broker import Account, Asset, BrokerError, MarketClock, Order, Position, Quote, Side, to_cents

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
KEYS_FILE = Path.home() / ".alpaca_keys"
REDACT = {"account_number", "id_account"}


def load_keys(path: Path = KEYS_FILE) -> tuple[str, str] | None:
    key, secret = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    if key and secret:
        return key, secret
    try:
        parts = path.read_text().split()
    except OSError:
        return None
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))   # nanoseconds to microseconds
    return datetime.fromisoformat(value)


def _redact(body: Any) -> Any:
    if isinstance(body, dict):
        return {k: ("***" if k in REDACT else _redact(v)) for k, v in body.items()}
    if isinstance(body, list):
        return [_redact(x) for x in body]
    return body


class AlpacaBroker:
    def __init__(self, key_id: str, secret: str, base: str = PAPER, data: str = DATA, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self._api = httpx.Client(base_url=base, headers=headers, timeout=timeout, transport=transport)
        self._data = httpx.Client(base_url=data, headers=headers, timeout=timeout, transport=transport)
        self.log: list[dict[str, Any]] = []   # evidence: method, path, status, request and response bodies

    @classmethod
    def from_keys(cls, path: Path = KEYS_FILE, **kw: Any) -> "AlpacaBroker":
        keys = load_keys(path)
        if keys is None:
            raise BrokerError(f"no Alpaca keys: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY or write {path}")
        return cls(*keys, **kw)

    # ------------------------------------------------------------------ transport

    def _call(self, client: httpx.Client, method: str, path: str, *, params: dict | None = None,
              json: dict | None = None, allow_404: bool = False) -> Any:
        try:
            r = client.request(method, path, params=params, json=json)
        except httpx.HTTPError as e:
            self.log.append({"method": method, "path": path, "status": None, "error": type(e).__name__})
            raise BrokerError(f"Alpaca is unreachable ({type(e).__name__}); check the network or a VPN") from e
        body = r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text
        self.log.append({"method": method, "path": path, "status": r.status_code, "request": json,
                         "response": _redact(body)})
        if r.status_code == 404 and allow_404:
            return None
        if r.status_code >= 400:
            msg = body.get("message") if isinstance(body, dict) else str(body)[:200]
            raise BrokerError(f"Alpaca {method} {path}: {r.status_code} {msg}")
        return body

    # ------------------------------------------------------------------ Broker

    def account(self) -> Account:
        a = self._call(self._api, "GET", "/v2/account")
        return Account(cash_cents=to_cents(a["cash"]), buying_power_cents=to_cents(a["buying_power"]),
                       status=a.get("status", ""))

    def clock(self) -> MarketClock:
        c = self._call(self._api, "GET", "/v2/clock")
        return MarketClock(is_open=c["is_open"], next_open=_ts(c["next_open"]), next_close=_ts(c["next_close"]))

    def asset(self, symbol: str) -> Asset | None:
        a = self._call(self._api, "GET", f"/v2/assets/{symbol}", allow_404=True)
        if a is None:
            return None
        return Asset(symbol=a["symbol"], name=a.get("name", ""), asset_class=a.get("class", ""),
                     tradable=a.get("tradable", False), fractionable=a.get("fractionable", False),
                     exchange=a.get("exchange", ""))

    def quote(self, symbol: str) -> Quote | None:
        q = self._call(self._data, "GET", f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"},
                       allow_404=True)
        t = (q or {}).get("trade")
        if not t:
            return None
        return Quote(symbol=symbol, price_cents=to_cents(t["p"]), at=_ts(t["t"]), source="alpaca:iex:latest_trade")

    def submit(self, symbol: str, side: Side, notional_cents: int | None = None, qty: str | None = None,
               client_order_id: str = "", limit_price_cents: int | None = None,
               extended_hours: bool = False) -> Order:
        """A market order by dollar amount, or a limit order by shares (the only kind allowed outside
        regular hours: pre-market 4:00 and after-hours until 20:00 New York time)."""
        body: dict[str, Any] = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day"}
        if limit_price_cents is not None:
            if qty is None and notional_cents is not None:
                qty = shares_for(notional_cents, limit_price_cents)
                notional_cents = None
            body.update(type="limit", limit_price=_dollars(limit_price_cents), extended_hours=extended_hours)
        if notional_cents is not None:
            body["notional"] = _dollars(notional_cents)
        if qty is not None:
            body["qty"] = qty
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._order(self._call(self._api, "POST", "/v2/orders", json=body))

    def order(self, order_id: str) -> Order:
        return self._order(self._call(self._api, "GET", f"/v2/orders/{order_id}"))

    def positions(self) -> list[Position]:
        return [Position(symbol=p["symbol"], qty=p["qty"], market_value_cents=to_cents(p["market_value"]) or 0,
                         avg_entry_cents=to_cents(p["avg_entry_price"]) or 0)
                for p in self._call(self._api, "GET", "/v2/positions")]

    @staticmethod
    def _order(o: dict[str, Any]) -> Order:
        return Order(id=o["id"], client_order_id=o.get("client_order_id") or "", symbol=o["symbol"], side=o["side"],
                     notional_cents=to_cents(o.get("notional")), qty=o.get("qty"),
                     limit_price_cents=to_cents(o.get("limit_price")), status=o["status"],
                     filled_qty=o.get("filled_qty") or "0", filled_avg_price_cents=to_cents(o.get("filled_avg_price")),
                     submitted_at=_ts(o.get("submitted_at")), filled_at=_ts(o.get("filled_at")), source="alpaca:paper")


def _dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def shares_for(notional_cents: int, price_cents: int) -> str:
    """Fractional shares that fit in the amount at the price, rounded down to 9 decimals."""
    q = (Decimal(notional_cents) / Decimal(price_cents)).quantize(Decimal("0.000000001"), ROUND_DOWN)
    return str(q.normalize())
