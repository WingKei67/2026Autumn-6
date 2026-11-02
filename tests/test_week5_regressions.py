"""Offline regression tests of our actual bot classes and real exchange handlers."""
import asyncio
import contextlib
import time

import pytest
from arena import Signal
from broker.broker import BrokerBot
import broker.config as bc
import trader.config as tc
import exchange.config as ec
from exchange.server import ExchangeServer, Portfolio
from exchange.scenario import load_week
from shared.messages import (BookSnapshot, PlaceOrder, CancelOrder, OrderAck, ErrorMsg,
                             SessionEvent, TradeExecution, PortfolioUpdate)
from team.trader import MyTrader, _Bot
from team.broker import MyBroker, _RiskBrokerBot


@pytest.fixture
def trader():
    t = MyTrader(); b = _Bot(t)
    t._week5_active = True
    b._session_open = True
    b._has_server_snapshot = True
    b._snapshot_at = time.monotonic()
    b.portfolio.cash = 175000
    b.portfolio._net_worth = 175000
    b.market.update_book(BookSnapshot(symbol='NFLX', bids=[[99, 100]], asks=[[100, 100]],
        mid_price=100, ref_price=100, spread=1))
    t._refs['NFLX'] = 100
    return b


@pytest.fixture
def broker(monkeypatch):
    b = _RiskBrokerBot(MyBroker())
    b._graded_week5 = b._continuous = b._has_snapshot = b._session_open = True
    b._snapshot_at = time.monotonic()
    b.state.cash = 350000
    b.state.exchange_prices['NFLX'] = 100
    b.sent = []
    async def send(self, msg): b.sent.append(msg)
    monkeypatch.setattr(BrokerBot, '_send', send)
    return b


def order(side='buy', qty=2, price=100):
    return PlaceOrder(team_id=bc.TEAM_ID, symbol='NFLX', side=side,
                      order_type='limit', quantity=qty, price=price)


@pytest.mark.parametrize('pos,qty', [(149,1), (150,0), (151,0)])
def test_trader_exact_boundary(trader, pos, qty):
    trader.portfolio.positions['NFLX'] = pos
    sent = []
    async def send(msg): sent.append(msg)
    trader._send = send
    asyncio.run(trader._place_order(Signal(symbol='NFLX', side='buy', price=100, quantity=10)))
    assert sum(m.quantity for m in sent) == qty


def test_concurrent_and_delayed_orders_keep_reservation(trader):
    sent = []
    async def send(msg):
        await asyncio.sleep(.001)
        sent.append(msg)
    trader._send = send
    async def run():
        await asyncio.gather(*(trader._place_order(Signal(symbol='NFLX', side='buy',
             price=100, quantity=150)) for _ in range(10)))
    asyncio.run(run())
    assert len(sent) == 1
    trader._owner._inflight['NFLX'][2] -= 1000
    trader._owner.on_tick(trader.market, trader.portfolio)
    assert 'NFLX' in trader._owner._inflight


def test_partial_fill_duplicate_and_terminal_confirmation(trader):
    trader._owner._inflight['NFLX'] = ['buy', 10, time.monotonic(), 100]
    msg = TradeExecution(trade_id='partial', symbol='NFLX', price=100, quantity=3,
        buyer_id=tc.TEAM_ID, seller_id='other', aggressor='buy', fee=.3, taker_id=tc.TEAM_ID)
    trader._on_trade(msg); trader._on_trade(msg)
    assert trader.portfolio.positions['NFLX'] == 3
    assert trader._owner._inflight['NFLX'][1] == 7
    trader._terminal_orders['ioc-id'] = 'NFLX'
    assert not trader._confirm_terminal(ErrorMsg(code='RATE_LIMITED', message='try later'))
    assert trader._confirm_terminal(ErrorMsg(code='ORDER_NOT_FOUND', message="Order 'ioc-id' not found"))
    assert not trader._owner._inflight


