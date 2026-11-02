"""Secondary chronological historical taker replay through the real exchange.

This is NOT exact historical counterfactual simulation. Only aggregated books
are known. We reconstruct at most three levels, cap participation at 10% of
visible touch, and execute prior-tick decisions on a later observed book.
No maker fill estimates are published: historical queue position is unknown.
The main seeded experiments model queues independently using real order IDs.
"""
from collections import defaultdict
import asyncio
import json
from pathlib import Path

from exchange.server import Portfolio
from exchange.calendar import MarketCalendar
from shared.messages import PlaceOrder,CancelOrder,SessionEvent
from team.alpha import STRATEGIES
from team.alpha.core import Observation,Intent,Strategy
from .runner import isolated,public_events,default_cash
from .exchange import OfflineExchange,Receiver
from .risk import Gateway
from .baselines import NoTrade, BuyHold, Original
BASELINES={"no_trade":NoTrade,"buy_hold":BuyHold}
from .metrics import summarize
from .data import rows,recording_paths


def frames(path):
    buckets=defaultdict(lambda:{'books':{},'events':[]})
    for row in rows(path):
        msg=row.get('msg',row);tick=row.get('tick')
        if not isinstance(msg,dict) or tick is None:continue
        if msg.get('type')=='book_snapshot':buckets[tick]['books'][msg['symbol']]=msg
        elif msg.get('type')=='session_event':buckets[tick]['events'].append(msg)
    return sorted(buckets.items())


async def run_history(path,name,params,cash=None):
    cash=default_cash() if cash is None else cash
    week=int(Path(path).name.split('week')[1][:2]);tape=frames(path)
    if not tape:raise ValueError('No historical book frames')
    with isolated(0,week,cash,1) as (tmp,scenario,accounts):
        scenario.events=[];s=OfflineExchange();s.set_scenario(scenario);s.calendar=MarketCalendar([])
        s.session_open=True;s.auction_phase=None
        a=accounts[0];s.clients[a]=a
        s.portfolios[a]=Portfolio(team_id=a,role='trader',level=1,cash=cash)
        for sym in s.books:
            provider='liquidity_'+sym
            s.portfolios[provider]=Portfolio(team_id=provider,role='broker',level=1,cash=1e9)
        rx=Receiver(a);rx.current=s;s.receivers[a]=rx
        g=Gateway(s,a,rx,cash,150,1)
        strategy=Original(a,tmp/'history.json',s) if name=='original' else BASELINES[name]() if name in BASELINES else STRATEGIES[name](**params)
        latest={};starting=None;curve=[];initial=None;first=tape[0][0]
        for absolute,data in tape:
            s.tick=absolute-first+1;s._refill_quotas()
            for sym,msg in data['books'].items():
                if sym not in s.books or sym=='ARENA10':continue
                latest[sym]=msg
                ref=float(msg.get('ref_price') or msg.get('mid_price') or s.ref_prices[sym])
                if ref<=0:continue
                s.ref_prices[sym]=ref
                if sym in s.price_engines:
                    engine=s.price_engines[sym];engine.fair_value=engine.market_price=ref;engine.impact_buffer=0
                account='liquidity_'+sym
                for oid,e in list(s.books[sym]._orders.items()):
                    if e.team_id==account:
                        await s._handle_cancel_order(account,CancelOrder(team_id=account,symbol=sym,order_id=oid),account)
                for side,key in [('buy','bids'),('sell','asks')]:
                    for price,quantity in msg.get(key,[])[:3]:
                        if price<=0:continue
                        # Submit displayed liquidity via the actual engine, not a fill shortcut.
                        qty=min(500,int(quantity))
                        if qty>0:await s._handle_place_order(account,PlaceOrder(team_id=account,symbol=sym,
                            side=side,quantity=qty,price=price,order_type='limit'),account)
            for msg in data['events']:
                event=SessionEvent.model_validate(msg);d=event.data or {}
                if event.event=='TRADING_HALT' and d.get('symbol'):s.circuit_breaker.halted.add(d['symbol'])
                elif event.event=='TRADING_RESUMED':s.circuit_breaker.halted.discard(d.get('symbol'))
                elif event.event=='MARKET_HALT':s.circuit_breaker.market_halted=True
                elif event.event=='MARKET_RESUMED':s.circuit_breaker.market_halted=False
                elif event.event=='DIVIDEND' and d.get('symbol'):
                    s.portfolios[a].cash+=s.portfolios[a].positions.get(d['symbol'],0)*float(d.get('amount_per_share',0))
                # Trading gates follow observed public events, not a simulated calendar.
                # Baseline original is evaluated as a research strategy here; its Week 5-only
                # gate is retained, so it is inactive on earlier-week historical tapes.
                event=event.model_copy(update={'data':{**d,'tick':s.tick}})
                await s._broadcast(event)
            if initial is None and len(latest)>=10:
                for sym in s.books:
                    if sym!='ARENA10':
                        s.portfolios[a].positions[sym]=6
                        s.portfolios[a].avg_cost[sym]=s.ref_prices[sym]
                starting=dict(s.portfolios[a].positions);marks=dict(s.ref_prices)
                initial=s.portfolios[a].net_worth(s.ref_prices,s._bidask_marks())
            if initial is None:continue
            await g.execute_due(s.tick)
            await s._apply_carry_and_maintenance()
            quotes={sym:q for sym in latest if sym in s.books and sym!='ARENA10' and (q:=g.quote(sym))}
            raw=rx.events;rx.events=[]
            p=s.portfolios[a]
            obs=Observation(s.tick,quotes,dict(p.positions),p.cash,p.net_worth(s.ref_prices,s._bidask_marks()),cash,
                frozenset(s.circuit_breaker.halted),s.circuit_breaker.market_halted,True,
                public_events(raw),.003,0.001,False,150)
            if isinstance(strategy,Original):intents=await strategy.observe_async(obs,raw,rx.pending)
            else:intents=strategy.observe(obs)
            clipped=[]
            for intent in intents:
                if intent.symbol not in quotes:continue
                q=quotes[intent.symbol]
                qty=min(intent.quantity,max(1,int(.1*(q.ask_size if intent.side=='buy' else q.bid_size))))
                clipped.append(Intent(intent.symbol,intent.side,qty,intent.price,intent.order_type,intent.cancel,intent.group,intent.reason))
            g.schedule(clipped,obs);g.sample_risk()
            curve.append(p.net_worth(s.ref_prices,s._bidask_marks()))
        if initial is None:raise ValueError('Insufficient historical universe')
        result=summarize(s,[a],[rx],[g],curve,initial,starting,marks)
        result.update({'file':str(path),'strategy':name,'params':params,'week':week,'ticks':len(curve),
            'kind':'secondary_historical_taker_replay','participation_cap':.1,'queue_estimated':False,
            'future_enabled':False,'limitations':'Aggregated future book reconstruction, 3 levels, no maker fills, no endogenous counterfactual participant response; recorded prints are not reused as extra liquidity.'})
        return result


def study(root,chosen,cash=None):
    result=[]
    for path in recording_paths(Path(root)/'sessions'):
        for name in ('event_momentum','post_halt','basket','buy_hold','no_trade'):
            params=chosen[name]['params'] if name in chosen else {}
            row=asyncio.run(run_history(path,name,params,cash))
            result.append(row)
            print(f'Historical replay {Path(path).name}: {name}, pnl={row["pnl"]:.2f}, fills={row["fills"]}',flush=True)
    return result
