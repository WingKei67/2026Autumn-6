"""Causality, real matching/fees/queues and isolated alpha risk regressions."""
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from team.alpha.core import Observation,Quote,PublicEvent,Intent
from team.alpha import EventMomentum,PostHaltReversion,AdaptiveMarketMaker,BasketArbitrage
from alpha_lab.runner import isolated,run_one,default_cash
from alpha_lab.exchange import OfflineExchange,Receiver
from alpha_lab.risk import Gateway
from exchange.server import Portfolio
from shared.messages import PlaceOrder,CancelOrder,TradeExecution


def obs(t,price=100,events=(),positions=None,**kw):
    return Observation(t,{'NFLX':Quote(price-.01,price+.01,20,20)},positions or {},175000,175000,175000,
        events=events,**kw)


def test_event_momentum_requires_public_event_and_confirmation():
    plain=EventMomentum(confirmation=3)
    for t,p in enumerate((100,101,102,103)):
        assert not plain.observe(obs(t,p))
    a=EventMomentum(confirmation=3)
    assert not a.observe(obs(0,100,(PublicEvent('CALENDAR_EVENT',0,('NFLX',),'earnings'),)))
    assert not a.observe(obs(1,101))
    assert not a.observe(obs(2,102))
    result=a.observe(obs(3,103))
    assert result and result[0].side=='buy'


def test_cost_gate_rejects_sub_fee_edge():
    a=EventMomentum(confirmation=3)
    for t,p in enumerate((100,100.05,100.10,100.15)):
        events=(PublicEvent('CALENDAR_EVENT',0,('NFLX',),'earnings'),) if t==0 else ()
        assert not a.observe(obs(t,p,events))


def test_future_perturbation_cannot_change_prefix_signals():
    def run(future):
        a=EventMomentum(confirmation=3)
        signals=[]
        for t,p in enumerate([100,101,102,103]+future):
            e=(PublicEvent('CALENDAR_EVENT',0,('NFLX',),'earnings'),) if t==0 else ()
            signals.append(a.observe(obs(t,p,e)))
        return signals
    assert run([80,70])[:4]==run([120,130])[:4]


@pytest.mark.parametrize('cls',[EventMomentum,PostHaltReversion,AdaptiveMarketMaker,BasketArbitrage])
def test_no_orders_on_halted_symbol(cls):
    a=cls()
    for t in range(20):a.observe(obs(t,100+t))
    intents=a.observe(obs(20,130,halted=frozenset({'NFLX'})))
    assert not [i for i in intents if not i.cancel]


def test_post_halt_uses_strictly_lagged_reference_and_reversal():
    a=PostHaltReversion(z=2.5)
    for t in range(40):a.observe(obs(t,100))
    a.observe(obs(40,90,(PublicEvent('TRADING_HALT',40,('NFLX',)),),halted=frozenset({'NFLX'})))
    assert all(p==100 for _,p in a.halt_reference['NFLX'])
    a.observe(obs(340,90,(PublicEvent('TRADING_RESUMED',340,('NFLX',)),)))
    a.observe(obs(341,90.1));a.observe(obs(342,90.2))
    result=a.observe(obs(343,90.4))
    assert result and result[0].side=='buy'


def test_post_halt_rejects_recent_earnings_repricing():
    a=PostHaltReversion(z=2.5)
    for t in range(40):a.observe(obs(t,100))
    a.observe(obs(40,90,(PublicEvent('CALENDAR_EVENT',40,('NFLX',),'earnings'),PublicEvent('TRADING_HALT',40,('NFLX',)))))
    a.observe(obs(340,90,(PublicEvent('TRADING_RESUMED',340,('NFLX',)),)))
    for t,p in [(341,90.1),(342,90.2),(343,90.4)]:assert not a.observe(obs(t,p))


def test_basket_is_unavailable_when_future_is_disabled():
    a=BasketArbitrage()
    for t in range(150):assert not a.observe(obs(t,100+t*.1))


