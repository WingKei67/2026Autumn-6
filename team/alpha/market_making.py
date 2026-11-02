"""Post-only microprice quotes with volatility/toxicity protection and inventory skew."""
import math
from .core import Intent, Strategy


class AdaptiveMarketMaker(Strategy):
    def __init__(self, width=1.5, skew=1., adverse=True, micro=True, cooldown=45):
        super().__init__(width=width,skew=skew,adverse=adverse,micro=micro,cooldown=cooldown)
        self.width=width; self.skew=skew; self.adverse=adverse; self.micro=micro; self.cooldown=cooldown

    def decide(self,obs):
        orders=[]
        if obs.tick%4: return orders
        for sym,q in obs.quotes.items():
            if sym=='ARENA10': continue
            news=self.news.get(sym)
            vol=self.volatility(sym)
            blocked=(not self.eligible(obs,sym) or (self.adverse and
                (vol>.003 or (news and obs.tick-news[0]<self.cooldown))))
            if obs.tick%4: continue
            # Explicit cancel request; the executor waits for actual cancellation.
            orders.append(Intent(sym,'buy',0,0,cancel=True))
            if blocked: continue
            h=[p for _,p in list(self.history[sym])[-6:]]
            trend=(q.mid/h[0]-1) if len(h)>4 else 0.
            micro=q.microprice if self.micro else q.mid
            fair=.5*q.mid+.5*micro + q.mid*max(-.001,min(.001,trend*.2))
            fair += .1*q.imbalance*(q.ask-q.bid)
            half=max(.01,q.mid*.0005,self.width*(q.ask-q.bid)/2)
            if self.adverse: half=max(half,q.mid*(2*vol+abs(trend)*.3))
            pos=obs.positions.get(sym,0); cap=min(20,obs.cap)
            fair-=self.skew*(pos/max(1,cap)-.5)*half*1.5
            size=max(1,min(3,int(3/(1+1000*vol))))
            bid=math.floor(min(fair-half,q.ask-.01)*100)/100
            ask=math.ceil(max(fair+half,q.bid+.01)*100)/100
            if pos<cap: orders.append(Intent(sym,'buy',min(size,cap-pos),bid,'post_only'))
            if pos>0: orders.append(Intent(sym,'sell',min(size,pos),ask,'post_only'))
        return orders
