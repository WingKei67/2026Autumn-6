"""Independent conservative gateway; no changes to the Week 5 live controls.

Reservations precede transport, survive cancellation latency and are only
released after the isolated exchange has processed the request. Stock shorts
are forbidden. Futures are separately capped and fully counted in gross.
"""
import math
from collections import defaultdict
from shared.messages import PlaceOrder, CancelOrder
from team.alpha.core import Quote
from .exchange import Reservation


class Gateway:
    def __init__(self,server,account,receiver,starting_cash,cap=150,latency=1):
        self.server=server; self.account=account; self.rx=receiver
        self.initial=starting_cash; self.cap=min(150,cap); self.latency=max(1,latency)
        self.queue=[]; self.sequence=0; self.blocked=defaultdict(int)
        self.group_orders={}; self.group_first_requested={}; self.hedge_failures=0
        self.peak_position=0; self.peak_gross=0.; self.position_violations=0

    def quote(self,sym):
        snap=self.server.books[sym].get_snapshot()
        if not snap['bids'] or not snap['asks']: return None
        b,a=snap['bids'][0],snap['asks'][0]
        if b[0]>=a[0]: return None
        return Quote(b[0],a[0],int(b[1]),int(a[1]))

    def allowed_quantity(self,intent,exclude=None):
        s=self.server; p=s.portfolios[self.account]; sym=intent.symbol
        if not math.isfinite(intent.price) or intent.price<=0: return 0
        if (p.liquidated or not s.session_open or s.circuit_breaker.is_halted(sym)):
            self.blocked['session_or_halt']+=1; return 0
        future=s.scenario.flag('futures_enabled') and sym=='ARENA10'
        if sym=='ARENA10' and not future: return 0
        ref=s.ref_prices.get(sym,0)
        if not ref or not .93*ref <= intent.price <= 1.07*ref:
            self.blocked['price_band']+=1; return 0
        entries=[e for e in self.rx.pending.values() if e is not exclude]
        buy=sum(e.quantity for e in entries if e.symbol==sym and e.side=='buy')
        sell=sum(e.quantity for e in entries if e.symbol==sym and e.side=='sell')
        pos=p.positions.get(sym,0); cap=min(self.cap,20) if future else self.cap
        room=cap-pos-buy if intent.side=='buy' else (cap+pos-sell if future else pos-sell)
        qty=min(intent.quantity,max(0,room))
        reducing=(intent.side=='sell' and pos>0) or (intent.side=='buy' and pos<0)
        equity=p.net_worth(s.ref_prices,s._bidask_marks())
        if not reducing and equity<.7*self.initial: return 0
        marks=s.ref_prices
        if any(not marks.get(x) for x,q in p.positions.items() if q): return 0
        for _ in range(qty+1):
            if qty<=0: return 0
            remaining=entries+[Reservation(sym,intent.side,qty,intent.price,intent.order_type)]
            gross=0.; buys=0.; future_margin=0.
            for x in set(p.positions)|{e.symbol for e in remaining}:
                eb=sum(e.quantity for e in remaining if e.symbol==x and e.side=='buy')
                es=sum(e.quantity for e in remaining if e.symbol==x and e.side=='sell')
                worst=max(abs(p.positions.get(x,0)+eb),abs(p.positions.get(x,0)-es))
                px=max([marks.get(x,0)]+[e.price for e in remaining if e.symbol==x])
                gross+=worst*px
                if x=='ARENA10':
                    from exchange import config
                    future_margin+=worst*config.FUTURES_MARGIN_PER_CONTRACT
            from exchange import config
            for e in remaining:
                buys+=e.quantity*e.price*((1+config.TAKER_FEE_RATE) if e.symbol!='ARENA10' and e.side=='buy'
                                          else config.TAKER_FEE_RATE if e.symbol=='ARENA10' else 0)
            # Reducing orders can always release risk, even above an entry cap.
            if reducing or (gross<=min(.4*self.initial,70000.) and p.cash-buys-future_margin>=.6*self.initial):
                return qty
            qty-=1
        return 0

    def schedule(self,intents,obs):
        for intent in intents:
            sym=intent.symbol
            if sym not in self.server.books: continue
            if intent.cancel:
                for key,e in list(self.rx.pending.items()):
                    if e.symbol==sym and e.oid and not any(x[2]=='cancel' and x[3] is e for x in self.queue):
                        self.sequence+=1
                        self.queue.append((obs.tick+self.latency,self.sequence,'cancel',e))
                continue
            if sym not in obs.quotes:
                self.blocked['missing_quote']+=1; continue
            key=(sym,intent.side)
            if key in self.rx.pending: continue
            qty=self.allowed_quantity(intent)
            if not qty:
                self.blocked['risk_budget']+=1
                if intent.group and intent.group in self.group_orders:self.hedge_failures+=1
                continue
            delay=self.latency
            if intent.group:
                if intent.group not in self.group_orders:
                    self.group_orders[intent.group]=sym
                    self.group_first_requested[intent.group]=intent.quantity
                elif self.group_orders[intent.group]!=sym: delay+=1
            e=Reservation(sym,intent.side,qty,intent.price,intent.order_type,group=intent.group,
                decision_tick=obs.tick,decision_mid=obs.quotes[sym].mid,due=obs.tick+delay)
            self.rx.pending[key]=e
            self.sequence+=1; self.queue.append((e.due,self.sequence,'place',e))

    async def execute_due(self,tick):
        due=sorted(x for x in self.queue if x[0]<=tick)
        self.queue=[x for x in self.queue if x[0]>tick]
        for _,_,kind,e in due:
            key=(e.symbol,e.side)
            if self.rx.pending.get(key) is not e: continue
            if kind=='cancel':
                await self.server._handle_cancel_order(self.account,CancelOrder(team_id=self.account,
                    symbol=e.symbol,order_id=e.oid),self.account)
                # Serialized isolated engine, no concurrent handler. On a live
                # venue an unacknowledged cancel is NOT equivalent to this check.
                if e.oid not in self.server.books[e.symbol]._orders: self.rx.pending.pop(key,None)
                continue
            from team.alpha.core import Intent
            if e.group and self.group_orders[e.group]!=e.symbol:
                first=self.group_orders[e.group]
                filled=self.rx.group_fills.get((e.group,first),0)
                if not filled:
                    self.rx.pending.pop(key,None); self.hedge_failures+=1; continue
                # Hedge only the actually executed fraction of the stock leg.
                proportion=min(1.,filled/max(1,self.group_first_requested[e.group]))
                e.quantity=max(1,round(e.quantity*proportion))
            requested=Intent(e.symbol,e.side,e.quantity,e.price,e.kind)
            qty=self.allowed_quantity(requested,exclude=e)
            if not qty:
                self.rx.pending.pop(key,None)
                if e.group: self.hedge_failures+=1
                continue
            e.quantity=qty
            if self.server.circuit_breaker.is_halted(e.symbol):
                raise AssertionError('gateway attempted an order during a halt')
            await self.server._handle_place_order(self.account,PlaceOrder(team_id=self.account,
                symbol=e.symbol,side=e.side,order_type=e.kind,price=e.price,quantity=qty),self.account)
            if e.kind=='ioc' or not e.oid or e.oid not in self.server.books[e.symbol]._orders:
                self.rx.pending.pop(key,None)
        self.sample_risk()

    def sample_risk(self):
        p=self.server.portfolios[self.account]
        self.peak_position=max(self.peak_position,max((abs(q) for q in p.positions.values()),default=0))
        self.peak_gross=max(self.peak_gross,sum(abs(q)*self.server.ref_prices.get(sym,0)
            for sym,q in p.positions.items()))
        for sym in set(p.positions)|{e.symbol for e in self.rx.pending.values()}:
            entries=[e for e in self.rx.pending.values() if e.symbol==sym]
            hi=p.positions.get(sym,0)+sum(e.quantity for e in entries if e.side=='buy')
            lo=p.positions.get(sym,0)-sum(e.quantity for e in entries if e.side=='sell')
            cap=min(20,self.cap) if sym=='ARENA10' else self.cap
            if max(abs(hi),abs(lo))>cap: self.position_violations+=1
