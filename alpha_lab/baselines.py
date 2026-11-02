"""The original MyTrader is executed, not replaced by a preset proxy."""
from team.alpha.core import Strategy


class NoTrade(Strategy): pass


class BuyHold(Strategy):
    def __init__(self): super().__init__(); self.targets={}
    def decide(self,obs):
        if not self.targets:
            syms=[s for s in obs.quotes if s!='ARENA10']
            if not syms:return []
            self.targets={s:min(obs.cap,int(.30*obs.initial_cash/len(syms)/obs.quotes[s].ask)) for s in syms}
        orders=[]
        for s,target in self.targets.items():
            if self.eligible(obs,s):orders+=self.cross(obs,s,target,'buy_and_hold')
        return orders


class Original:
    """Actual Week 5 strategy AND its final outbound risk method, no live run()."""
    def __init__(self,account,path,server):
        import trader.config as cfg
        from team.trader import MyTrader,_Bot
        from team.ledger import Ledger
        cfg.TEAM_ID=account
        self.owner=MyTrader(); self.bot=_Bot(self.owner)
        self.bot._state_path=path
        self.bot.ledger=Ledger()
        self.server=server; self.collected=[]
        async def capture(msg):
            from team.alpha.core import Intent
            from shared.messages import PlaceOrder
            if isinstance(msg,PlaceOrder):
                self.collected.append(Intent(msg.symbol,msg.side,msg.quantity,msg.price,msg.order_type))
        self.bot._send=capture

    async def observe_async(self,obs,raw_events,pending):
        self.owner.now=lambda: float(obs.tick)
        # Live handshakes advertise fees, including fee-stress experiments.
        self.owner.on_event("FEE_SCHEDULE","",{"taker":obs.taker_fee,"rebate":obs.maker_rebate})
        self.owner._refs.update(self.server.ref_prices) # refs are publicly broadcast
        self.bot._session_open=obs.session_open
        self.owner._week5_active=self.server.scenario.week==5
        self.bot._on_portfolio(self.server.portfolios[self.bot.portfolio.team_id].to_message(self.server.ref_prices))
        if not pending:self.owner._inflight.clear()
        for event in raw_events:
            await self.bot._on_session_event(event)
        for sym in self.server.books:
            self.bot.market.update_book(self.server._make_snapshot(sym,self.server.books[sym]))
        signal=self.owner.on_tick(self.bot.market,self.bot.portfolio)
        self.collected=[]
        if signal:await self.bot._place_order(signal)
        return self.collected

    def on_fill(self,msg): self.bot._on_trade(msg)
