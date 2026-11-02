"""Causal features. All forward prices are confined to research label construction."""
from dataclasses import dataclass,field
from collections import defaultdict,deque
import math
import numpy as np
from team.alpha.core import PublicEvent,Observation

SYMBOLS=('AAPL','MSFT','NVDA','TSLA','AMZN','GOOGL','META','NFLX','AMD','INTC','ARENA10')
FEATURES=['r1','r3','r10','r30','micro','imbalance','vol30','peer1','peer3','peer10','ann_remaining','peak_remaining','age','resume_age','lag_z','trade_flow','pct','spread']
@dataclass(frozen=True)
class FullEvent(PublicEvent):
    pct:float=0.
    ramp:int=15
    overshoot:float=1.3
@dataclass(frozen=True)
class FullObservation(Observation):
    trade_flow:dict=field(default_factory=dict)

class FeatureStore:
    def __init__(self):
        self.history=defaultdict(lambda:deque(maxlen=180));self.news={};self.resumed={};self.current={}
    def update(self,obs):
        for e in obs.events:
            if e.event=='CALENDAR_EVENT':
                for sym in e.symbols:
                    pre=self.history[sym][-1][1] if self.history[sym] else obs.quotes[sym].mid if sym in obs.quotes else 0.
                    self.news[sym]=(e.tick,pre,getattr(e,'pct',0.),getattr(e,'ramp',15),getattr(e,'overshoot',1.3),e.kind)
            elif e.event=='TRADING_RESUMED':
                for sym in e.symbols:self.resumed[sym]=obs.tick
        changes={}
        for sym,q in obs.quotes.items():
            h=self.history[sym]; past=dict(h)
            changes[sym]=[q.mid/past[obs.tick-k]-1 if obs.tick-k in past else 0. for k in [1,3,10,30]]
        result={}
        for sym,q in obs.quotes.items():
            if sym not in SYMBOLS:continue
            h=self.history[sym];prices=np.array([p for _,p in list(h)[-31:]],dtype=float)
            vol=float(np.std(np.diff(np.log(prices)))) if len(prices)>5 else .001
            peers=[v for s,v in changes.items() if s!=sym and s!='ARENA10']
            peer=np.mean(peers,axis=0) if peers else np.zeros(4)
            tick,pre,pct,ramp,ov,kind=self.news.get(sym,(-10000,0.,0.,15,1.3,''))
            age=obs.tick-tick
            ann=pre*(1+pct)/q.mid-1 if pre and age<180 else 0.
            peak=pre*(1+pct*ov)/q.mid-1 if pre and age<180 else 0.
            lag=list(h)[-65:-5];center=np.mean([p for _,p in lag]) if len(lag)>=20 else q.mid
            scale=max(q.mid*.001,float(np.std([p for _,p in lag]))) if lag else q.mid*.001
            v=changes[sym]+[(q.microprice/q.mid-1),q.imbalance,vol,float(peer[0]),float(peer[1]),float(peer[2]),ann,peak,min(age,10000),min(obs.tick-self.resumed.get(sym,-10000),10000),(q.mid-center)/scale,getattr(obs,'trade_flow',{}).get(sym,0.),pct if age<180 else 0.,q.spread]
            result[sym]=np.array(v,dtype=float)
        for sym,q in obs.quotes.items():self.history[sym].append((obs.tick,q.mid))
        self.current=result;return result

class Recorder:
    def __init__(self):self.store=FeatureStore();self.rows=[];self.events=[];self.fills_seen=0
    def __call__(self,obs,raw,account,server):
        if account!='alpha_0':return
        f=self.store.update(obs)
        for sym,v in f.items():
            q=obs.quotes[sym];self.rows.append([obs.tick,SYMBOLS.index(sym),q.mid,q.bid,q.ask,int(sym in obs.halted or obs.market_halted or not obs.session_open),*v])
        for e in raw:
            self.events.append(dict(event=e.event,data=e.data))

# Data columns: tick, symbol index, mid, bid, ask, ineligible, then FEATURES.
def labels(data,horizon):
    """Later-book touch screen, NOT a fill simulator. Entry tick t+1, exit t+h."""
    n=len(data);forward=np.full(n,np.nan);long=np.full(n,np.nan);short=np.full(n,np.nan)
    for sid in np.unique(data[:,1]):
        idx=np.flatnonzero(data[:,1]==sid);a=data[idx];ticks=a[:,0]
        entry=np.searchsorted(ticks,ticks+1);exit=np.searchsorted(ticks,ticks+horizon)
        valid=(entry<len(a))&(exit<len(a));ii=np.flatnonzero(valid)
        ii=ii[(ticks[entry[ii]]==ticks[ii]+1)&(ticks[exit[ii]]==ticks[ii]+horizon)]
        # Every point between entry and exit must be eligible: cannot assume
        # a liquidation at a stale halted quote or a continuously tradable path.
        bad=np.r_[0,np.cumsum(a[:,5]>0)]
        ii=ii[(bad[exit[ii]+1]-bad[entry[ii]]==0)&(a[ii,5]==0)]
        dest=idx[ii];forward[dest]=a[exit[ii],2]/a[entry[ii],2]-1
        long[dest]=(a[exit[ii],3]*(1-.003))/(a[entry[ii],4]*(1+.003))-1
        short[dest]=(a[entry[ii],3]*(1-.003)-a[exit[ii],4]*(1+.003))/a[entry[ii],2]
    return forward,long,short
