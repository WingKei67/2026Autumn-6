"""Development -> freeze -> untouched paired tests. Completely offline."""
from round2.bootstrap import ROOT,audit
import asyncio,argparse,json,hashlib,time,csv
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
from round2 import runner,strategies
OUT=ROOT/'round2/results'
BASE_REFRESH=runner.MarketPopulation.refresh
NEW=['event_regime','cross_asset','adaptive_allocator']
def hashes():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'round2'/n for n in ['features.py','models.py','strategies.py','gateway.py','runner.py','baselines.py','experiments.py']]}
def summary(server,accounts,receivers,gateways,*args):
    r=audit.summarize(server,accounts,receivers,gateways,*args)
    marks=server._bidask_marks()
    r['final_cash']=sum(server.portfolios[a].cash for a in accounts)
    r['final_positions']={sym:sum(server.portfolios[a].positions.get(sym,0) for a in accounts) for sym in server.books}
    r['final_marks']={sym:float((marks.get(sym) or (None,None))[0] or server.ref_prices[sym]) for sym in server.books}
    r['public_events']=[dict(event=e.event,data=e.data) for e in server.wire_events if e.event in ['CALENDAR_EVENT','TRADING_HALT','TRADING_RESUMED']]
    return r
async def fast_refresh(self,force=False):
    self.ttl=1;return await BASE_REFRESH(self,force)
def component(name,params):
    if name=='round1_combined':return [('event_momentum',{'confirmation':3,'holding':120,'trailing':2.}),('market_making',{'width':1.,'skew':1.5})]
    if name.startswith('allocator_'):
        params={**params}
        if name=='allocator_fixed8':params['size_mode']='fixed8'
        if name=='allocator_single':params['max_entries']=1
        if name=='allocator_no_uncertainty':params['uncertainty']=False
        return [('adaptive_allocator',params)]
    return [(name,params)]
def job(payload):
    seed,week,stress,candidates,stage=payload
    strategies.MODEL_PATH=OUT/('models_discovery.json' if stage=='validation' else 'frozen_models.json')
    runner.OBSERVER=None;runner.summarize=summary
    runner.MarketPopulation.refresh=fast_refresh if stress=='fast_quotes' else BASE_REFRESH
    result=[]
    for name,params,key in candidates:
        row=asyncio.run(runner.run_one(seed,component(name,params),week=week,stress=stress,ticks=1500))
        row.update(candidate=key,strategy=name,split=stage,model_sha256=hashlib.sha256(strategies.MODEL_PATH.read_bytes()).hexdigest());result.append(row)
    return result

def execute(path,jobs,workers):
    rows=[];t=time.monotonic()
    with path.open('w') as f,ProcessPoolExecutor(max_workers=workers) as pool:
        for i,future in enumerate(as_completed([pool.submit(job,j) for j in jobs]),1):
            results=future.result();rows.extend(results)
            for r in results:f.write(json.dumps(r,allow_nan=False)+'\n')
            f.flush()
            if i%10==0 or i==len(jobs):print(path.stem,i,'/',len(jobs),'runs',len(rows),'seconds',round(time.monotonic()-t),flush=True)
    return rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stage',choices=['validation','test','stress','ablations'],required=True);ap.add_argument('--workers',type=int,default=8);args=ap.parse_args()
    if args.stage=='validation':
        started_hashes=hashes();started_models=hashlib.sha256((OUT/'models_discovery.json').read_bytes()).hexdigest()
        (OUT/'validation_started.json').write_text(json.dumps(dict(code_sha256=started_hashes,models_sha256=started_models),indent=2))
        candidates=[(n,{'safety':s},f'{n}:safety={s}') for n in NEW for s in [.001,.003]]+[(n,{},n) for n in ['original','original_active','round1_combined','no_trade']]
        rows=execute(OUT/'validation.jsonl',[(seed,w,'base',candidates,'validation') for seed in range(4100,4120) for w in [5,9]],args.workers)
        assert hashes()==started_hashes and hashlib.sha256((OUT/'models_discovery.json').read_bytes()).hexdigest()==started_models
        base={(r['seed'],r['week']):r['pnl'] for r in rows if r['candidate']=='original_active'};chosen={};scores=[]
        for n in NEW:
            choices=[]
            for s in [.001,.003]:
                key=f'{n}:safety={s}';v=[r for r in rows if r['candidate']==key]
                delta=[r['pnl']-base[(r['seed'],r['week'])] for r in v]
                # Optimize the requested objective, using validation only.
                score=float(np.mean(delta));choices.append((score,s))
                scores.append(dict(strategy=n,safety=s,mean_increment_vs_original_active=score,mean_pnl=float(np.mean([r['pnl'] for r in v]))))
            chosen[n]={'safety':max(choices)[1]}
        (OUT/'validation_selection.json').write_text(json.dumps(scores,indent=2))
        (OUT/'frozen_models.json').write_bytes((OUT/'models_discovery.json').read_bytes())
        protocol=dict(parameters=chosen,code_sha256=hashes(),models_sha256=hashlib.sha256((OUT/'frozen_models.json').read_bytes()).hexdigest(),discovery_seeds=list(range(4000,4040)),validation_seeds=list(range(4100,4120)),test_seeds=list(range(6000,6100)),stress_seeds=list(range(6000,6050)),weeks=[5,9],cash=175000,ticks=1500,taker_fee=.003,stress_definitions=['fees_60bps','volatile','fast_quotes','latency_3ticks'],selection='maximum mean paired net P&L increment versus active original on validation; no test inspection',shared_risk='same cash, 150 shares, 70k gross, 60% cash reserve, 60 shares/request; new per-name notional <=20% starting cash')
        (OUT/'frozen_protocol.json').write_text(json.dumps(protocol,indent=2));print('Frozen',chosen,flush=True);return
    protocol=json.loads((OUT/'frozen_protocol.json').read_text());assert hashes()==protocol['code_sha256'],'Frozen trading code changed; do not tune on the test set'
    assert hashlib.sha256((OUT/'frozen_models.json').read_bytes()).hexdigest()==protocol['models_sha256']
    candidates=[(n,protocol['parameters'][n],n) for n in NEW]+[(n,{},n) for n in ['original','original_active','round1_combined','no_trade']]
    seeds=protocol['test_seeds'];stresses=['base']
    if args.stage=='stress':
        seeds=protocol['stress_seeds'];stresses=protocol['stress_definitions'];candidates=[c for c in candidates if c[0]!='original']
    if args.stage=='ablations':
        seeds=protocol['stress_seeds'];candidates=[(n,protocol['parameters']['adaptive_allocator'],n) for n in ['allocator_fixed8','allocator_single','allocator_no_uncertainty']]
    execute(OUT/(args.stage+'.jsonl'),[(seed,w,stress,candidates,args.stage) for stress in stresses for seed in seeds for w in [5,9]],args.workers)
if __name__=='__main__':main()
