import asyncio
import time
import pytest
from team.trader import MyTrader, _Bot, SEAT_POSITION_CAP
from team.broker import MyBroker, _RiskBrokerBot, BROKER_SYMBOL_CAP, BROKER_QUOTE_CAP
from shared.messages import BookSnapshot, PlaceOrder, SessionEvent
from broker.broker import BrokerBot


def sample_book(symbol='NFLX', price=100):
    return BookSnapshot(symbol=symbol, bids=[[price - .1, 20]],
        asks=[[price+.1, 20]], mid_price=price, spread=.2, ref_price=price)


def test_trader_halt_and_resume_probe():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b.market.update_book(sample_book())
    b.portfolio.cash=175_000.0; b.portfolio._net_worth=175_000.0
    b._session_open=True
    t.on_event('TRADING_HALT','',{'symbol':'NFLX'})
    assert t.on_tick(b.market,b.portfolio) is None
    t.on_event('TRADING_RESUMED','',{'symbol':'NFLX'})
    sig=t.on_tick(b.market,b.portfolio)
    assert sig is not None and sig.symbol=='NFLX' and sig.quantity==1


def test_trader_symbol_and_gross_limits():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b._session_open=True
    b.portfolio.cash=175_000; b.portfolio._net_worth=175_000
    b.market.update_book(sample_book('NFLX',1000))
    t._refs['NFLX']=1000
    sent=[]
    async def fake(msg): sent.append(msg)
    b._send=fake
    from arena import Signal
    asyncio.run(b._place_order(Signal(symbol='NFLX',side='buy',quantity=800,price=1000)))
    assert len(sent)==1 and 0 < sent[0].quantity <= 70
    assert sent[0].quantity < SEAT_POSITION_CAP


def test_trader_does_not_send_into_halt():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b._session_open=True
    b.portfolio.cash=175_000; b.portfolio._net_worth=175_000
    sent=[]
    async def fake(msg):sent.append(msg)
    b._send=fake
    from arena import Signal
    t.on_event('TRADING_HALT','',{'symbol':'NFLX'})
    asyncio.run(b._place_order(Signal(symbol='NFLX',side='buy',quantity=1,price=100)))
    assert not sent


def test_broker_hard_cap_across_resting(monkeypatch):
    sent=[]
    async def fake(self,msg): sent.append(msg)
    monkeypatch.setattr(BrokerBot, '_send', fake)
    b=_RiskBrokerBot(MyBroker())
    b.state.exchange_prices['NFLX']=100
    b.state.cash=350_000
    b.state.cash=350_000
    b._has_snapshot=True; b._snapshot_at=time.monotonic()
    b._graded_week5=True; b._continuous=True
    b._session_open=True
    for _ in range(20):
        asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',order_type='limit',price=99,quantity=5)))
    q=sum(m.quantity for m in sent)
    assert q == 5  # single unacknowledged order; repeated submissions blocked
    b.state.positions['NFLX']=BROKER_SYMBOL_CAP
    sent.clear(); b._orders.clear()
    asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',order_type='limit',price=99,quantity=20)))
    assert sent == []


def test_broker_halt_resume(monkeypatch):
    sent=[]
    async def fake(self,msg):sent.append(msg)
    monkeypatch.setattr(BrokerBot, '_send', fake)
    b=_RiskBrokerBot(MyBroker()); b._session_open=True; b._has_snapshot=True; b._snapshot_at=time.monotonic(); b._graded_week5=True; b._continuous=True
    b.state.exchange_prices['NFLX']=100
    b.state.cash=350_000
    asyncio.run(b._on_session_event(SessionEvent(event='TRADING_HALT',message='halt',data={'symbol':'NFLX'})))
    asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',order_type='limit',price=99,quantity=1)))
    assert not sent
    asyncio.run(b._on_session_event(SessionEvent(event='TRADING_RESUMED',message='resume',data={'symbol':'NFLX'})))
    asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',order_type='limit',price=99,quantity=1)))
    assert len(sent)==1  # only the post-resume IOC; competing quote blocked


def test_broker_does_not_recycle_cancelled_risk_budget(monkeypatch):
    """Canceled orders have no venue ACK: lifetime submissions must stay capped."""
    sent=[]
    async def fake(self,msg): sent.append(msg)
    monkeypatch.setattr(BrokerBot, '_send', fake)
    b=_RiskBrokerBot(MyBroker()); b._session_open=True; b._has_snapshot=True; b._snapshot_at=time.monotonic(); b._graded_week5=True; b._continuous=True
    b.state.exchange_prices['NFLX']=100
    b.state.cash=350_000
    from shared.messages import CancelOrder
    # Simulate multiple quote replacements, with cancel attempts in between.
    for _ in range(100):
        asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',
                                      order_type='limit',price=99,quantity=2)))
        asyncio.run(b.cancel_quotes('NFLX'))
    assert sum(x.quantity for x in sent if isinstance(x,PlaceOrder)) == 2  # no ACK/absence proof, so risk remains reserved


