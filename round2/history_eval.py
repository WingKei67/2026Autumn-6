"""Funded chronological replay; all recordings were previously viewed in Round I."""
from round2.bootstrap import ROOT,audit
import asyncio,json,argparse,math
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from dataclasses import fields,replace
import numpy as np
from round2.models import FAMILIES,mask,fit,I
from round2.features import labels,FullObservation,FEATURES
from round2.strategies import EventRegime,CrossAsset,AdaptiveAllocator
from round2.baselines import ActiveOriginal
from round2.gateway import Gateway
from round2.runner import public_events
OUT=ROOT/'round2/results'

def walk_models():
    models=json.loads((OUT/'frozen_models.json').read_text());manifest=json.loads((OUT/'history_manifest.json').read_text())
    for week in [3,4]:
        past=[r for r in manifest if r['week']<week];new={}
        for family,template in models.items():
            xs=[];ys=[]
            for r in past:
                a=np.load(r['path'])['data'];_,y,_=labels(a,template['horizon']);v=a[:,6:];keep=mask(v,family)&np.isfinite(y)&(a[:,0]%3==0)&(a[:,1]<10)
                xs.append(v[keep]);ys.append(y[keep])
            x=np.concatenate(xs);y=np.concatenate(ys)
            if len(y)<50:new[family]={**template,'supported_proxy':False,'reason':'insufficient past-day eligible labels'};continue
            m=fit(x,y,family,template['horizon'],template['ridge'])
            m['train_rmse']*=math.sqrt(40/len(past)) # policy's sqrt(40) becomes sqrt(past days)
            m.update(supported_proxy=template['supported_proxy'],train_days=[r['week'] for r in past],support_note='family support inherited from synthetic validation; not a historical significance claim')
            new[family]=m
        (OUT/f'walk_models_w{week}.json').write_text(json.dumps(new,indent=2))

def job(payload):
    item,name,mode=payload
    from scripts.alpha_history import run_repaired
    import alpha_lab.history as h
    from shared.messages import TradeExecution
    from alpha_lab.exchange import OfflineExchange as ActualExchange
    h.Gateway=Gateway;h.public_events=public_events;h.summarize=audit.summarize
    original=h.OfflineExchange;current=[]
    class TrackedExchange(original):
        def __init__(self):
            super().__init__();current[:]=[self]
            # A legal earlier decision can arrive after SESSION_CLOSED.
            # Preserve the real exchange rejection rather than the V3 fixture's
            # assertion that conflates decision time with arrival time.
            async def arrive(ws,msg,team_id):
                return await ActualExchange._handle_place_order(self,ws,msg,team_id)
            self._handle_place_order=arrive
    h.OfflineExchange=TrackedExchange
    class HistoricalOriginal(ActiveOriginal):
        def __init__(self,*args):
            super().__init__(*args);s=self.server;old=s.receivers[args[0]]
            def receive(msg):
                old(msg)
                if isinstance(msg,TradeExecution):self.on_fill(msg)
            s.receivers[args[0]]=receive
    async def original_observe(self,obs,raw,pending):
        obs=replace(obs,session_open=self.server.session_open and self.server.auction_phase is None)
        return await ActiveOriginal.observe_async(self,obs,raw,pending)
    HistoricalOriginal.observe_async=original_observe
    h.Original=HistoricalOriginal
    modelpath=OUT/(f'walk_models_w{item["week"]}.json' if mode=='walk_forward' else 'frozen_models.json')
    models=json.loads(modelpath.read_text());params=json.loads((OUT/'frozen_protocol.json').read_text())['parameters']
    a=np.load(item['path'])['data'];first=int(a[:,0].min());flow={}
    for r in a:flow.setdefault(int(r[0])-first+1,{})[__import__('round2.features',fromlist=['SYMBOLS']).SYMBOLS[int(r[1])]]=float(r[6+I['trade_flow']])
    for key,cls in [('event_regime',EventRegime),('cross_asset',CrossAsset),('adaptive_allocator',AdaptiveAllocator)]:
        def factory(cls=cls,key=key):
            policy=cls(models=models,**params[key]);s=current[0];rx=s.receivers['alpha_0']
            def receive(msg):
                rx(msg)
                if isinstance(msg,TradeExecution):policy.on_fill(msg,'alpha_0',s.tick)
            s.receivers['alpha_0']=receive
            observe=policy.observe
            def observed(obs):return observe(FullObservation(**{**vars(obs),'session_open':s.session_open and s.auction_phase is None},trade_flow=flow.get(obs.tick,{})))
            policy.observe=observed
            return policy
        h.STRATEGIES[key]=factory
    actual='original' if name=='original_active' else name
    row=asyncio.run(run_repaired(item['file'],actual,{},175000.));row.update(strategy=name,mode=mode)
    return row

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=6);args=ap.parse_args();walk_models()
    manifest=json.loads((OUT/'history_manifest.json').read_text())
    jobs=[(r,n,mode) for r in manifest for mode in (['frozen_synthetic','walk_forward'] if r['week']>2 else ['frozen_synthetic']) for n in ['event_regime','cross_asset','adaptive_allocator','original_active','no_trade']]
    result=[]
    with (OUT/'historical.jsonl').open('w') as f,ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i,fu in enumerate(as_completed([pool.submit(job,j) for j in jobs]),1):
            r=fu.result();result.append(r);f.write(json.dumps(r,allow_nan=False)+'\n');f.flush();print(i,'/',len(jobs),r['week'],r['mode'],r['strategy'],round(r['pnl'],2),flush=True)
if __name__=='__main__':main()
