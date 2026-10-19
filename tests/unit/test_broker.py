import json
from datetime import datetime, timedelta

import httpx
import pytest

from kernel.broker import Account, Order, OrderLimits, OrderRequest, Position, check_order
from kernel.brokers_alpaca import AlpacaBroker, load_keys
from kernel.clock import MSK, SimClock
from sandbox.broker.sim import SimBroker

MONDAY_OPEN = datetime(2026, 10, 19, 17, 0, tzinfo=MSK)    # 10:00 in New York
SATURDAY = datetime(2026, 10, 17, 10, 0, tzinfo=MSK)
LIMITS = OrderLimits(allowlist=["SPY", "VTI", "BND"], max_notional_cents=50_000)
RICH = Account(cash_cents=1_000_000, buying_power_cents=1_000_000)


def test_sim_fills_while_open_and_queues_after_hours():
    clock = SimClock(MONDAY_OPEN)
    b = SimBroker(clock, cash_cents=100_000)
    assert b.clock().is_open
    o = b.submit("SPY", "buy", notional_cents=10_000, client_order_id="g1")
    q = b.quote("SPY")
    assert o.filled and o.filled_avg_price_cents == q.price_cents and b.cash_cents == 90_000
    assert b.positions()[0].symbol == "SPY" and b.positions()[0].avg_entry_cents == q.price_cents

    clock.set(SATURDAY)
    b2 = SimBroker(clock, cash_cents=100_000)
    assert not b2.clock().is_open and b2.clock().next_open.weekday() == 0
    w = b2.submit("VTI", "buy", notional_cents=20_000)
    assert w.status == "accepted" and b2.account().buying_power_cents == 80_000 and b2.positions() == []
    assert b2.settle() == []
    clock.set(MONDAY_OPEN)
    assert [x.id for x in b2.settle()] == [w.id] and b2.order(w.id).filled and b2.cash_cents == 80_000
    s = b2.submit("VTI", "sell", qty=b2.positions()[0].qty)
    assert s.filled and b2.positions() == []


def _check(req, clock_at=MONDAY_OPEN, account=RICH, envelope=100_000, positions=(), recent=(), sim=None,
           quote_at=None):
    clock = SimClock(clock_at)
    sim = sim or SimBroker(clock, cash_cents=account.cash_cents)
    quote = sim.quote(req.symbol)
    if quote is not None and quote_at is not None:
        quote = quote.model_copy(update={"at": quote_at})
    return check_order(req, LIMITS, asset=sim.asset(req.symbol), quote=quote, account=account,
                       envelope_cash_cents=envelope, positions=list(positions), recent=list(recent),
                       now=clock.now(), market_open=sim.clock().is_open)


def buy(symbol="SPY", cents=10_000, **kw):
    return OrderRequest(symbol=symbol, side="buy", notional_cents=cents, **kw)


def test_a_plain_buy_inside_the_mandate_passes():
    assert _check(buy()) == []
    assert _check(buy(), clock_at=SATURDAY) == []   # after hours the last close is the market price


@pytest.mark.parametrize("req, reason", [
    (buy("GME"), "not on the client's list"),
    (buy("TLSS"), "over the counter"),
    (buy("SPY261218C00600000"), "no options, crypto"),
    (buy("BTC/USD"), "no options, crypto"),
    (buy(cents=60_000), "amount outside the allowed range"),
    (OrderRequest(symbol="SPY", side="buy", qty="1"), "a buy must be a dollar amount"),
    (OrderRequest(symbol="SPY", side="sell", qty="1"), "no short selling"),
    (buy(cited_price_cents=10_000), "away from the market price"),
])
def test_orders_outside_the_mandate_are_rejected(req, reason):
    assert any(reason in r for r in _check(req)), _check(req)


