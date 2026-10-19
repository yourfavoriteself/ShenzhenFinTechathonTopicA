"""The investment envelope: how the kernel places, and then confirms, orders on a US exchange.

An agent proposes "invest $10 in SPY" as an ordinary intent (action invest, payee = ticker, amount
in cents). The pipeline runs it through the usual steps plus the "broker" step below, which checks
the order against the client's investment mandate with the kernel's own quote. Execution picks the
order type from the market session: a market order for a dollar amount in regular hours, a limit
order in extended hours (Alpaca takes only limit orders then), and a queued market order while the
market is closed. The result is confirmed by reading the order back from the broker.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from .broker import (DEAD, Broker, BrokerError, Order, OrderLimits, OrderRequest, check_order,
                     market_session)
from .cards import Risk
from .intent import Intent
from .mandate import Grant
from .money import usd
from .ports import PayeeInfo, Receipt

if TYPE_CHECKING:
    from .pipeline import Ctx, StepResult

ORDER_REF = "broker:order:"
LIMIT_SLACK = 0.005      # extended-hours buy limit: half a percent above the kernel's quote
ALLOWLIST = ["SPY", "VTI", "QQQ", "BND"]


class InvestDesk:
    def __init__(self, broker: Broker, now: Callable[[], datetime], *, envelope_cents: int,
                 limits: OrderLimits | None = None, source: str = "sim"):
        self.broker, self.now, self.source = broker, now, source
        self.envelope_cents = envelope_cents
        self.limits = limits or OrderLimits(allowlist=list(ALLOWLIST), max_notional_cents=envelope_cents // 2)
        self.orders: list[Order] = []
        self.label = f"investment envelope ({'Alpaca paper account' if source == 'alpaca' else 'broker sandbox'})"

    # ------------------------------------------------------------------ reading

    def payee(self, client_id: str, symbol: str) -> PayeeInfo | None:
        try:
            a = self.broker.asset(symbol)
        except BrokerError:
            return None
        if a is None:
            return None
        return PayeeInfo(token=symbol, name=f"{a.name} ({symbol})", kind="security",
                         verified=symbol in self.limits.allowlist,
                         known_to_client=any(o.symbol == symbol and o.status not in DEAD for o in self.orders))

    def spent(self) -> int:
        return sum(o.notional_cents or _limit_value(o) for o in self.orders if o.status not in DEAD)

    def remaining(self) -> int:
        return max(0, self.envelope_cents - self.spent())

    # ------------------------------------------------------------------ the pipeline step

    def check(self, ctx: "Ctx") -> "StepResult":
        from .pipeline import StepResult, Verdict
        i = ctx.intent
        req = OrderRequest(symbol=str(i.val("payee")), side="buy", notional_cents=i.val("amount"),
                           cited_price_cents=_cited(i))
        try:
            reasons = check_order(req, self.limits, asset=self.broker.asset(req.symbol),
                                  quote=self.broker.quote(req.symbol), account=self.broker.account(),
                                  envelope_cash_cents=self.remaining(), positions=self.broker.positions(),
                                  recent=self.orders, now=self.now(), market_open=self.broker.clock().is_open)
        except BrokerError as e:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH, reasons=[f"broker: {e}"])
        data = {"broker": self.source, "session": market_session(self.now())}
        if reasons:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH, reasons=reasons, data=data)
        return StepResult(step="", data=data)

    # ------------------------------------------------------------------ executor and verifier

    def execute(self, grant: Grant, intent: Intent) -> Receipt:
        symbol, amount = grant.payee, grant.amount
        session, note = market_session(self.now()), ""
        try:
            o = None
            if session == "extended":
                q = self.broker.quote(symbol)
                if q is None:
                    return Receipt(ok=False, details={"error": f"no quote for {symbol}"})
                limit = int(math.ceil(q.price_cents * (1 + LIMIT_SLACK)))
                try:
                    o = self.broker.submit(symbol, "buy", notional_cents=amount, client_order_id=grant.id,
                                           limit_price_cents=limit, extended_hours=True)
                except BrokerError as e:   # some brokers take fractional orders in regular hours only
                    session, note = "closed", f"extended-hours limit order refused ({e})"
            if o is None:
                o = self.broker.submit(symbol, "buy", notional_cents=amount, client_order_id=grant.id)
        except BrokerError as e:
            return Receipt(ok=False, details={"error": str(e)})
        self.orders.append(o)
        ok = o.status not in DEAD
        return Receipt(ok=ok, reference=ORDER_REF + o.id, details={
            "order": o.id, "client_order_id": grant.id, "status": o.status, "session": session,
            "type": "limit" if o.limit_price_cents else "market", "source": self.source, "note": note,
            **({"limit_price": usd(o.limit_price_cents), "qty": o.qty} if o.limit_price_cents else {})})

    def verify(self, intent: Intent, receipt: Receipt) -> bool:
        """Read the order back from the broker: same grant, security, side and money, and still alive."""
        try:
            o = self.broker.order(receipt.reference.removeprefix(ORDER_REF))
        except BrokerError:
            return False
        amount = intent.val("amount")
        money_ok = o.notional_cents == amount if o.notional_cents is not None else 0 < _limit_value(o) <= amount
        return (o.client_order_id == receipt.details.get("client_order_id") and o.symbol == intent.val("payee")
                and o.side == "buy" and money_ok and o.status not in DEAD)

    def refresh(self) -> list[Order]:
        """Re-read every order from the broker (fills happen on the exchange's time, not ours)."""
        out = []
        for n, o in enumerate(self.orders):
            try:
                self.orders[n] = self.broker.order(o.id)
            except BrokerError:
                continue
            out.append(self.orders[n])
        return out

    def summary(self) -> dict[str, Any]:
        acct = self.broker.account()
        return {"source": self.source, "label": self.label, "envelope": self.envelope_cents,
                "spent": self.spent(), "remaining": self.remaining(), "allowlist": self.limits.allowlist,
                "max_order": self.limits.max_notional_cents, "broker_cash": acct.cash_cents,
                "session": market_session(self.now()), "orders": [o.model_dump(mode="json") for o in self.orders],
                "positions": [p.model_dump(mode="json") for p in self.broker.positions()]}


def _limit_value(o: Order) -> int:
    if o.limit_price_cents is None or not o.qty:
        return 0
    return int(math.ceil(float(o.qty) * o.limit_price_cents))


def _cited(i: Intent) -> int | None:
    """A price the agent cites in the memo ("at $767.20"): the kernel compares it with its own quote."""
    m = re.search(r"\bat \$([\d,]+(?:\.\d{1,2})?)", i.memo)
    return int(round(float(m.group(1).replace(",", "")) * 100)) if m else None
