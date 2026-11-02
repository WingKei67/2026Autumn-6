"""Offline causal discovery datasets and seed-clustered prediction screens."""
from round2.bootstrap import ROOT,audit
import argparse,asyncio,json,csv,hashlib,time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
from round2 import runner
from round2.features import Recorder,FEATURES,SYMBOLS,labels
OUT=ROOT/'round2/results'

def collect_job(payload):
    seed,week=payload;rec=Recorder();runner.OBSERVER=rec;runner.summarize=audit.summarize
    row=asyncio.run(runner.run_one(seed,[('no_trade',{})],ticks=1500,week=week))
    path=OUT/f'market_{seed}_{week}.npz';np.savez_compressed(path,data=np.asarray(rec.rows))
    (OUT/f'events_{seed}_{week}.json').write_text(json.dumps(rec.events))
    return dict(seed=seed,week=week,path=str(path),rows=len(rec.rows),pnl=row['pnl'])

def historical():
    from alpha_lab.history import frames
    from alpha_lab.data import recording_paths,rows
    from round2.features import FeatureStore,FullObservation
    from team.alpha.core import Quote
    from round2.runner import public_events
    from shared.messages import SessionEvent
    manifest=[]
    for path in recording_paths(ROOT/'sessions'):
        store=FeatureStore();latest={};output=[];halted=set();market_halt=False;opened=False
        flows={};seen=set()
        for row in rows(path):
            msg=row.get('msg',row)
            if msg.get('type')!='trade_execution':continue
            tid=msg.get('trade_id');t=row.get('tick');sym=msg.get('symbol')
            if tid in seen or t is None:continue
            seen.add(tid);signed,total=flows.get((t,sym),(0,0));q=msg.get('quantity',0)
            flows[(t,sym)]=(signed+q*(1 if msg.get('aggressor')=='buy' else -1),total+q)
        for tick,frame in frames(path):
            latest.update(frame['books']);raw=[SessionEvent.model_validate(x) for x in frame['events']]
            for e in raw:
                d=e.data or {}
                if e.event=='SESSION_OPEN':opened=True
                if e.event in ['SESSION_CLOSED','SESSION_PRECLOSE','SESSION_PREOPEN']:opened=False
                if e.event=='TRADING_HALT' and d.get('symbol'):halted.add(d['symbol'])
                if e.event=='TRADING_RESUMED':halted.discard(d.get('symbol'))
                if e.event=='MARKET_HALT':market_halt=True
                if e.event=='MARKET_RESUMED':market_halt=False
            quotes={}
            for sym,b in latest.items():
                if sym not in SYMBOLS or sym=='ARENA10' or not b.get('bids') or not b.get('asks'):continue
                bid,bsize=b['bids'][0];ask,asz=b['asks'][0]
                if 0<bid<ask:quotes[sym]=Quote(bid,ask,int(bsize),int(asz))
            events=tuple(__import__('dataclasses').replace(e,tick=tick) for e in public_events(raw))
            flow={s:a/max(1,b) for (t,s),(a,b) in flows.items() if t==tick}
            obs=FullObservation(tick,quotes,{},175000.,175000.,175000.,frozenset(halted),market_halt,opened,events,trade_flow=flow)
            for sym,v in store.update(obs).items():
                q=quotes[sym];output.append([tick,SYMBOLS.index(sym),q.mid,q.bid,q.ask,int(not opened or market_halt or sym in halted),*v])
        week=int(path.name.split('week')[1][:2]);dest=OUT/f'history_w{week}.npz';np.savez_compressed(dest,data=np.asarray(output));manifest.append(dict(week=week,file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=len(output),path=str(dest),status='previously examined; retrospective chronological fold only'))
    (OUT/'history_manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stage',choices=['collect','history'],required=True);ap.add_argument('--workers',type=int,default=8);args=ap.parse_args();OUT.mkdir(exist_ok=True)
    if args.stage=='history':print(historical());return
    # Existing audit used 0..11,1000..1049,2000..2049,3000..3049.
    protocol=dict(discovery_seeds=list(range(4000,4040)),validation_seeds=list(range(4100,4120)),sealed_test_seeds=list(range(6000,6100)),weeks=[5,9],horizons=[3,5,10,20,40,60,120],test_not_used_for_discovery=True)
    (OUT/'research_protocol.json').write_text(json.dumps(protocol,indent=2))
    jobs=[(seed,w) for seed in protocol['discovery_seeds']+protocol['validation_seeds'] for w in protocol['weeks']];result=[];t=time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i,f in enumerate(as_completed([pool.submit(collect_job,j) for j in jobs]),1):
            result.append(f.result())
            if i%10==0:print(i,'/',len(jobs),'seconds',round(time.monotonic()-t),flush=True)
    (OUT/'market_manifest.json').write_text(json.dumps(result,indent=2))
if __name__=='__main__':main()
