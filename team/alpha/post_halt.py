"""Fade confirmed transient dislocations; exclude recent permanent earnings news."""
from statistics import mean, pstdev
from .core import PositionStrategy


class PostHaltReversion(PositionStrategy):
    def __init__(self, z=3., reversal=True, earnings_filter=True):
        super().__init__(z=z,reversal=reversal,earnings_filter=earnings_filter)
        self.z=z; self.reversal=reversal; self.earnings_filter=earnings_filter
        self.used=set()

    def decide(self,obs):
        orders=self.manage(obs,40,2.)
        for sym,tick in self.resumed.items():
            if (sym,tick) in self.used or sym in self.plans or not self.eligible(obs,sym): continue
            if not 3 <= obs.tick-tick <= 25: continue
            news=self.news.get(sym)
            if self.earnings_filter and news and news[1]=='earnings' and obs.tick-news[0] < 650: continue
            # A strictly lagged pre-halt reference, never recomputed using the recovery.
            ref=[p for _,p in self.halt_reference.get(sym,[])]
            if len(ref)<30: continue
            center=mean(ref); scale=max(pstdev(ref),center*.001)
            p=obs.quotes[sym].mid; z=(p-center)/scale
            h=[v for _,v in list(self.history[sym])[-3:]]
            if len(h)<3: continue
            reversal=(p>h[-1]>h[-2] if z<0 else p<h[-1]<h[-2])
            if abs(z)<self.z or (self.reversal and not reversal): continue
            expected=abs(p-center)/p
            if expected <= 2*obs.taker_fee+obs.quotes[sym].spread+.003: continue
            self.used.add((sym,tick))
            orders += self.enter(obs,sym,1 if z<0 else -1,lot=5)
        return orders