def test_money_freshness_and_frequency_limits():
    assert any("investment envelope" in r for r in _check(buy(cents=40_000), envelope=30_000))
    margin = Account(cash_cents=5_000, buying_power_cents=200_000)
    assert any("no margin" in r for r in _check(buy(cents=10_000), account=margin))
    stale = MONDAY_OPEN - timedelta(minutes=30)
    assert any("older than 15 minutes" in r for r in _check(buy(), quote_at=stale))
    assert any("after the decision moment" in r for r in _check(buy(), quote_at=MONDAY_OPEN + timedelta(minutes=1)))
    prior = Order(id="o1", symbol="SPY", side="buy", status="filled", submitted_at=MONDAY_OPEN - timedelta(hours=2))
    assert any("in the last 24 hours" in r for r in _check(buy(), recent=[prior]))
    old = prior.model_copy(update={"submitted_at": MONDAY_OPEN - timedelta(days=2)})
    assert _check(buy(), recent=[old]) == []
    held = [Position(symbol="SPY", qty="0.5", market_value_cents=29_000, avg_entry_cents=58_000)]
    assert _check(OrderRequest(symbol="SPY", side="sell", qty="0.5"), positions=held) == []
    assert any("no short selling" in r for r in _check(OrderRequest(symbol="SPY", side="sell", qty="0.6"),
                                                       positions=held))
    cited = _check(buy(cited_price_cents=SimBroker(SimClock(MONDAY_OPEN)).quote("SPY").price_cents + 100))
    assert cited == []


# ------------------------------------------------------------------ Alpaca over a mocked API

ORDER = {"id": "61e69015-8549-4bfd-b9c3-01e75843f47d", "client_order_id": "g-1", "symbol": "SPY", "side": "buy",
         "notional": "25", "qty": None, "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
         "submitted_at": "2026-10-19T20:05:01.123456789Z", "filled_at": None}


def _api(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    assert request.headers["APCA-API-KEY-ID"] == "PKTEST" and request.headers["APCA-API-SECRET-KEY"] == "s3cret"
    if p == "/v2/account":
        return httpx.Response(200, json={"cash": "100000.5", "buying_power": "200001", "status": "ACTIVE",
                                         "account_number": "PA123"})
    if p == "/v2/clock":
        return httpx.Response(200, json={"is_open": False, "next_open": "2026-10-20T09:30:00-04:00",
                                         "next_close": "2026-10-20T16:00:00-04:00"})
    if p == "/v2/stocks/SPY/trades/latest":
        assert request.url.params["feed"] == "iex"
        return httpx.Response(200, json={"symbol": "SPY", "trade": {"t": "2026-10-19T19:59:59.98Z", "p": 581.23}})
    if p == "/v2/assets/SPY":
        return httpx.Response(200, json={"symbol": "SPY", "name": "SPDR", "class": "us_equity", "exchange": "ARCA",
                                         "tradable": True, "fractionable": True})
    if p == "/v2/assets/NOPE":
        return httpx.Response(404, json={"message": "asset not found"})
    if p == "/v2/orders" and request.method == "POST":
        body = json.loads(request.content)
        assert body == {"symbol": "SPY", "side": "buy", "type": "market", "time_in_force": "day",
                        "notional": "25.00", "client_order_id": "g-1"}
        return httpx.Response(200, json=ORDER)
    if p == f"/v2/orders/{ORDER['id']}":
        return httpx.Response(200, json={**ORDER, "status": "filled", "filled_qty": "0.043012345",
                                         "filled_avg_price": "581.24", "filled_at": "2026-10-20T13:30:01Z"})
    if p == "/v2/positions":
        return httpx.Response(200, json=[{"symbol": "SPY", "qty": "0.043012345", "market_value": "25.01",
                                          "avg_entry_price": "581.24"}])
    if p == "/v2/orders/bad":
        return httpx.Response(403, json={"message": "insufficient buying power"})
    return httpx.Response(500)


def test_alpaca_maps_the_rest_api_and_never_logs_keys():
    b = AlpacaBroker("PKTEST", "s3cret", transport=httpx.MockTransport(_api))
    a = b.account()
    assert a.cash_cents == 10_000_050 and a.buying_power_cents == 20_000_100
    c = b.clock()
    assert not c.is_open and c.next_open.hour == 9
    q = b.quote("SPY")
    assert q.price_cents == 58_123 and q.at.year == 2026 and q.source == "alpaca:iex:latest_trade"
    assert b.asset("SPY").fractionable and b.asset("NOPE") is None
    o = b.submit("SPY", "buy", notional_cents=2_500, client_order_id="g-1")
    assert o.status == "accepted" and o.notional_cents == 2_500 and o.submitted_at.microsecond == 123456
    f = b.order(o.id)
    assert f.filled and f.filled_avg_price_cents == 58_124 and f.filled_qty == "0.043012345"
    assert b.positions()[0].market_value_cents == 2_501
    with pytest.raises(Exception, match="insufficient buying power"):
        b.order("bad")
    log = json.dumps(b.log, default=str)
    assert "s3cret" not in log and "PKTEST" not in log and "PA123" not in log and len(b.log) == 9


def test_alpaca_unreachable_and_keys(tmp_path, monkeypatch):
    def down(request):
        raise httpx.ConnectTimeout("timed out")
    b = AlpacaBroker("k", "s", transport=httpx.MockTransport(down))
    with pytest.raises(Exception, match="VPN"):
        b.account()
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert load_keys(tmp_path / "missing") is None
    (tmp_path / "keys").write_text("KID SEC\n")
    assert load_keys(tmp_path / "keys") == ("KID", "SEC")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "E1")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "E2")
    assert load_keys(tmp_path / "keys") == ("E1", "E2")


