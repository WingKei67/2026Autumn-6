"""
Week 6 (scenario tournament): the broker's closing cross and new-listing width.

- SESSION_PRECLOSE: no more two-sided quotes; one limit order per symbol that
  takes the position to zero at the cross, priced through the mark but well
  inside the 8 % LULD band; never a market order (the auction rejects them)
- the pull + flatten go through the message budget, and each symbol is done once
- nothing for a flat, halted or liquidated symbol; BROKER_FLATTEN_AT_CLOSE off → pull only
- the next session quotes normally again
- a symbol with too few marks (VLTA just listed) is quoted 1 % wide
"""

from __future__ import annotations

import asyncio

import pytest

import team.broker as tb
from shared.messages import SessionEvent


def _ev(event: str, **data) -> SessionEvent:
    return SessionEvent(event=event, message="", data=data)


def _orders(mm):
    return [o for o in mm.sent if getattr(o, "type", "") == "place_order"]


@pytest.fixture
def mm():
    b = tb._RiskBrokerBot(tb.MyBroker(), "ws://unused")
    b.sent = []

    async def _send(msg):
        b.sent.append(msg)
    b._send = _send
    b._session_open = True
    b.state.exchange_prices.update({"NFLX": 100.0, "AMD": 50.0, "VLTA": 33.0})
    return b


def _resting(mm, symbol):
    """A live two-sided quote the venue has ACKed (the ids are what get cancelled)."""
    mm.state.resting_orders[symbol] = {"buy_id": None, "buy_qty": 10, "sell_id": None,
                                       "sell_qty": 10, "buy_ids": ["b1"], "sell_ids": ["s1"]}
    mm.state.last_quote_prices[symbol] = mm.state.exchange_prices[symbol]


def _preclose(mm):
    asyncio.run(mm._on_session_event(_ev("SESSION_PRECLOSE", ticks=10)))


def test_preclose_flattens_long_and_short_into_the_cross(mm):
    mm.state.positions.update({"NFLX": 120, "AMD": -40})
    _resting(mm, "NFLX")                                  # a live two-sided quote first
    _preclose(mm)
    assert mm.closing
    for sym in ("NFLX", "AMD"):
        asyncio.run(mm.quote_symbol(sym))
    orders = {o.symbol: o for o in _orders(mm)}
    assert set(orders) == {"NFLX", "AMD"}
    sell, buy = orders["NFLX"], orders["AMD"]
    assert (sell.side, sell.quantity, sell.order_type) == ("sell", 120, "limit")
    assert (buy.side, buy.quantity, buy.order_type) == ("buy", 40, "limit")
    # through the mark (so it crosses) but inside the LULD band (so it is accepted)
    assert 100.0 * 0.92 < sell.price < 100.0
    assert 50.0 < buy.price < 50.0 * 1.08
    # the old NFLX quote was cancelled before the flatten order went out
    kinds = [getattr(o, "type", "") for o in mm.sent]
    assert "cancel_order" in kinds and kinds.index("cancel_order") < kinds.index("place_order")


def test_preclose_never_quotes_two_sided_and_acts_once(mm):
    mm.state.positions["NFLX"] = 30
    _preclose(mm)
    for _ in range(5):                                    # the requote loop keeps calling
        asyncio.run(mm.quote_symbol("NFLX"))
    orders = _orders(mm)
    assert len(orders) == 1 and orders[0].side == "sell"
    asyncio.run(mm.quote_symbol("VLTA"))                  # flat: pulled, nothing sent
    assert len(_orders(mm)) == 1 and "VLTA" in mm._closed


def test_preclose_skips_halted_and_liquidated(mm):
    mm.state.positions.update({"NFLX": 50, "AMD": 50})
    asyncio.run(mm._on_session_event(_ev("TRADING_HALT", symbol="NFLX", duration=300)))
    _preclose(mm)
    asyncio.run(mm.quote_symbol("NFLX"))                  # halted: an order would be SYMBOL_HALTED
    assert not _orders(mm)
    mm.liquidated = True
    asyncio.run(mm.quote_symbol("AMD"))
    assert not _orders(mm)


