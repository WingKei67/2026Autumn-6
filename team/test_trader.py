"""Offline unit tests against the actual course SDK models (no venue connection)."""
from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import sys
from pathlib import Path

# When test_trader.py is copied next to the deliverable in the course repo,
# discover that repo automatically. ARENA_SOURCE_ROOT may override it.
HERE = Path(__file__).resolve().parent
candidates = [HERE, HERE.parent, Path('/mnt/data/arena_source/2026Autumn-6')]
SRC = Path(os.environ['ARENA_SOURCE_ROOT']) if os.environ.get('ARENA_SOURCE_ROOT') else next(
    (p for p in candidates if (p/'engine'/'shared').is_dir() and (p/'arena').is_dir()),
    HERE)
if (SRC/'engine').is_dir():
    sys.path.insert(0, str(SRC / 'engine'))
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location('sixrings_v4_test', Path(__file__).with_name('trader.py'))
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

from shared.messages import OrderAck, PortfolioUpdate, SessionEvent


class Market:
    def __init__(self, prices=None):
        self.current = dict(prices or {'AAPL': 220.0, 'GOOGL': 180.0, 'NFLX': 520.0})
        self.hist = {s: [p] * 75 for s,p in self.current.items()}
    def symbols(self):return list(self.current)
    def best_bid(self,s):return self.current[s]*0.9997
    def best_ask(self,s):return self.current[s]*1.0003
    def prices(self,s):return list(self.hist[s])
    def order_book_imbalance(self,s):return 0.5
    def update(self,s,p):
        self.current[s] = p
        self.hist[s].append(p)


class Portfolio:
    def __init__(self, cash=100000.0, positions=None):
        self.cash=cash
        self.positions=positions or {}
    def can_buy(self,s,q,px):return self.cash >= q*px*1.001
    def can_sell(self,s,q):return self.positions.get(s,0)>=q


def server_update(b, market, cash=100000.0, positions=None):
    pos=positions or {}
    eq=cash+sum(market.current[s]*q for s,q in pos.items())
    update=PortfolioUpdate(team_id='6_rings_trader_1',cash=cash,positions=pos,
        realized_pnl=0,unrealized_pnl=0,total_fees_paid=0,net_worth=eq)
    b.on_portfolio_update(update)
    return Portfolio(cash,pos)


def synced_bot(positions=None, cash=100000.0, market=None):
    market=market or Market()
    b=m.MyTrader()
    b.on_connected()
    p=server_update(b,market,cash,positions)
    b.session_open=True
    b._first_signal=b.on_tick(market,p)
    assert b.synced
    return b,market,p


def test_requires_real_exchange_snapshot():
    b=m.MyTrader()
    market=Market({'INTC':26.0,'AAPL':220.0,'NFLX':520.0})
    p=Portfolio()
    b.session_open=True
    for _ in range(7):
        assert b.on_tick(market,p) is None
    probe=b.on_tick(market,p)
    assert probe and probe.quantity==1 and probe.side=='buy'
    assert not b.synced
    # A real IOC fill triggers the authoritative exchange PortfolioUpdate.
    b.on_order_ack(OrderAck(order_id='probe',team_id='6_rings_trader_1',
        symbol=probe.symbol,side='buy',price=probe.price,quantity=1))
    b.on_fill('buy',probe.symbol,1,probe.price)
    p=server_update(b,market,cash=154602.53-26.0078,positions={'NFLX':16,'AAPL':9,'INTC':1})
    assert b.on_tick(market,p) is None  # synthetic account sync after fill
    assert b.synced
    assert b.initial_equity > 160000
    assert b.positions_at_sync == {'NFLX':16,'AAPL':9,'INTC':1}


def test_trailing_stop_legacy():
    b,market,p = synced_bot({'NFLX':16},cash=155000)
    market.update('NFLX',490)  # drop >2.8% from 520
    signal=b.on_tick(market,p)
    assert signal is not None
    assert signal.symbol=='NFLX' and signal.side=='sell' and signal.quantity==16
    assert b.pending.reason == 'trailing_stop'


def test_concentration_reduction():
    b,market,p=synced_bot({'NFLX':70},cash=110000)
    sig=b._first_signal
    assert sig is not None
    assert sig.symbol=='NFLX' and sig.side=='sell'
    assert b.pending.reason=='concentration'
    assert sig.quantity <= b.MAX_EXIT_ORDER_SHARES


def test_drawdown_circuit_breaker():
    b,market,p=synced_bot({'NFLX':18},cash=100000)
    market.update('NFLX',200)
    signal=b.on_tick(market,p)
    assert b.risk_only
    assert signal is not None and signal.side=='sell'
    assert b.pending.reason=='drawdown_reduce'