def test_extended_hours_limit_order_fills_in_the_sim_and_maps_to_alpaca_qty():
    from kernel.brokers_alpaca import shares_for
    from sandbox.broker.sim import SimBroker
    clock = SimClock(datetime(2026, 10, 20, 11, 30, tzinfo=MSK))   # 4:30 New York, pre-market
    b = SimBroker(clock, cash_cents=100_000)
    price = b.price_at("SPY", clock.now())
    o = b.submit("SPY", "buy", qty="0.1", limit_price_cents=price + 100, extended_hours=True)
    assert o.status == "filled" and o.filled_avg_price_cents == price
    late = b.submit("SPY", "buy", qty="0.1", limit_price_cents=price + 100)
    assert late.status == "accepted"
    assert shares_for(1_000, 58_000) == "0.017241379"


def test_invest_desk_picks_the_order_type_by_session_and_reads_the_order_back():
    from types import SimpleNamespace

    from kernel.intent import Intent
    from kernel.investing import InvestDesk
    from kernel.provenance import Label, Labeled
    from kernel.mandate import Action
    clock = SimClock(datetime(2026, 10, 20, 11, 30, tzinfo=MSK))   # pre-market
    desk = InvestDesk(SimBroker(clock, cash_cents=100_000), clock.now, envelope_cents=5_000)
    grant = SimpleNamespace(payee="SPY", amount=1_000, id="gr_1")
    intent = Intent(client_id="c", task_id="t", proposed_by="p", action=Action.INVEST,
                    payee=Labeled(value="SPY", label=Label.CLIENT, source="client"),
                    amount=Labeled(value=1_000, label=Label.CLIENT, source="client"))
    r = desk.execute(grant, intent)
    assert r.details["session"] == "extended" and r.details["type"] == "limit" and desk.verify(intent, r)
    assert desk.orders[0].status == "filled" and desk.remaining() == 4_000
    clock.set(datetime(2026, 10, 21, 18, 0, tzinfo=MSK))           # 11:00 New York, regular hours
    r2 = desk.execute(SimpleNamespace(payee="SPY", amount=1_000, id="gr_2"), intent)
    assert r2.details["type"] == "market" and desk.verify(intent, r2)
    forged = r2.model_copy(update={"details": {**r2.details, "client_order_id": "gr_other"}})
    assert not desk.verify(intent, forged), "an order the grant did not place is not confirmed"


def test_invest_desk_falls_back_to_a_queued_order_when_the_limit_order_is_refused():
    from types import SimpleNamespace

    from kernel.investing import InvestDesk

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.content)
            if body["type"] == "limit":
                return httpx.Response(422, json={"message": "fractional orders must be DAY market orders"})
            return httpx.Response(200, json={**ORDER, "client_order_id": body["client_order_id"]})
        return _api(request)
    b = AlpacaBroker("PKTEST", "s3cret", transport=httpx.MockTransport(api))
    desk = InvestDesk(b, lambda: datetime(2026, 10, 20, 11, 30, tzinfo=MSK), envelope_cents=5_000, source="alpaca")
    r = desk.execute(SimpleNamespace(payee="SPY", amount=2_500, id="g-1"), None)
    assert r.ok and r.details["type"] == "market" and "refused" in r.details["note"]