def test_preclose_respects_the_message_budget(mm):
    mm.state.positions["NFLX"] = 50
    _preclose(mm)
    mm._tokens, mm._tokens_t = 0.0, float("inf")          # budget empty, and no refill
    asyncio.run(mm.quote_symbol("NFLX"))
    assert not _orders(mm) and "NFLX" not in mm._closed   # try again next pass
    mm._tokens, mm._tokens_t = tb.MSGS_PER_S, __import__("time").monotonic()
    asyncio.run(mm.quote_symbol("NFLX"))
    assert len(_orders(mm)) == 1


def test_flatten_can_be_switched_off(mm, monkeypatch):
    monkeypatch.setattr(tb, "FLATTEN_AT_CLOSE", False)
    mm.state.positions["NFLX"] = 50
    _resting(mm, "NFLX")
    _preclose(mm)
    asyncio.run(mm.quote_symbol("NFLX"))
    assert not _orders(mm)                                # quotes pulled, no flatten
    assert {o.order_id for o in mm.sent} == {"b1", "s1"}


def test_next_session_quotes_again(mm):
    mm.state.positions["NFLX"] = 10
    _preclose(mm)
    asyncio.run(mm._on_session_event(_ev("SESSION_CLOSED")))
    assert not mm.closing
    asyncio.run(mm._on_session_event(_ev("SESSION_PREOPEN")))
    mm._session_open = True
    mm.sent.clear()
    asyncio.run(mm.quote_symbol("NFLX"))
    sides = {o.side for o in _orders(mm)}
    assert sides == {"buy", "sell"}                       # back to a two-sided ladder


def test_new_listing_is_quoted_wide():
    b = tb.MyBroker()
    assert b.spread("VLTA", 33.0, [33.0] * 5) == pytest.approx(33.0 * tb.NEW_LISTING_SPREAD_FRAC)
    settled = [100.0 + 0.01 * (i % 2) for i in range(tb.VOL_WINDOW + 5)]
    assert b.spread("NFLX", 100.0, settled) < 100.0 * tb.NEW_LISTING_SPREAD_FRAC


def test_flatten_order_fills_at_the_clearing_price_in_the_real_cross(mm):
    """Run our flatten order through the venue's own closing-cross code: it
    executes, and at the single clearing price, not at our (aggressive) limit."""
    from shared.orderbook import OrderBook

    mm.state.positions["NFLX"] = -60                      # short 60 → buy 60 into the cross
    _preclose(mm)
    asyncio.run(mm.quote_symbol("NFLX"))
    (order,) = _orders(mm)
    book = OrderBook("NFLX")
    book.auction_mode = True
    book.place_order("house_mm", "sell", 100.05, 40)
    book.place_order("house_mm", "sell", 100.10, 40)
    book.place_order("other_team", "buy", 99.95, 30)      # someone else's resting bid: no cross
    book.place_order(tb._config.TEAM_ID, order.side, order.price, order.quantity)
    trades = book.auction_execute()
    ours = [t for t in trades if t.buyer_id == tb._config.TEAM_ID]
    assert sum(t.quantity for t in ours) == 60            # flat after the cross
    assert {t.price for t in ours} == {100.10}            # the clearing price, far under our limit
    assert order.price > 100.10


def test_fill_logs_the_counterparty(mm, caplog):
    from shared.messages import TradeExecution
    me = tb._config.TEAM_ID
    with caplog.at_level("INFO", logger="team.broker"):
        mm._on_trade(TradeExecution(trade_id="t1", symbol="TSLA", price=234.83, quantity=10,
                                    buyer_id=me, seller_id="noise_2", aggressor="sell",
                                    fee=9.39, maker_id=me, taker_id="noise_2"))
        mm._on_trade(TradeExecution(trade_id="t2", symbol="TSLA", price=235.14, quantity=10,
                                    buyer_id="momentum_1", seller_id=me, aggressor="buy",
                                    fee=9.41, maker_id=me, taker_id="momentum_1"))
    text = caplog.text
    assert "from noise_2" in text and "to momentum_1" in text and "MAKER" in text
    assert "paid +9.39" not in text                        # the taker's fee is not ours
    assert mm.by_counterparty == {"noise_2": [1, 10, 0], "momentum_1": [1, 0, 10]}
    assert mm.state.positions["TSLA"] == 0                 # the stock bookkeeping still ran
