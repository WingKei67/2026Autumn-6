"""Trader 1 production regression tests (week-aware activation, futures guard, Event2 integration).

Covers defects found in the research continuation: the week-5-only gate (deployed bot silent in
weeks 6-10) and ARENA10 (cash-settled future) being traded by a share-only ledger.
"""
import asyncio
import time
import pytest
from arena import Signal
from team.trader import MyTrader, _Bot, GRADED_WEEKS, NON_SHARE_SYMBOLS
from team.event2 import Event2Config
from shared.messages import BookSnapshot


def book(sym, px, depth=100):
    return BookSnapshot(symbol=sym, bids=[[px - .05, depth]], asks=[[px + .05, depth]],
                        mid_price=px, spread=.1, ref_price=px)


def make(strategy='baseline', **cfg):
    t = MyTrader(strategy=strategy, event2_config=Event2Config(**cfg) if cfg else None)
    b = _Bot(t)
    b._has_server_snapshot = True; b._snapshot_at = time.monotonic(); b._session_open = True
    b.portfolio.cash = 175_000.0; b.portfolio._net_worth = 175_000.0
    return t, b


@pytest.mark.parametrize('week,active', [(0, False), (4, False), (5, True), (6, True), (7, True),
                                         (8, True), (9, True), (10, True), (11, False)])
def test_week_aware_activation(week, active):
    t = MyTrader()
    t.on_event('WEEK_CHANGED', '', {'week': week})
    assert t._week5_active is active
    assert (week in GRADED_WEEKS) is active


def test_session_open_sets_week_and_short_gate():
    t = MyTrader()
    t.on_session_open({'week': 6, 'scenario': {'week': 6, 'flags': {'shorts_allowed': True}}})
    assert t._week5_active and t._shorts_allowed
    t.on_reconnect()                       # a reconnect can have missed SSR: shorts fail closed
    assert not t._shorts_allowed
    t.on_session_open({'week': 6, 'late_join': True, 'scenario': {'week': 6, 'flags': {'shorts_allowed': True}}})
    assert t._week5_active and not t._shorts_allowed
    t.on_session_open({'week': 4, 'scenario': {'week': 4, 'flags': {'shorts_allowed': False}}})
    assert not t._week5_active and not t._shorts_allowed


def test_ipo_still_allowed_after_week5_but_not_in_week5():
    t = MyTrader(); t._portfolio = type('P', (), {'cash': 175000.0, 'positions': {}})()
    t.on_event('WEEK_CHANGED', '', {'week': 6})
    assert t.on_ipo('VLTA', 30., 34., 3500, {}) is not None      # unchanged deployed behaviour in IPO weeks
    t.on_event('WEEK_CHANGED', '', {'week': 5})
    assert t.on_ipo('VLTA', 30., 34., 3500, {}) is None


def test_futures_never_planned_reacted_or_ordered():
    t, b = make()
    t.on_event('WEEK_CHANGED', '', {'week': 9})
    b.market.update_book(book('ARENA10', 300)); b.market.update_book(book('AAPL', 200))
    t._refs.update({'ARENA10': 300., 'AAPL': 200.})
    t.on_event('CALENDAR_EVENT', '', {'pct': .05, 'ramp_ticks': 15, 'overshoot': 1.2,
                                      'affected': ['ARENA10', 'AAPL'], 'tick': 100})
    assert all('ARENA10' not in s for _, s in t._unanswered)
    assert 'ARENA10' not in t.plans
    sent = []
    async def fake(msg): sent.append(msg)
    b._send = fake
    asyncio.run(b._place_order(Signal(symbol='ARENA10', side='buy', quantity=1, price=300.)))
    assert sent == [] and 'ARENA10' in NON_SHARE_SYMBOLS


def test_default_strategy_is_baseline_and_unknown_rejected(monkeypatch):
    monkeypatch.delenv('TRADER_STRATEGY', raising=False)
    assert MyTrader().event2 is None
    monkeypatch.setenv('TRADER_STRATEGY', 'event2')
    assert MyTrader().event2 is not None
    with pytest.raises(ValueError):
        MyTrader(strategy='nonsense')


def _short_ready(t):
    t.on_event('WEEK_CHANGED', '', {'week': 6, 'scenario': {'flags': {'shorts_allowed': True}}})
    t.on_session_open({'week': 6, 'scenario': {'week': 6, 'flags': {'shorts_allowed': True}}})


def test_event2_shorts_negative_event_within_seat_cap():
    t, b = make('event2'); _short_ready(t)
    b.market.update_book(book('MSFT', 400, depth=500)); t._refs['MSFT'] = 400.
    t.now = lambda: 0.0
    t.event2.note_quotes(b.market)
    t.on_event('CALENDAR_EVENT', '', {'pct': -.06, 'ramp_ticks': 15, 'overshoot': 1.2, 'affected': ['MSFT'], 'tick': 1})
    sig = t.on_tick(b.market, b.portfolio)
    assert sig is not None and sig.side == 'sell' and sig.symbol == 'MSFT'
    sent = []
    async def fake(msg): sent.append(msg)
    b._send = fake
    asyncio.run(b._place_order(sig))
    assert len(sent) == 1 and sent[0].order_type == 'ioc' and sent[0].side == 'sell'
    assert 0 < sent[0].quantity <= 60


def test_event2_never_shorts_when_gate_closed_or_ssr():
    t, b = make('event2')
    t.on_event('WEEK_CHANGED', '', {'week': 6, 'scenario': {'flags': {'shorts_allowed': True}}})   # gate not known
    sent = []
    async def fake(msg): sent.append(msg)
    b._send = fake
    b.market.update_book(book('MSFT', 400)); t._refs['MSFT'] = 400.
    asyncio.run(b._place_order(Signal(symbol='MSFT', side='sell', quantity=10, price=399.)))
    assert sent == []
    _short_ready(t)
    t.on_event('SSR_ON', '', {'symbol': 'MSFT'})
    asyncio.run(b._place_order(Signal(symbol='MSFT', side='sell', quantity=10, price=399.)))
    assert sent == []


def test_event2_cover_is_not_blocked_by_gross_cap_and_short_respects_seat_cap():
    t, b = make('event2'); _short_ready(t)
    b.market.update_book(book('MSFT', 400)); t._refs['MSFT'] = 400.
    sent = []
    async def fake(msg): sent.append(msg)
    b._send = fake
    b.portfolio.positions['MSFT'] = -150
    asyncio.run(b._place_order(Signal(symbol='MSFT', side='sell', quantity=50, price=399.)))
    assert sent == []                                   # already at the 150-share seat cap
    t._inflight.clear()
    asyncio.run(b._place_order(Signal(symbol='MSFT', side='buy', quantity=500, price=402.)))
    assert len(sent) == 1 and sent[0].quantity == 150   # cover limited to the short, no cash/gross gate
