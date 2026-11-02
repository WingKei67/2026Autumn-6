"""One isolated strategy experiment using real local exchange handlers.

The exogenous population is a deliberately simple, seeded liquidity/flow model,
not a claim to reproduce hosted participants. Each candidate gets its own
exchange; candidates never trade against each other or see private future state.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
import json
import logging
import math
import os
from pathlib import Path
import random
import tempfile
from unittest.mock import patch

import exchange.config as cfg
import exchange.persistence as persistence
from exchange.scenario import load_week
from exchange.server import Portfolio
from plugins.securities import defaults
from shared.messages import PlaceOrder, CancelOrder, SessionEvent, ErrorMsg
from team.config import CAPITAL
from team.alpha import STRATEGIES
from team.alpha.core import Observation, PublicEvent, Intent
from .exchange import OfflineExchange, Receiver
from .risk import Gateway
from .baselines import NoTrade, BuyHold, Original
from .metrics import summarize

UNIVERSE=('AAPL','NVDA','NFLX','AMD')
BASELINES={'no_trade':NoTrade,'buy_hold':BuyHold}
_RAW_INNOVATION=defaults.fundamental_innovation

@lru_cache(maxsize=40000)
def cached_innovation(symbol,tick,sigma,mu,seed,vol):
    return _RAW_INNOVATION(symbol,tick,sigma,mu)

def memo_innovation(symbol,tick,sigma,mu=0.):
    return cached_innovation(symbol,tick,sigma,mu,defaults.FUNDAMENTAL_SEED,defaults.FUNDAMENTAL_VOL)


def default_cash():return float(CAPITAL.get('6_rings_trader_1',cfg.INITIAL_CASH))


@contextmanager
def isolated(seed,week,cash,n,fee=.003,stress='base'):
    saved={k:deepcopy(v) for k,v in vars(cfg).items() if k.isupper() and isinstance(v,(str,int,float,bool,dict,set,tuple,list))}
    saved_cache=deepcopy(cfg._roster_cache); saved_persist=persistence.SEASON_PATH
    saved_fund=(defaults.FUNDAMENTAL_SEED,defaults.FUNDAMENTAL_VOL)
    saved_innovation=defaults.fundamental_innovation
    defaults.fundamental_innovation=memo_innovation
    saved_rng=random.getstate(); saved_env=dict(os.environ)
    log_level=logging.root.manager.disable
    import trader.config as tc
    saved_team=tc.TEAM_ID
    import trader.trader as trader_engine
    saved_quiet=trader_engine._console.quiet
    trader_engine._console.quiet=True
    with tempfile.TemporaryDirectory(prefix='algoarena_alpha_') as tmp:
        try:
            os.environ.pop('GAME_WEEK',None);os.environ.pop('SCENARIO_PATH',None)
            os.environ['REPORTS_DIR']=tmp
            cfg.RECORD_SESSIONS=False;cfg.SEASON_PERSIST='false'
            cfg.LATENCY_MS_DEFAULT=cfg.LATENCY_MS_COLOCATED=0.
            cfg.TAKER_FEE_RATE=fee;cfg._TAKER_FEE_FROM_ENV=True;cfg._MAKER_REBATE_FROM_ENV=True
            cfg.QUEUE_FEEDBACK_ENABLED=False;cfg.LATENCY_STATS_ENABLED=False
            cfg.ROSTER_PATH=str(Path(tmp)/'roster.json')
            accounts=[f'alpha_{i}' for i in range(n)]
            # Same per-team grant fraction as the configured three live seats.
            # Split 6 endowed shares across combined sleeves instead of multiplying it.
            all_seats=accounts+[f'inactive_{i}' for i in range(3*n-n)]
            roster={'research':{'traders':all_seats,'capital':{a:cash/n for a in accounts}},
                    'liquidity':{'brokers':['liquidity'],'capital':{'liquidity':1e9}},
                    'flow':{'traders':['flow'],'capital':{'flow':1e9}}}
            for symbol in UNIVERSE+('ARENA10',):
                name='liquidity_'+symbol
                roster[name]={'brokers':[name],'capital':{name:1e9}}
            Path(cfg.ROSTER_PATH).write_text(json.dumps(roster))
            cfg._roster_cache={'key':None,'data':{}}
            persistence.SEASON_PATH=str(Path(tmp)/'season.json')
            defaults.FUNDAMENTAL_SEED=f'alpha:{seed}'
            defaults.FUNDAMENTAL_VOL=(.5,1.,2.,4.)[seed%4]*(2 if stress=='volatile' else 1)
            random.seed(seed)
            scenario=load_week(week);scenario.apply()
            if stress=='volatile':
                for event in scenario.events:
                    if 'magnitude_range' in event:event['magnitude_range']=[x*1.5 for x in event['magnitude_range']]
            logging.disable(logging.CRITICAL)
            yield Path(tmp),scenario,accounts
        finally:
            for key,value in saved.items():setattr(cfg,key,value)
            cfg._roster_cache=saved_cache;persistence.SEASON_PATH=saved_persist
            defaults.FUNDAMENTAL_SEED,defaults.FUNDAMENTAL_VOL=saved_fund
            defaults.fundamental_innovation=saved_innovation
            random.setstate(saved_rng);tc.TEAM_ID=saved_team
            os.environ.clear();os.environ.update(saved_env)
            trader_engine._console.quiet=saved_quiet
            logging.disable(log_level)


class MarketPopulation:
    def __init__(self,server,seed,stress='base'):
        self.server=server;self.rng=random.Random(seed+77191)
        self.spread=(.0004,.0012,.003)[seed%3]*(2 if stress=='volatile' else 1)
        self.ttl=(2,3,5)[seed%3];self.depth=(12,25,45)[seed%3]
        self.informed=(.1,.35,.65)[seed%3]
        self.ids={};self.last={};self.refs={}
        self.symbols=UNIVERSE+(('ARENA10',) if server.scenario.flag('futures_enabled') else ())

    async def refresh(self,force=False):
        s=self.server
        for sym in self.symbols:
            if not s.session_open and s.auction_phase!='preopen':continue
            if s.circuit_breaker.is_halted(sym):continue
            if not force and s.tick-self.last.get(sym,-999)<self.ttl:continue
            # Actual cancel/order requests, not a synthetic touch fill rule.
            account='liquidity_'+sym
            for oid in self.ids.get(sym,[]):
                await s._handle_cancel_order(account,CancelOrder(team_id=account,symbol=sym,order_id=oid),account)
            before=set(s.books[sym]._orders)
            ref=s.ref_prices[sym]
            for level in range(2):
                width=self.spread/2*(1+level)
                for side,price in [('buy',ref*(1-width)),('sell',ref*(1+width))]:
                    qty=self.depth*(level+1)
                    await s._handle_place_order(account,PlaceOrder(team_id=account,symbol=sym,
                        side=side,quantity=qty,price=round(price,2),order_type='post_only'),account)
            self.ids[sym]=[oid for oid,e in s.books[sym]._orders.items() if oid not in before and e.team_id==account]
            self.last[sym]=s.tick

    async def flow(self):
        s=self.server
        for sym in self.symbols:
            if not s.session_open or s.circuit_breaker.is_halted(sym):continue
            ref=s.ref_prices[sym];previous=self.refs.get(sym,ref);self.refs[sym]=ref
            if self.rng.random()>.55:continue
            change=ref/previous-1
            probability=.5+self.informed*max(-.45,min(.45,change*80))
            side='buy' if self.rng.random()<probability else 'sell'
            snap=s.books[sym].get_snapshot();levels=snap['asks' if side=='buy' else 'bids']
            if not levels:continue
            price=levels[0][0]*(1.001 if side=='buy' else .999)
            await s._handle_place_order('flow',PlaceOrder(team_id='flow',symbol=sym,side=side,
                quantity=self.rng.randint(1,18),price=round(price,2),order_type='ioc'),'flow')


def public_events(raw):
    result=[]
    for e in raw:
        d=e.data or {};affected=d.get('affected') or ([d['symbol']] if d.get('symbol') else [])
        result.append(PublicEvent(e.event,int(d.get('tick',0)),tuple(affected),str(d.get('kind',''))))
    return tuple(result)


async def run_one(seed,components,ticks=1500,week=5,cash=None,stress='base',keep_curve=False):
    cash=default_cash() if cash is None else cash
    n=len(components);fee=.006 if stress=='fees_60bps' else .003
    with isolated(seed,week,cash,n,fee,stress) as (tmp,scenario,accounts):
        virtual=[1700000000.]
        with patch('time.time',lambda:virtual[0]):
            s=OfflineExchange();s.set_scenario(scenario)
            s.registry.reset_tick()
            for sym,entry in s.registry.securities.items():
                base=entry['defn'].base_price;entry['current_price']=base;s.registry.prices[sym]=base
                s.ref_prices[sym]=base
                if sym in s.price_engines:
                    engine=s.price_engines[sym]
                    engine.fair_value=engine.market_price=engine.session_open=base
                    engine.impact_buffer=0.;engine.price_history.clear()
            background_errors={}
            def background_receive(msg):
                if isinstance(msg,ErrorMsg):background_errors[msg.code]=background_errors.get(msg.code,0)+1
            for name,role in [('liquidity','broker'),('flow','trader')]+[('liquidity_'+sym,'broker') for sym in UNIVERSE+('ARENA10',)]:
                s.portfolios[name]=Portfolio(team_id=name,role=role,level=1,cash=1e9)
                s.clients[name]=name
                s.receivers[name]=background_receive
            receivers=[];gateways=[];strategies=[]
            for account,(name,params) in zip(accounts,components):
                role='broker' if name=='market_making' else 'trader'
                s.portfolios[account]=Portfolio(team_id=account,role=role,level=1,cash=cash/n)
                s.clients[account]=account
                rx=Receiver(account);rx.current=s
                cap=max(1,(20 if name=='market_making' else 150)//n)
                gateway=Gateway(s,account,rx,cash/n,cap,latency=3 if stress=='latency_3ticks' else 1)
                strategy=(Original(account,tmp/(account+'.json'),s) if name=='original' else
                    BASELINES[name]() if name in BASELINES else STRATEGIES[name](**params))
                if name=='original':
                    base_receiver=rx
                    def receive(msg,receiver=base_receiver,original=strategy):
                        from shared.messages import TradeExecution
                        receiver(msg)
                        if isinstance(msg,TradeExecution):original.on_fill(msg)
                    s.receivers[account]=receive
                else:s.receivers[account]=rx
                receivers.append(rx);gateways.append(gateway);strategies.append(strategy)
            population=MarketPopulation(s,seed,stress)
            await s.open_session()
            # Real opening auction and grants, but strategies deliberately wait for continuous trading.
            while s.auction_phase=='preopen':
                await population.refresh(force=True)
                virtual[0]+=1;await s.advance_tick()
            initial=sum(s.portfolios[a].net_worth(s.ref_prices,s._bidask_marks()) for a in accounts)
            starting_positions={sym:sum(s.portfolios[a].positions.get(sym,0) for a in accounts) for sym in s.books}
            starting_marks=dict(s.ref_prices)
            starting_per_account={a:dict(s.portfolios[a].positions) for a in accounts}
            curve=[];max_team=0;team_violations=0;halts=0;resumes=0
            for step in range(ticks):
                virtual[0]+=1
                await s.advance_tick();await s.resume_due()
                await population.refresh()
                for gateway in gateways:await gateway.execute_due(s.tick)
                await population.flow()
                s._score_markouts(virtual[0])
                quotes={sym:q for sym in population.symbols if (q:=gateways[0].quote(sym))}
                halted=frozenset(s.circuit_breaker.halted)
                for account,strategy,rx,gateway in zip(accounts,strategies,receivers,gateways):
                    p=s.portfolios[account]
                    raw=rx.events;rx.events=[]
                    events=public_events(raw)
                    # Events that omit their tick are stamped at actual observation.
                    events=tuple(PublicEvent(e.event,e.tick or s.tick,e.symbols,e.kind) for e in events)
                    halts+=sum(e.event=='TRADING_HALT' for e in events)
                    resumes+=sum(e.event=='TRADING_RESUMED' for e in events)
                    obs=Observation(s.tick,quotes,dict(p.positions),p.cash,
                        p.net_worth(s.ref_prices,s._bidask_marks()),cash/n,halted,
                        s.circuit_breaker.market_halted,s.session_open,events,cfg.TAKER_FEE_RATE,
                        cfg.MAKER_REBATE_RATE,scenario.flag('futures_enabled'),gateway.cap)
                    if step>=ticks-12 and components[accounts.index(account)][0] not in BASELINES:
                        intents=[]
                        for sym in population.symbols:
                            if sym in quotes and sym not in halted:
                                intents.append(Intent(sym,'buy',0,0,cancel=True))
                                intents+=StrategyCross(obs,sym,starting_per_account[account].get(sym,0))
                    elif isinstance(strategy,Original):
                        intents=await strategy.observe_async(obs,raw,rx.pending)
                    else:intents=strategy.observe(obs)
                    gateway.schedule(intents,obs)
                    gateway.sample_risk()
                for sym in s.books:
                    lo=hi=sum(s.portfolios[a].positions.get(sym,0) for a in accounts)
                    for rx in receivers:
                        for e in rx.pending.values():
                            if e.symbol==sym:
                                if e.side=='buy':hi+=e.quantity
                                else:lo-=e.quantity
                    worst=max(abs(lo),abs(hi));max_team=max(max_team,worst)
                    if worst>scenario.position_limit:team_violations+=1
                curve.append(sum(s.portfolios[a].net_worth(s.ref_prices,s._bidask_marks()) for a in accounts))
            result=summarize(s,accounts,receivers,gateways,curve,initial,starting_positions,starting_marks)
            result.update({'seed':seed,'week':week,'stress':stress,'ticks':ticks,
                'components':components,'cash':cash,'taker_fee':fee,'maker_rebate_rate':cfg.MAKER_REBATE_RATE,
                'background_errors':background_errors,'spread_bps':population.spread*10000,'quote_ttl':population.ttl,
                'volatility_multiplier':defaults.FUNDAMENTAL_VOL,
                'max_team_position':max_team,'team_position_violations':team_violations,
                'halts':halts//n,'resumes':resumes//n,
                'open_overlay_shares':sum(abs(s.portfolios[a].positions.get(sym,0)-starting_per_account[a].get(sym,0))
                    for a in accounts for sym in s.books),
                'availability':'disabled_by_week_gate' if all(name=='basket' for name,_ in components) and not scenario.flag('futures_enabled') else 'enabled'})
            if keep_curve:
                result['equity_curve']=curve;result['fill_details']=[f for r in receivers for f in r.fills]
            # No close_session persistence/report writes; any timer is virtual and holds no asyncio task.
            return result


def StrategyCross(obs,sym,target):
    from team.alpha.core import Strategy
    return Strategy.cross(obs,sym,target,'end_of_test_unwind')
