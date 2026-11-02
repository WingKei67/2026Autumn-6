"""A stronger original-policy comparator, without changing the original file."""
from alpha_lab.baselines import Original
class ActiveOriginal(Original):
    async def observe_async(self,obs,raw_events,pending):
        from shared.messages import Signal
        self.owner.now=lambda:float(obs.tick)
        self.owner.on_event('FEE_SCHEDULE','',{'taker':obs.taker_fee,'rebate':obs.maker_rebate})
        self.owner._refs.update(self.server.ref_prices)
        self.owner._venue_tick=obs.tick
        self.bot._session_open=obs.session_open
        self.owner._week5_active=True
        self.bot._on_portfolio(self.server.portfolios[self.bot.portfolio.team_id].to_message(self.server.ref_prices))
        if not pending:self.owner._inflight.clear()
        for e in raw_events:await self.bot._on_session_event(e)
        self.owner._week5_active=True
        for sym in self.server.books:self.bot.market.update_book(self.server._make_snapshot(sym,self.server.books[sym]))
        signal=self.owner.on_tick(self.bot.market,self.bot.portfolio);self.collected=[]
        if signal:await self.bot._place_order(signal)
        return self.collected