@pytest.mark.parametrize('reason', ['stale', 'equity', 'unknown_mark', 'cash', 'liquidated'])
def test_trader_deterioration_blocks_new_risk(trader, reason):
    trader._connects = 1
    if reason == 'stale': trader._snapshot_at -= 11
    elif reason == 'equity': trader.portfolio._net_worth = 80000
    elif reason == 'unknown_mark': trader.portfolio.positions['UNKNOWN'] = 3
    elif reason == 'cash': trader.portfolio.cash = 0
    elif reason == 'liquidated': trader.portfolio.liquidated = True
    sent = []
    async def send(msg): sent.append(msg)
    trader._send = send
    asyncio.run(trader._place_order(Signal(symbol='NFLX', side='buy', price=100, quantity=2)))
    assert not sent


def test_broker_cancel_race_and_replacement(broker):
    async def run():
        await broker._send(order())
        broker._on_order_ack(OrderAck(team_id=bc.TEAM_ID, symbol='NFLX', side='buy',
            price=100, quantity=2, order_id='quote'))
        await broker.cancel_quotes('NFLX')
        await broker._send(order())
        assert len([m for m in broker.sent if isinstance(m, PlaceOrder)]) == 1
        broker._on_error(ErrorMsg(code='ORDER_NOT_FOUND', message="Order 'quote' not found"))
        await broker._send(order())
        assert len([m for m in broker.sent if isinstance(m, PlaceOrder)]) == 2
        broker._on_error(ErrorMsg(code='ORDER_NOT_FOUND', message="Order 'quote' not found"))
        assert broker._has_snapshot  # duplicate confirmation cannot stop trading
    asyncio.run(run())


def test_broker_no_short_and_cash_reservation(broker):
    async def run():
        await broker._send(order('sell', 100))
        assert not broker.sent
        broker.state.cash = 210150
        await broker._send(order('buy', 10))
        assert broker.sent[-1].quantity == 1
    asyncio.run(run())


def test_broker_unknown_marks_and_equity(broker):
    broker.state.positions['UNKNOWN'] = 1
    asyncio.run(broker._send(order()))
    assert not broker.sent
    broker.state.positions.clear(); broker._last_equity = 100000
    asyncio.run(broker._send(order()))
    assert not broker.sent


def test_reconnect_market_state_preserves_then_reconciles_halt(trader):
    t = trader._owner
    t.on_event('TRADING_HALT', '', {'symbol':'NFLX'})
    t.on_reconnect()
    assert 'NFLX' in t._halted_symbols
    asyncio.run(trader._on_session_event(SessionEvent(event='MARKET_STATE', message='',
        data={'halted_symbols':['NFLX'], 'market_halted':False})))
    assert 'NFLX' in t._halted_symbols
    asyncio.run(trader._on_session_event(SessionEvent(event='MARKET_STATE', message='',
        data={'halted_symbols':[], 'market_halted':False})))
    assert 'NFLX' not in t._halted_symbols and 'NFLX' in t._resume_pending