def test_ack_and_partial_fill_releases_ioc():
    b,market,p=synced_bot({'NFLX':70},cash=110000)
    sig=b._first_signal
    assert sig is not None
    ack=OrderAck(order_id='oid',team_id='6_rings_trader_1',
                 symbol=sig.symbol,side=sig.side,price=sig.price,
                 quantity=sig.quantity)
    b.on_order_ack(ack)
    b.on_fill(sig.side,sig.symbol,3,sig.price)
    assert b.pending.filled==3
    for _ in range(3):
        b.on_tick(market,p)
    assert b.pending is None
    assert not b.paused


def test_no_ack_fail_closed():
    b,market,p=synced_bot({'NFLX':70},cash=110000)
    assert b._first_signal is not None
    for _ in range(b.IOC_UNCONFIRMED_TICKS+1):
        b.on_tick(market,p)
    assert b.paused


def test_ipo_one_indication():
    b=m.MyTrader()
    assert b.on_ipo('ORCA',24,28,4000,{})==(50,28)
    assert b.on_ipo('ORCA',24,28,4000,{}) is None


def test_negative_event_forces_exit():
    b,market,p=synced_bot({'AAPL':12},cash=110000)
    b.on_event('CALENDAR_EVENT','AAPL earnings -6.2%', {'symbol':'AAPL','pct':-0.062})
    sig=b.on_tick(market,p)
    assert sig is not None and sig.side=='sell' and sig.symbol=='AAPL'
    assert b.pending.reason=='negative_event'


def test_cost_gate_blocks_weak_signal():
    b,market,p=synced_bot(cash=200000)
    for _ in range(80):
        assert b.on_tick(market,p) is None
    assert b.attempts==0


def test_positive_event_buy_when_cost_covered():
    b,market,p=synced_bot(cash=200000)
    b.on_event('CALENDAR_EVENT','AAPL earnings +6.2%', {'symbol':'AAPL','pct':0.062})
    sig=b.on_tick(market,p)
    assert sig and sig.side=='buy' and sig.symbol=='AAPL'
    assert sig.quantity <= b.MAX_NEW_ORDER_SHARES
    assert b.pending.reason=='event_positive'


def test_reconnect_requires_new_update():
    b,market,p=synced_bot(cash=200000)
    b.on_connected()
    assert b.on_tick(market,p) is None
    assert not b.synced
    server_update(b,market,cash=200000)
    b.session_open=True
    b.on_tick(market,p)
    assert b.synced


def test_transport_emits_ioc():
    b=m.MyTrader()
    runner=m._ExecutionAwareBot(b)
    sent=[]
    async def intercept(message):
        sent.append(message)
    runner._send=intercept
    sig=m.Signal(symbol='AAPL',side='buy',quantity=3,price=220.2)
    asyncio.run(runner._place_order(sig))
    assert len(sent)==1 and sent[0].order_type=='ioc'
    assert sent[0].quantity==3


def test_price_spike_guard_blocks_mismatch():
    b,market,p=synced_bot({'NFLX':16},cash=100000)
    # New portfolio snapshot inconsistent with the local mark by >2.2%.
    b.on_portfolio_update(PortfolioUpdate(team_id='6_rings_trader_1',
      cash=100000,positions={'NFLX':16},realized_pnl=0,unrealized_pnl=0,
      total_fees_paid=0,net_worth=300000))
    assert b.on_tick(market,p) is None


def test_quote_rejects_missing_position_marks():
    b,market,p=synced_bot(cash=150000)
    b.on_portfolio_update(PortfolioUpdate(team_id='6_rings_trader_1',
      cash=100000,positions={'ORCA':36},realized_pnl=0,unrealized_pnl=0,
      total_fees_paid=0,net_worth=101000))
    p=Portfolio(100000,{'ORCA':36})
    assert b.on_tick(market,p) is None


def test_actual_portfolio_shape_and_value():
    # Representative quantity/cash snapshot from the user's AlgoArena log;
    # market marks here are illustrative, not fabricated authoritative prices.
    q={'META':16,'NFLX':16,'MSFT':16,'TSLA':16,'AMD':16,'NVDA':18,
       'AAPL':9,'GOOGL':9,'ORCA':36,'AMZN':5,'INTC':16}
    mids={'META':649.1569,'NFLX':521.5881,'MSFT':411.9488,'TSLA':235.2775,
       'AMD':197.5938,'NVDA':112.2055,'AAPL':221.2923,'GOOGL':169.3082,
       'ORCA':27.7781,'AMZN':163.9036,'INTC':26.0875}
    market=Market(mids)
    b,p_market,p=synced_bot(positions=q,cash=154602.53,market=market)
    assert len(b.positions_at_sync)==11
    assert b.initial_equity>190000
    assert b.synced
    assert b.positions_at_sync['NFLX']==16
    assert not b.risk_only


