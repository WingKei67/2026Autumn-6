"""Real exchange, with deterministic transport/timers instead of wall-clock tasks.

Matching, portfolio accounting, risk checks, quotas, carry, futures settlement,
calendar, price impact and auctions are inherited unchanged. Only outbound
transport and asynchronous halt timers are replaced with a virtual tick clock.
"""
from dataclasses import dataclass
from collections import defaultdict
import math

from exchange.server import ExchangeServer
from shared.messages import SessionEvent, TradeExecution, OrderAck, ErrorMsg


class OfflineExchange(ExchangeServer):
    def __init__(self):
        super().__init__()
        self.receivers={}
        self.timers=[]
        self.wire_events=[]
        self.transcript=[]

    async def _send(self, websocket, message):
        if websocket in self.receivers: self.receivers[websocket](message)

    async def _broadcast(self, message, skip_ids=None):
        if isinstance(message,SessionEvent):
            self.wire_events.append(message)
            for name in self.receivers:
                self.receivers[name](message)

    async def _broadcast_ladder(self,*args,**kwargs): return
    async def _push_queue_updates(self,*args,**kwargs): return

    async def _halt_symbol(self,symbol,reason,duration=None):
        record=self.circuit_breaker.halt_symbol(symbol,reason,duration)
        self.timers.append((self.tick+math.ceil(record['duration']),'symbol',symbol))
        await self._broadcast(SessionEvent(event='TRADING_HALT',message=reason,
            data={'symbol':symbol,'duration':record['duration'],'resume_at':record['resume_at']}))

    async def _halt_all_symbols(self,reason,level):
        if level==3:
            await self.close_session(); return
        duration=self.circuit_breaker.HALT_DURATION_SEC*({1:3,2:12}.get(level,1))
        self.circuit_breaker.market_halted=True
        self.timers.append((self.tick+math.ceil(duration),'market',level))
        await self._broadcast(SessionEvent(event='MARKET_HALT',message=reason,
            data={'level':level,'duration':duration}))

    async def resume_due(self):
        due=[x for x in self.timers if x[0]<=self.tick]
        self.timers=[x for x in self.timers if x[0]>self.tick]
        for _,kind,value in due:
            if kind=='symbol' and self.circuit_breaker.resume_symbol(value):
                await self._broadcast(SessionEvent(event='TRADING_RESUMED',message='virtual-clock resume',data={'symbol':value}))
            elif kind=='market':
                self.circuit_breaker.market_halted=False
                await self._broadcast(SessionEvent(event='MARKET_RESUMED',message='virtual-clock resume',data={'level':value}))


@dataclass
class Reservation:
    symbol: str
    side: str
    quantity: int
    price: float
    kind: str
    oid: str = ''
    group: str = ''
    decision_tick: int = 0
    decision_mid: float = 0.
    due: int = 0


class Receiver:
    def __init__(self,account):
        self.account=account
        self.pending={}
        self.events=[]
        self.fills=[]
        self.errors=defaultdict(int)
        self.current=None
        self.positions_seen={}
        self.halt_orders=0
        self.group_fills=defaultdict(int)
        self.group_initial={}

    def __call__(self,msg):
        if isinstance(msg,SessionEvent): self.events.append(msg)
        elif isinstance(msg,OrderAck):
            entry=self.pending.get((msg.symbol,msg.side))
            if entry: entry.oid=msg.order_id
        elif isinstance(msg,ErrorMsg): self.errors[msg.code]+=1
        elif isinstance(msg,TradeExecution):
            if self.account not in (msg.buyer_id,msg.seller_id): return
            side='buy' if msg.buyer_id==self.account else 'sell'
            entry=self.pending.get((msg.symbol,side))
            qty=msg.quantity
            fee=msg.fee if msg.taker_id==self.account else 0.
            rebate=msg.maker_rebate if msg.maker_id==self.account else 0.
            if entry:
                entry.quantity=max(0,entry.quantity-qty)
                if entry.group: self.group_fills[(entry.group,entry.symbol)]+=qty
            self.fills.append({'tick':self.current.tick if self.current else 0,'symbol':msg.symbol,
                'side':side,'quantity':qty,'price':msg.price,'fee':fee,'rebate':rebate,
                'maker':msg.maker_id==self.account,
                'decision_tick':entry.decision_tick if entry else None,
                'decision_mid':entry.decision_mid if entry else None,
                'group':entry.group if entry else ''})
