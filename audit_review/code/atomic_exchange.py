"""Local audit fixture: serialize order mutation and defer outbound delivery.

Preserves original exchange validation, order types, matching and accounting.
The owner task may recursively activate stops. Outbound ACKs/fills keep their
original within-handler ordering but cannot suspend matching before settlement.
This is a research fixture, not a complete production event-loop patch: session
transitions, auction clearing and tick mutation still require coordinated venue
serialization, and real transports need per-connection ordered outbound queues.
"""
import asyncio
from alpha_lab.exchange import OfflineExchange

class BufferedSettlementExchange(OfflineExchange):
    def __init__(self):
        super().__init__();self._mutation_lock=asyncio.Lock();self._mutation_owner=None;self._outbox=None
    async def _deliver_send(self,ws,msg):
        await super()._send(ws,msg)
    async def _deliver_broadcast(self,msg,skip_ids=None):
        await super()._broadcast(msg,skip_ids)
    async def _send(self,ws,msg):
        if self._mutation_owner is asyncio.current_task():self._outbox.append(('send',ws,msg))
        else:await self._deliver_send(ws,msg)
    async def _broadcast(self,msg,skip_ids=None):
        if self._mutation_owner is asyncio.current_task():self._outbox.append(('broadcast',msg,skip_ids))
        else:await self._deliver_broadcast(msg,skip_ids)
    async def _handle_place_order(self,ws,msg,team_id):
        if self._mutation_owner is asyncio.current_task():
            return await super()._handle_place_order(ws,msg,team_id)
        async with self._mutation_lock:
            self._mutation_owner=asyncio.current_task();self._outbox=[]
            try:
                result=await super()._handle_place_order(ws,msg,team_id)
                events=self._outbox
            finally:
                self._mutation_owner=None;self._outbox=None
        for kind,a,b in events:
            if kind=='send':await self._deliver_send(a,b)
            else:await self._deliver_broadcast(a,b)
        return result