def test_positive_event_profit_target_exits():
    b,market,p=synced_bot(cash=200000)
    b.on_event('CALENDAR_EVENT','AAPL earnings +6.2%',
               {'symbol':'AAPL','pct':0.062})
    order=b.on_tick(market,p)
    assert order is not None and order.side=='buy'
    b.on_order_ack(OrderAck(order_id='buy1',team_id='6_rings_trader_1',
        symbol='AAPL',side='buy',price=order.price,quantity=order.quantity))
    b.on_fill('buy','AAPL',order.quantity,order.price)
    new_cash=p.cash-order.quantity*order.price*(1+b.fee)
    p=server_update(b,market,cash=new_cash,positions={'AAPL':order.quantity})
    for i in range(1,26):
        market.update('AAPL',220.0*(1+0.003*i))
        signal=b.on_tick(market,p)
        if signal and signal.side=='sell':
            assert signal.symbol=='AAPL'
            assert b.pending.reason in {'event_target','protect_profit'}
            break
    else:
        raise AssertionError('Event profit target never liquidated')


def test_macro_trough_is_captured_before_rebound():
    b,market,p=synced_bot(cash=200000)
    b.on_event('CALENDAR_EVENT','Market-wide econ print -2.5%',
               {'kind':'econ_print','pct':-0.025})
    assert b.econ_reference.get('AAPL') == 220.0
    # The drop occurs *before* the waiting/reversal period.
    for j in range(23):
        market.update('AAPL',220.0*(1-0.027*j/22))
        b.on_tick(market,p)
    assert b.econ_lows['AAPL'] < 215
    market.update('AAPL',b.econ_lows['AAPL']*1.0055)
    seen=None
    for _ in range(10):
        seen=b.on_tick(market,p)
        if seen:break
    assert seen and seen.symbol=='AAPL' and seen.side=='buy'
    assert b.pending.reason=='event_rebound'


def test_ordinary_momentum_stays_disabled_even_after_surge():
    b,market,p=synced_bot(cash=200000)
    assert not b.ENABLE_ORDINARY_ALPHA
    for i in range(90):
        market.update('AAPL',220.0*(1+0.0008*(i+1)))
        assert b.on_tick(market,p) is None
    assert b.attempts==0


def test_full_fill_does_not_create_extra_order():
    b,market,p=synced_bot({'NFLX':70},cash=110000)
    order=b._first_signal
    b.on_order_ack(OrderAck(order_id='o',team_id='6_rings_trader_1',
                            symbol=order.symbol,side=order.side,
                            price=order.price,quantity=order.quantity))
    b.on_fill(order.side,order.symbol,order.quantity,order.price)
    # Authoritative update reconciles new held inventory.
    new_qty=70-order.quantity
    p=server_update(b,market,cash=p.cash+order.quantity*order.price*(1-b.fee),
                    positions={'NFLX':new_qty})
    for _ in range(3):b.on_tick(market,p)
    assert b.pending is None
    assert not b.paused


def test_probe_is_bounded_and_fails_closed():
    b=m.MyTrader()
    market=Market({'INTC':26.0, 'AAPL':220.0})
    b.on_connected()
    b.session_open=True
    p=Portfolio()
    for _ in range(b.SYNC_PROBE_WAIT_TICKS-1):
        assert b.on_tick(market,p) is None
    probe=b.on_tick(market,p)
    assert probe and probe.symbol=='INTC' and probe.quantity==1
    for _ in range(b.IOC_UNCONFIRMED_TICKS+3):
        assert b.on_tick(market,p) is None
    assert b.sync_probes == 1
    assert b.paused
    assert not b.synced


def test_inherited_shorts_fail_closed():
    market=Market()
    b=m.MyTrader()
    b.on_connected()
    p=server_update(b,market,cash=200000,positions={'AAPL':-5})
    b.session_open=True
    assert b.on_tick(market,p) is None
    assert b.hard_block


def test_unfilled_event_ioc_can_retry_after_ack():
    b,market,p=synced_bot(cash=200000)
    b.on_event('CALENDAR_EVENT','AAPL earnings +6.2%',
               {'symbol':'AAPL','pct':0.062})
    order=b.on_tick(market,p)
    assert order and b.events['AAPL'].entered
    b.on_order_ack(OrderAck(order_id='id',team_id='6_rings_trader_1',
          symbol='AAPL',side='buy',price=order.price,quantity=order.quantity))
    for _ in range(3):
        b.on_tick(market,p)
    assert b.pending is None
    assert not b.events['AAPL'].entered
    # Entry retry is still rate-gated so it cannot spam the exchange.
    assert b.on_tick(market,p) is None
