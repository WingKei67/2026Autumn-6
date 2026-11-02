"""Rolling residual trade against the tradable ARENA10 future; never a fictitious index fill."""
import math
from statistics import mean, pstdev
from .core import Strategy


class BasketArbitrage(Strategy):
    def __init__(self,z=2.5,rolling_beta=True,hedge=True,lookback=90):
        super().__init__(z=z,rolling_beta=rolling_beta,hedge=hedge,lookback=lookback)
        self.z=z; self.rolling_beta=rolling_beta; self.hedge=hedge; self.lookback=lookback
        self.active=None

    def decide(self,obs):
        if obs.tick%5: return []
        if not obs.futures_enabled or not self.eligible(obs,'ARENA10'): return []
        if self.active:
            sym,base,hedgebase,tick,group=self.active
            if obs.tick-tick>=40:
                orders=self.cross(obs,sym,base,'basket_exit',group+':exit')+self.cross(obs,'ARENA10',hedgebase,'hedge_exit',group+':exit')
                if obs.positions.get(sym,0)==base and obs.positions.get('ARENA10',0)==hedgebase: self.active=None
                return orders
            return []
        index=list(self.history['ARENA10'])[-self.lookback:]
        if len(index)<self.lookback: return []
        x=[math.log(p) for _,p in index]
        dx=[b-a for a,b in zip(x,x[1:])]; variance=pstdev(dx)**2
        if variance<1e-12: return []
        for sym,q in obs.quotes.items():
            if sym=='ARENA10' or not self.eligible(obs,sym): continue
            news=self.news.get(sym)
            if news and news[1]=='earnings' and obs.tick-news[0]<150: continue
            prices=list(self.history[sym])[-self.lookback:]
            if len(prices)!=len(index) or [t for t,_ in prices]!=[t for t,_ in index]: continue
            y=[math.log(p) for _,p in prices]; dy=[b-a for a,b in zip(y,y[1:])]
            mx,my=mean(dx),mean(dy)
            beta=sum((a-mx)*(b-my) for a,b in zip(dx,dy))/len(dx)/variance if self.rolling_beta else 1.
            beta=max(.2,min(3.,beta))
            residuals=[b-beta*a for a,b in zip(x,y)]
            scale=max(pstdev(residuals),.001)
            residual=math.log(q.mid)-beta*math.log(obs.quotes['ARENA10'].mid)
            displacement=residual-mean(residuals); z=displacement/scale
            costs=(2*obs.taker_fee+q.spread)*(1+beta)+.004
            if abs(z)<self.z or abs(displacement)<=costs: continue
            direction=1 if z<0 else -1; base=obs.positions.get(sym,0)
            qty=3 if direction>0 else min(3,base)
            if not qty: continue
            hedge_qty=max(1,round(beta*qty*q.mid/obs.quotes['ARENA10'].mid))
            hedgebase=obs.positions.get('ARENA10',0); group=f'basket:{sym}:{obs.tick}'
            self.active=(sym,base,hedgebase,obs.tick,group)
            orders=self.cross(obs,sym,base+direction*qty,'basket_entry',group)
            if self.hedge: orders+=self.cross(obs,'ARENA10',hedgebase-direction*hedge_qty,'hedge_entry',group)
            return orders
        return []