def test_broker_never_quotes_before_authoritative_snapshot(monkeypatch):
    sent=[]
    async def fake(self,msg): sent.append(msg)
    monkeypatch.setattr(BrokerBot, '_send', fake)
    b=_RiskBrokerBot(MyBroker()); b._session_open=True; b._graded_week5=True; b._continuous=True
    b.state.exchange_prices['NFLX']=100
    b.state.cash=350_000
    asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',
                                  order_type='limit',price=99,quantity=5)))
    assert not sent


def test_post_resume_ack_must_be_for_post_resume_order():
    t=MyTrader()
    t.on_event('TRADING_HALT','',{'symbol':'AMD'})
    t.on_event('TRADING_RESUMED','',{'symbol':'AMD'})
    assert 'AMD' in t._resume_pending and 'AMD' not in t._resume_sent


def test_trader_rejects_stale_price_after_shock():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b._session_open=True
    b.portfolio.cash=175_000; b.portfolio._net_worth=175_000
    b.market.update_book(sample_book('AMD',100))
    t._refs['AMD']=100
    sent=[]
    async def fake(msg): sent.append(msg)
    b._send=fake
    from arena import Signal
    asyncio.run(b._place_order(Signal(symbol='AMD',side='buy',quantity=1,price=111)))
    assert not sent


def test_trader_resume_when_book_stale_falls_back_to_safe_reference():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b.market.update_book(sample_book('NFLX',125))
    t._refs['NFLX']=100
    b.portfolio.cash=175_000; b.portfolio._net_worth=175_000
    t.on_event('TRADING_HALT','',{'symbol':'NFLX'})
    t.on_event('TRADING_RESUMED','',{'symbol':'NFLX'})
    sig=t.on_tick(b.market,b.portfolio)
    assert sig and sig.quantity==1 and .93*100 <= sig.price <= 1.07*100


def test_other_account_liquidation_does_not_disable_trader():
    t=MyTrader()
    t.on_event('LIQUIDATION','',{'team_id':'another_team_trader'})
    assert not t._market_halted


def test_other_account_liquidation_does_not_disable_broker():
    b=_RiskBrokerBot(MyBroker())
    asyncio.run(b._on_session_event(SessionEvent(event='LIQUIDATION',message='',data={'team_id':'another_broker'})))
    assert not b._disabled


def test_trader_pending_buy_reservation_across_symbols():
    t=MyTrader(); t._week5_active=True; b=_Bot(t); b._has_server_snapshot=True; b._snapshot_at=time.monotonic()
    b._session_open=True
    b.portfolio.cash=175_000; b.portfolio._net_worth=175_000
    b.market.update_book(sample_book('NFLX',1000))
    b.market.update_book(sample_book('AMD',1000))
    t._refs.update({'NFLX':1000,'AMD':1000})
    sent=[]
    async def fake(msg):sent.append(msg)
    b._send=fake
    from arena import Signal
    asyncio.run(b._place_order(Signal(symbol='NFLX',side='buy',quantity=70,price=1000)))
    asyncio.run(b._place_order(Signal(symbol='AMD',side='buy',quantity=70,price=1000)))
    assert sum(m.quantity*m.price for m in sent)<=70_000


def test_reconnect_does_not_leave_stale_halt_lock():
    t=MyTrader()
    t.on_event('TRADING_HALT','',{'symbol':'NFLX'})
    assert 'NFLX' in t._halted_symbols
    t.on_reconnect()
    assert 'NFLX' in t._halted_symbols  # reconnect does not lift a halt
    assert 'NFLX' in t._resume_pending


def test_broker_open_play_is_inactive(monkeypatch):
    sent=[]
    async def fake(self,msg):sent.append(msg)
    monkeypatch.setattr(BrokerBot,'_send',fake)
    b=_RiskBrokerBot(MyBroker()); b._has_snapshot=True; b._snapshot_at=time.monotonic(); b._session_open=True
    b.state.exchange_prices['NFLX']=100
    b.state.cash=350_000
    asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',
                                  order_type='limit',price=99,quantity=2)))
    assert not sent


def test_broker_resume_probe_uses_reserved_extra_budget(monkeypatch):
    sent=[]
    async def fake(self,msg):sent.append(msg)
    monkeypatch.setattr(BrokerBot,'_send',fake)
    b=_RiskBrokerBot(MyBroker()); b._has_snapshot=True; b._snapshot_at=time.monotonic(); b._session_open=True
    b._graded_week5=True; b._continuous=True
    b.state.cash=350_000; b.state.exchange_prices['NFLX']=100
    for _ in range(10):
        asyncio.run(b._send(PlaceOrder(team_id='x',symbol='NFLX',side='buy',
                                      order_type='limit',price=99,quantity=2)))
    assert sum(m.quantity for m in sent) == 2
    # Absence proof allows reuse; the old quote cannot compete with the IOC.
    from shared.messages import OrderAck, ErrorMsg
    import broker.config as cfg
    b._on_order_ack(OrderAck(team_id=cfg.TEAM_ID, symbol="NFLX", side="buy",
        price=99, quantity=2, order_id="old"))
    b._on_error(ErrorMsg(code="ORDER_NOT_FOUND", message="Order 'old' not found"))
    asyncio.run(b._resume_probe('NFLX'))
    assert sum(m.quantity for m in sent)==3
    assert sent[-1].order_type=='ioc'