def test_post_only_quotes_and_inventory_size():
    a=AdaptiveMarketMaker()
    intents=a.observe(obs(4,positions={'NFLX':20},cap=20))
    assert not [i for i in intents if i.side=='buy' and not i.cancel]
    assert all(i.order_type=='post_only' for i in intents if not i.cancel)


def test_real_exchange_next_tick_fill_and_actual_fee():
    async def run():
        with isolated(77,5,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
            account=accounts[0]
            s.portfolios[account]=Portfolio(team_id=account,role='trader',level=1,cash=175000)
            s.portfolios['liquidity']=Portfolio(team_id='liquidity',role='broker',level=1,cash=1e6)
            s.clients[account]=account
            rx=Receiver(account);rx.current=s;s.receivers[account]=rx
            s.books['NFLX'].place_order('liquidity','sell',100,2)
            g=Gateway(s,account,rx,175000)
            g.schedule([Intent('NFLX','buy',2,101)],obs(10))
            s.tick=10;await g.execute_due(10)
            assert not rx.fills
            # Signal-time ask is removed; actual next-tick resting price wins.
            s.books['NFLX'].cancel_team_orders('liquidity')
            s.books['NFLX'].place_order('liquidity','sell',100.50,1)
            s.tick=11;await g.execute_due(11)
            assert len(rx.fills)==1
            assert rx.fills[0]['price']==100.50 and rx.fills[0]['quantity']==1
            assert rx.fills[0]['fee']==pytest.approx(.003*100.50)
            assert s.portfolios[account].positions['NFLX']==1
    asyncio.run(run())


def test_real_queue_priority_partial_fill_and_rebate():
    async def run():
        with isolated(78,5,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
            account=accounts[0];rx=Receiver(account);rx.current=s;s.receivers[account]=rx;s.clients[account]=account
            for name,role in [(account,'broker'),('liquidity','broker'),('flow','trader')]:
                s.portfolios[name]=Portfolio(team_id=name,role=role,level=1,cash=1e6)
            s.portfolios['flow'].positions['NFLX']=20
            # Existing liquidity is ahead at the same price.
            s.books['NFLX'].place_order('liquidity','buy',100,3)
            g=Gateway(s,account,rx,175000,cap=20)
            g.schedule([Intent('NFLX','buy',2,100,'post_only')],obs(1))
            s.tick=2;await g.execute_due(2)
            await s._handle_place_order('flow',PlaceOrder(team_id='flow',symbol='NFLX',side='sell',
                quantity=4,price=100,order_type='ioc'),'flow')
            assert s.portfolios['liquidity'].positions['NFLX']==3
            assert s.portfolios[account].positions['NFLX']==1
            assert rx.fills[-1]['maker']
            assert rx.fills[-1]['rebate']==pytest.approx(.001*100)
            assert rx.pending['NFLX','buy'].quantity==1
    asyncio.run(run())


def test_pending_buy_and_cancel_remain_reserved():
    with isolated(79,5,175000,1) as (_,scenario,accounts):
        s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
        a=accounts[0];s.portfolios[a]=Portfolio(team_id=a,role='trader',level=1,cash=175000)
        r=Receiver(a);g=Gateway(s,a,r,175000,cap=10)
        for _ in range(20):g.schedule([Intent('NFLX','buy',10,100)],obs(1))
        assert len(r.pending)==1 and r.pending['NFLX','buy'].quantity==10
        g.schedule([Intent('NFLX','buy',0,0,cancel=True)],obs(2))
        assert r.pending['NFLX','buy'].quantity==10


def test_cash_floor_and_no_stock_shorts():
    with isolated(80,5,175000,1) as (_,scenario,accounts):
        s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
        a=accounts[0];s.portfolios[a]=Portfolio(team_id=a,role='trader',level=1,cash=105050)
        r=Receiver(a);g=Gateway(s,a,r,175000)
        assert g.allowed_quantity(Intent('NFLX','buy',10,100))==0
        assert g.allowed_quantity(Intent('NFLX','sell',10,100))==0


def test_seed_reproducibility_and_global_config_restoration():
    import exchange.config as cfg
    original_fee=cfg.TAKER_FEE_RATE
    a=asyncio.run(run_one(99,[('event_momentum',{})],ticks=100))
    b=asyncio.run(run_one(99,[('event_momentum',{})],ticks=100))
    assert a==b
    assert cfg.TAKER_FEE_RATE==original_fee
    assert not a['same_tick_fills'] and not a['position_violations']


def test_live_source_hashes_are_unchanged():
    """Broker is pinned to the original protocol hash. team/trader.py (and new team/event2.py) were INTENTIONALLY
    changed (week-aware activation, futures guard, Event2); their approved hashes live in tests/production_pins.json
    with the reason and approval. The original trader is preserved verbatim in research/legacy_trader_e4d8c54.py and
    must still hash to the original pinned value."""
    import hashlib,json
    root=Path(__file__).resolve().parents[1]
    protocol=root/'alpha_results/study/protocol.json'
    if not protocol.exists():pytest.skip('No experiment protocol yet')
    d=json.loads(protocol.read_text())
    assert hashlib.sha256((root/'team/broker.py').read_bytes()).hexdigest()==d['live_broker_sha256']
    pins=json.loads((root/'tests/production_pins.json').read_text())
    for f in ('team/trader.py','team/event2.py'):
        latest=pins[f]['history'][-1]
        assert latest['reason'] and latest['approval']
        assert hashlib.sha256((root/f).read_bytes()).hexdigest()==latest['sha256'], f'{f} changed since last approved pin'
    legacy=(root/'research/legacy_trader_e4d8c54.py').read_text().split('\n',1)[1].encode()
    assert hashlib.sha256(legacy).hexdigest()==d['live_trader_sha256'], 'original-trader control is no longer verbatim'


def test_partial_stock_leg_scales_delayed_futures_hedge():
    async def run():
        with isolated(81,9,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True
            a=accounts[0];rx=Receiver(a);rx.current=s;s.receivers[a]=rx;s.clients[a]=a
            for name,role in [(a,'trader'),('liquidity','broker')]:
                s.portfolios[name]=Portfolio(team_id=name,role=role,level=1,cash=175000)
            s.ref_prices['NVDA']=100;s.ref_prices['ARENA10']=300
            for sym,ref in [('NVDA',100),('ARENA10',300)]:
                s.price_engines[sym].fair_value=s.price_engines[sym].market_price=ref
            s.books['NVDA'].place_order('liquidity','sell',100,1)
            s.books['ARENA10'].place_order('liquidity','buy',300,10)
            o=replace(obs(1),quotes={'NVDA':Quote(99,100,10,1),'ARENA10':Quote(300,301,10,10)},futures_enabled=True)
            g=Gateway(s,a,rx,175000)
            g.schedule([Intent('NVDA','buy',3,100,group='hedge'),Intent('ARENA10','sell',3,300,group='hedge')],o)
            s.tick=2;await g.execute_due(2)
            assert s.portfolios[a].positions['NVDA']==1
            assert s.portfolios[a].positions.get('ARENA10',0)==0
            s.tick=3;await g.execute_due(3)
            assert s.portfolios[a].positions['ARENA10']==-1
            assert not g.position_violations
    asyncio.run(run())


def test_week5_future_gate_is_enforced_by_real_exchange():
    async def run():
        with isolated(82,5,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True
            a=accounts[0];s.portfolios[a]=Portfolio(team_id=a,role='trader',level=1,cash=175000)
            rx=Receiver(a);s.receivers[a]=rx
            await s._handle_place_order(a,PlaceOrder(team_id=a,symbol='ARENA10',side='buy',
                quantity=1,price=s.ref_prices['ARENA10'],order_type='ioc'),a)
            assert rx.errors['FUTURES_LOCKED']==1
    asyncio.run(run())


def test_stale_post_only_quote_rejects_instead_of_optimistic_fill():
    async def run():
        with isolated(83,5,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
            a=accounts[0];s.portfolios[a]=Portfolio(team_id=a,role='broker',level=1,cash=175000)
            rx=Receiver(a);rx.current=s;s.receivers[a]=rx
            g=Gateway(s,a,rx,175000)
            g.schedule([Intent('NFLX','buy',2,100,'post_only')],obs(1))
            s.books['NFLX'].place_order('liquidity','sell',99.9,20)
            s.tick=2;await g.execute_due(2)
            assert rx.errors['POST_ONLY_CROSS']==1
            assert not rx.fills and not rx.pending
    asyncio.run(run())


def test_fill_before_cancel_confirmation_keeps_correct_reservation():
    async def run():
        with isolated(84,5,175000,1) as (_,scenario,accounts):
            s=OfflineExchange();s.set_scenario(scenario);s.session_open=True;s.ref_prices['NFLX']=100
            a=accounts[0];rx=Receiver(a);rx.current=s;s.receivers[a]=rx;s.clients[a]=a
            for name,role in [(a,'broker'),('flow','trader')]:
                s.portfolios[name]=Portfolio(team_id=name,role=role,level=1,cash=175000)
            s.portfolios['flow'].positions['NFLX']=10
            s.price_engines['NFLX'].fair_value=s.price_engines['NFLX'].market_price=s.price_engines['NFLX'].session_open=100
            g=Gateway(s,a,rx,175000,cap=10)
            g.schedule([Intent('NFLX','buy',10,100,'post_only')],obs(1))
            s.tick=2;await g.execute_due(2)
            g.schedule([Intent('NFLX','buy',0,0,cancel=True)],obs(2))
            await s._handle_place_order('flow',PlaceOrder(team_id='flow',symbol='NFLX',side='sell',
                price=100,quantity=3,order_type='ioc'),'flow')
            assert rx.pending['NFLX','buy'].quantity==7
            assert s.portfolios[a].positions['NFLX']==3
            g.sample_risk();assert not g.position_violations
            s.tick=3;await g.execute_due(3)
            assert not rx.pending
            assert g.allowed_quantity(Intent('NFLX','buy',20,100))==7
    asyncio.run(run())


def test_report_pairs_each_strategy_with_same_seed_benchmark():
    from scripts.alpha_report import confidence
    assert confidence([2.,2.,2.])==[2.,2.]


def test_historical_long_only_liquidity_has_funded_offers(monkeypatch):
    from scripts.alpha_history import run_repaired
    import alpha_lab.history as hist
    from plugins.securities.futures import INDEX_MEMBERS
    tape=[]
    for t in range(1,9):
        books={sym:{'type':'book_snapshot','symbol':sym,'ref_price':100+t,
            'bids':[[100+t-.05,30]],'asks':[[100+t+.05,30]]} for sym in INDEX_MEMBERS}
        events=[{'type':'session_event','event':'CALENDAR_EVENT','message':'public',
            'data':{'kind':'earnings','affected':['NFLX']}}] if t==1 else []
        tape.append((t,{'books':books,'events':events}))
    monkeypatch.setattr(hist,'frames',lambda _:tape)
    r=asyncio.run(run_repaired('session_week02_fixture.jsonl','event_momentum',{'confirmation':3,'holding':20}))
    assert r['fills']>0
    assert r['background_initial_inventory']==200 and not r['same_tick_fills']


def test_historical_orders_wait_for_recorded_session_open(monkeypatch):
    from scripts.alpha_history import run_repaired
    import alpha_lab.history as hist
    from plugins.securities.futures import INDEX_MEMBERS
    tape=[]
    for t in range(1,8):
        books={sym:{'type':'book_snapshot','symbol':sym,'ref_price':100,
            'bids':[[99.95,30]],'asks':[[100.05,30]]} for sym in INDEX_MEMBERS}
        events=[{'type':'session_event','event':'SESSION_OPEN','message':'open','data':{}}] if t==4 else []
        tape.append((t,{'books':books,'events':events}))
    monkeypatch.setattr(hist,'frames',lambda _:tape)
    r=asyncio.run(run_repaired('session_week02_fixture.jsonl','buy_hold',{}))
    assert r['recorded_session_phases_enforced']
    assert r['first_fill_tick']>=5
