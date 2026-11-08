"""
Week 5 (breakers, stops, liquidation): the broker's risk plumbing.

- quotes cut to the room left under the inventory cap, the team limit and the equity floor
- nothing quoted while halted; quoting resumes on TRADING_RESUMED
- no flatten at the close, no short under the short-sale rule

The trader's risk rules are tested in tests/test_trader.py.
"""

from __future__ import annotations

import asyncio

import pytest

import team.broker as tb
from shared.messages import SessionEvent


# ── Broker ───────────────────────────────────────────────────────────────

@pytest.fixture
def mm():
    b = tb._RiskBrokerBot(tb.MyBroker(), "ws://unused")
    b.sent = []

    async def _send(msg):
        b.sent.append(msg)
    b._send = _send
    b._session_open = True
    b.state.exchange_prices["NFLX"] = 100.0
    return b


def test_broker_room_includes_cap_team_and_floor(mm):
    mm.state.positions["NFLX"] = 140
    assert mm.room("NFLX") == (10, tb.INVENTORY_CAP + 140)
    team_cap = tb.TEAM_POSITION_LIMIT - tb.TEAM_LIMIT_BUFFER
    mm.team_board, mm.my_board = {"NFLX": team_cap}, {"NFLX": 140}
    assert mm.room("NFLX")[0] == 0                        # team at limit − buffer: no more buying
    mm.team_board = {}
    mm.start_nw, mm.nw = 400_000.0, 250_000.0
    assert mm.room("NFLX") == (0, 140)                    # only the side that reduces


def test_broker_quotes_cut_to_room(mm):
    mm.state.positions["NFLX"] = 140
    asyncio.run(mm.quote_symbol("NFLX"))
    buys = sum(o.quantity for o in mm.sent if o.side == "buy")
    sells = sum(o.quantity for o in mm.sent if o.side == "sell")
    assert buys == 10 and sells > 0
    assert not mm.needs_requote("NFLX", 100.0)             # one-sided on purpose: no churn


def test_broker_halt_cancels_and_stops_quoting(mm):
    asyncio.run(mm.quote_symbol("NFLX"))
    mm.sent.clear()
    asyncio.run(mm._on_session_event(SessionEvent(event="TRADING_HALT", message="",
                                                  data={"symbol": "NFLX", "duration": 300})))
    assert mm.is_halted("NFLX")
    asyncio.run(mm.quote_symbol("NFLX"))
    assert not any(getattr(o, "type", "") == "place_order" for o in mm.sent)
    asyncio.run(mm._on_session_event(SessionEvent(event="TRADING_RESUMED", message="",
                                                  data={"symbol": "NFLX"})))
    asyncio.run(mm.quote_symbol("NFLX"))
    assert any(getattr(o, "side", None) == "buy" for o in mm.sent)


def test_broker_does_not_flatten_at_close(mm):
    mm.state.positions["NFLX"] = 50
    asyncio.run(mm._on_session_event(SessionEvent(event="SESSION_CLOSED", message="", data={})))
    assert not any(getattr(o, "order_type", None) == "market" for o in mm.sent)


def test_broker_never_shorts_under_ssr(mm):
    mm.state.positions["NFLX"] = 10
    asyncio.run(mm._on_session_event(SessionEvent(event="SSR_ON", message="", data={"symbol": "NFLX"})))
    assert mm.room("NFLX")[1] == 10
    asyncio.run(mm._peek('{"type":"error","code":"SSR_RESTRICTED","message":"AMD is under the short-sale rule"}'))
    assert "AMD" in mm.ssr and mm.room("AMD")[1] == 0