@pytest.mark.parametrize('seat', ['6_rings_trader_1', '6_rings_trader_2', '6_rings_broker'])
def test_actual_bot_real_exchange_halt_resume_and_fills(monkeypatch, tmp_path, seat):
    """Real matching/risk and wire dispatch; in-memory transport, no live accounts."""
    async def run():
        monkeypatch.setattr(tc, 'TEAM_ID', seat)
        monkeypatch.setattr(bc, 'TEAM_ID', seat)
        for name in ('CIRCUIT_BREAKERS_ENABLED', 'MAINTENANCE_FRACTION', 'LULD_BAND_PCT',
                     'OPENING_AUCTION_TICKS', 'CLOSING_AUCTION', 'SSR_TRIGGER_PCT', 'SHORT_LOCATE_CAP'):
            monkeypatch.setattr(ec, name, getattr(ec, name))
        scen = load_week(5); scen.apply()
        server = ExchangeServer(); server.scenario = scen; server.session_open = True
        sym = 'NFLX'; server.ref_prices[sym] = 100
        role = 'broker' if seat.endswith('broker') else 'trader'
        cash = 350000 if role == 'broker' else 175000
        server.portfolios[seat] = Portfolio(team_id=seat, role=role, level=1, cash=cash)
        server.portfolios['liquidity'] = Portfolio(team_id='liquidity', role='broker', level=1, cash=1000000)
        server.portfolios['liquidity'].positions[sym] = 100
        server.books[sym].place_order('liquidity', 'sell', 100, 100)
        frames = asyncio.Queue(); commands = asyncio.Queue(); errors = []
        class ServerSocket:
            async def send(self, raw):
                if '"type":"error"' in raw:
                    errors.append(ErrorMsg.model_validate_json(raw).code)
                await frames.put(raw)
        class BotSocket:
            async def send(self, raw): await commands.put(raw)
            def __aiter__(self): return self
            async def __anext__(self): return await frames.get()
            async def close(self): pass
        server.clients[seat] = ServerSocket()
        bot = _RiskBrokerBot(MyBroker()) if role == 'broker' else _Bot(MyTrader())
        bot._ws = BotSocket()
        if role == 'broker':
            bot._graded_week5 = bot._continuous = bot._session_open = True
            bot.state.exchange_prices[sym] = 100
            bot._on_portfolio(server.portfolios[seat].to_message(server.ref_prices))
        else:
            bot._state_path = tmp_path / 'books.json'
            bot._owner._week5_active = bot._session_open = True
            bot.market.update_book(server._make_snapshot(sym, server.books[sym]))
            bot._on_portfolio(server.portfolios[seat].to_message(server.ref_prices))
        async def venue():
            while True:
                raw = await commands.get()
                if '"type":"place_order"' in raw:
                    await server._handle_place_order(server.clients[seat], PlaceOrder.model_validate_json(raw), seat)
                else:
                    await server._handle_cancel_order(server.clients[seat], CancelOrder.model_validate_json(raw), seat)
        listener = asyncio.create_task(bot.listen_to_exchange() if role == 'broker' else bot.listen())
        worker = asyncio.create_task(venue())
        try:
            # Exercise actual halt event handlers and actual resumed order ACK/fill.
            await server._halt_symbol(sym, 'isolated regression', duration=.02)
            await asyncio.sleep(.005)
            if role == 'broker':
                assert sym in bot._halted_symbols
                await bot.quote_symbol(sym)
            else:
                assert sym in bot._owner._halted_symbols
                await bot._place_order(Signal(symbol=sym, side='buy', price=100, quantity=1))
            assert commands.empty()
            await asyncio.sleep(.04)
            if role == 'trader':
                sig = bot._owner.on_tick(bot.market, bot.portfolio)
                assert sig is not None
                await bot._place_order(sig)
            await asyncio.sleep(.04)
            if role == 'broker':
                await bot.cancel_quotes(sym)
                await asyncio.sleep(.02)
                assert not bot._resume_pending
            else:
                assert not bot._owner._resume_pending
                assert not bot._owner._inflight
                assert bot.ledger.positions == server.portfolios[seat].positions
            assert server.portfolios[seat].positions.get(sym, 0) == 1
            assert not server.portfolios[seat].liquidated
            assert all(e == 'ORDER_NOT_FOUND' for e in errors)
            await server._apply_carry_and_maintenance()
            assert not server.portfolios[seat].liquidated
        finally:
            listener.cancel(); worker.cancel()
            for task in (listener, worker):
                with contextlib.suppress(asyncio.CancelledError): await task
    asyncio.run(run())


