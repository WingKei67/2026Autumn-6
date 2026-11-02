"""Three new model policies: event-state prediction, cross-asset prediction,
and a coordinated allocator of the validated predictors. No maker rebates.
"""
from pathlib import Path
import json,math
from collections import defaultdict
import numpy as np
from team.alpha import STRATEGIES as ROUND1
from team.alpha.core import Strategy,Intent
from round2.features import FeatureStore
from round2.models import predict,entry_mask,I
MODEL_PATH=Path(__file__).resolve().parent/'results/models_discovery.json'

class PredictivePolicy(Strategy):
    def __init__(self,family='event_regime',safety=.001,size_mode='adaptive',stop=.018,models=None,max_entries=None,uncertainty=True,**kwargs):
        super().__init__();self.family=family;self.safety=safety;self.size_mode=size_mode;self.stop=stop
        self.max_entries=max_entries;self.uncertainty=uncertainty
        self.models=models or json.loads(MODEL_PATH.read_text());self.features=FeatureStore();self.base=None;self.plans={};self.completed=set();self.account=None;self.metrics=defaultdict(int)
    def forecasts(self,v):
        names=['event_regime','cross_asset'] if self.family=='adaptive_allocator' else [self.family]
        selected=[]
        for name in names:
            m=self.models[name]
            if not m.get('supported_proxy',False) or not entry_mask(v,name):continue
            value=float(predict(v,m));uncertainty=m['train_rmse']/math.sqrt(40) if self.uncertainty else 0.
            selected.append((value-uncertainty,m['horizon'],name))
        if not selected:return None
        # The allocator uses consensus when both predict: no summing correlated alphas.
        if len(selected)>1:return (sum(x[0] for x in selected)/len(selected),max(x[1] for x in selected),'consensus')
        return selected[0]
    def on_fill(self,msg,account,tick=None):
        self.account=account
        sym=msg.symbol;p=self.plans.get(sym)
        if p is None:return
        if msg.buyer_id==account:
            q=msg.quantity;p['entry_notional']+=q*msg.price;p['entry_qty']+=q
            p['entry']=p['entry_notional']/p['entry_qty']
            if p['started'] is None:p['started']=tick if tick is not None else p['last_tick']+1
    def observe(self,obs):
        if self.base is None:self.base=dict(obs.positions)
        current=self.features.update(obs);intents=[];candidates=[]
        for sym,v in current.items():
            if sym=='ARENA10' or not self.eligible(obs,sym):continue
            q=obs.quotes[sym];pos=obs.positions.get(sym,0);base=self.base.get(sym,0);plan=self.plans.get(sym)
            forecast=self.forecasts(v)
            if plan:
                plan['last_tick']=obs.tick
                actual=pos-base
                plan['best']=max(plan['best'],q.bid)
                if plan['entry']:
                    net_loss=q.bid*(1-obs.taker_fee)/(plan['entry']*(1+obs.taker_fee))-1
                    if net_loss < -self.stop:plan['exit']=True;self.metrics['stops']+=1
                if plan['started'] is not None and obs.tick-plan['started']>=plan['holding']:plan['exit']=True
                # Holding compares incremental future exit value; entry fees are sunk.
                if not forecast and v[I['age']]>max(20,plan['holding']):plan['exit']=True
                elif forecast and forecast[0]+2*.003+q.spread<.001:plan['exit']=True
                if obs.tick-plan['created']>6 and actual==0:plan['exit']=True
                target=base if plan['exit'] else plan['target']
                if plan['exit'] and pos==base:
                    del self.plans[sym];continue
                target=max(0,target)
                delta=target-pos
                if delta:
                    side='buy' if delta>0 else 'sell'
                    budget=min(.015,max(.001,abs(v[I['r1']])*2,q.spread,abs(v[I['pct']])*1.3/15))
                    price=q.ask*(1+budget) if side=='buy' else q.bid*(1-budget)
                    intents.append(Intent(sym,side,min(60,abs(delta)),round(price,2),reason='model_exit' if plan['exit'] else 'model_entry'))
                continue
            if forecast is None:continue
            edge,horizon,source=forecast
            # Predictions are already after 30bp fees at both future touches.
            edge-=2*(obs.taker_fee-.003)
            key=(sym,self.features.news.get(sym,(-10000,))[0],source)
            if key in self.completed or edge<=self.safety:continue
            # Expected-value / volatility sizing, capped below the original's
            # 25%-of-equity per-name rule; gateway preserves global risk budgets.
            risk=max(.005,v[I['vol30']]*math.sqrt(horizon))
            notional=min(.20*obs.initial_cash,.025*obs.initial_cash*(edge-self.safety)/(.006+risk))
            quantity=8 if self.size_mode=='fixed8' else min(60,max(1,int(notional/q.ask)))
            quantity=min(quantity,max(0,obs.cap-base))
            if quantity:candidates.append((edge/max(risk,.005),sym,base+quantity,horizon,key,edge))
        for _,sym,target,horizon,key,edge in sorted(candidates,reverse=True)[:(self.max_entries or (3 if self.family=='adaptive_allocator' else 1))]:
            self.completed.add(key);q=obs.quotes[sym];v=current[sym]
            self.plans[sym]=dict(created=obs.tick,last_tick=obs.tick,started=None,holding=horizon,target=target,exit=False,entry=0.,entry_qty=0,entry_notional=0.,best=q.bid)
            budget=min(.015,max(.001,abs(v[I['r1']])*2,q.spread,abs(v[I['pct']])*1.3/15))
            intents.append(Intent(sym,'buy',min(60,max(0,target-obs.positions.get(sym,0))),round(q.ask*(1+budget),2),reason='validated_prediction'))
            self.metrics['entries']+=1
        return [i for i in intents if i.quantity>0]

class EventRegime(PredictivePolicy):
    def __init__(self,**kwargs):super().__init__(family='event_regime',**kwargs)
class CrossAsset(PredictivePolicy):
    def __init__(self,**kwargs):super().__init__(family='cross_asset',**kwargs)
class AdaptiveAllocator(PredictivePolicy):
    def __init__(self,**kwargs):super().__init__(family='adaptive_allocator',**kwargs)
class OvershootResearch(PredictivePolicy):
    def __init__(self,**kwargs):super().__init__(family='overshoot_reversal',**kwargs)
STRATEGIES={**ROUND1,'event_regime':EventRegime,'cross_asset':CrossAsset,'adaptive_allocator':AdaptiveAllocator,'overshoot_research':OvershootResearch}