def test_all_three_actual_seat_allocations_against_team_risk_engine(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setattr(ec, 'team_of', lambda seat: 'six' if seat.startswith('6_rings_') else None)
        monkeypatch.setattr(ec, 'POSITION_LIMIT_SHARES', 800)
        server = ExchangeServer(); server.session_open = True
        server.ref_prices['NFLX'] = 100
        server.portfolios['liquidity'] = Portfolio(team_id='liquidity', role='broker', level=1, cash=1000000)
        server.portfolios['liquidity'].positions['NFLX'] = 1000
        server.books['NFLX'].place_order('liquidity', 'sell', 100, 1000)
        class Socket:
            async def send(self, raw): pass
        for seat in ('6_rings_trader_1', '6_rings_trader_2', '6_rings_broker'):
            monkeypatch.setattr(tc, 'TEAM_ID', seat); monkeypatch.setattr(bc, 'TEAM_ID', seat)
            role = 'broker' if seat.endswith('broker') else 'trader'
            cash = 350000 if role == 'broker' else 175000
            server.portfolios[seat] = Portfolio(team_id=seat, role=role, level=1, cash=cash)
            ws = Socket(); server.clients[seat] = ws
            if role == 'trader':
                bot = _Bot(MyTrader()); bot._state_path = tmp_path / (seat + '.json')
                bot._owner._week5_active = bot._session_open = True
                bot._owner._refs['NFLX'] = 100
                bot._on_portfolio(server.portfolios[seat].to_message(server.ref_prices))
                async def send(msg): await server._handle_place_order(ws, msg, seat)
                bot._send = send
                await bot._place_order(Signal(symbol='NFLX', side='buy', price=100, quantity=1000))
            else:
                bot = _RiskBrokerBot(MyBroker())
                bot._graded_week5 = bot._continuous = bot._session_open = True
                bot.state.exchange_prices['NFLX'] = 100
                bot._on_portfolio(server.portfolios[seat].to_message(server.ref_prices))
                async def send(self, msg): await server._handle_place_order(ws, msg, seat)
                monkeypatch.setattr(BrokerBot, '_send', send)
                await bot._send(order(qty=1000))
        positions = [server.portfolios[s].positions.get('NFLX', 0) for s in
                     ('6_rings_trader_1', '6_rings_trader_2', '6_rings_broker')]
        assert positions == [150, 150, 20]
        assert sum(positions) == 320 < 800
    asyncio.run(run())


def test_full_loss_of_long_inventory_still_above_maintenance(monkeypatch):
    monkeypatch.setattr(ec, 'LIQUIDATION_ENABLED', True)
    monkeypatch.setattr(ec, 'MAINTENANCE_FRACTION', .5)
    server = ExchangeServer()
    for seat, starting, gross in [('trader',175000,70000), ('broker',350000,75000)]:
        p = Portfolio(team_id=seat, role=seat, level=1, cash=.60 * starting)
        p.starting_cash = starting
        p.positions['NFLX'] = int(gross / 100)
        server.portfolios[seat] = p
    server.ref_prices['NFLX'] = .001  # effectively total loss, larger than scenario shocks
    asyncio.run(server._apply_carry_and_maintenance())
    assert all(not p.liquidated for p in server.portfolios.values())


def test_broker_missing_stale_snapshot_and_no_resting_stack(broker):
    broker._has_snapshot = False
    asyncio.run(broker._send(order()))
    assert not broker.sent
    broker._has_snapshot = True; broker._snapshot_at -= 11
    asyncio.run(broker._send(order()))
    assert not broker.sent
    broker._snapshot_at = time.monotonic()
    asyncio.run(broker._send(order()))
    assert broker.sent[-1].order_type == 'ioc'


def test_uncorrelated_reject_disables_broker_until_reconciliation(broker):
    asyncio.run(broker._send(order()))
    broker._on_error(ErrorMsg(code='PRICE_BAND', message='stale price'))
    assert not broker._has_snapshot
    assert broker._orders


def test_resume_at_full_cap_reduces_inventory(trader):
    trader.portfolio.positions['NFLX'] = 150
    trader.portfolio.cash = 105000
    trader._owner.on_event('TRADING_RESUMED', '', {'symbol':'NFLX'})
    sig = trader._owner.on_tick(trader.market, trader.portfolio)
    assert sig.side == 'sell' and sig.quantity == 1
    sent = []
    async def send(msg): sent.append(msg)
    trader._send = send
    asyncio.run(trader._place_order(sig))
    assert sent[0].side == 'sell'


def test_broker_ack_for_old_opposite_side_does_not_prove_resume(broker):
    async def run():
        broker.state.positions['NFLX'] = 2
        await broker._send(order('sell'))
        broker._resume_probes.add('NFLX')
        broker._resume_pending.add('NFLX')
        broker._on_order_ack(OrderAck(team_id=bc.TEAM_ID, symbol='NFLX', side='sell',
            quantity=2, price=100, order_id='pre-halt'))
        assert 'NFLX' in broker._resume_pending
    asyncio.run(run())
