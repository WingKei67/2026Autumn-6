"""Preregistered calibration, held-out seed evaluation, sensitivities and ablations."""
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from itertools import product
import json
import math
from pathlib import Path
from statistics import mean, stdev
import time
import hashlib

from .runner import run_one, default_cash


@dataclass(frozen=True)
class Candidate:
    name:str
    params:dict
    label:str=''

    @property
    def key(self):
        return self.label or self.name+':'+json.dumps(self.params,sort_keys=True,separators=(',',':'))


def grids():
    candidates=[]
    for c,h,t in product((3,5,10,15),(20,40,60,120),(2.,4.)):
        candidates.append(Candidate('event_momentum',{'confirmation':c,'holding':h,'trailing':t}))
    for z in (2.5,3.,3.5,4.):candidates.append(Candidate('post_halt',{'z':z}))
    for width,skew in product((1.,1.5,2.5),(.5,1.5)):
        candidates.append(Candidate('market_making',{'width':width,'skew':skew}))
    for z,lookback in product((2.,2.5,3.,3.5),(60,120)):
        candidates.append(Candidate('basket',{'z':z,'lookback':lookback}))
    return candidates


def job(payload):
    seed,candidates,ticks,week,cash,stress=payload
    async def execute():
        result=[]
        for candidate in candidates:
            components=candidate.get('components') or [(candidate['name'],candidate['params'])]
            row=await run_one(seed,components,ticks=ticks,week=week,cash=cash,stress=stress)
            row['candidate']=candidate['key'];row['strategy']=candidate['name']
            result.append(row)
        return result
    return asyncio.run(execute())


def spec(c):return {'name':c.name,'params':c.params,'key':c.key}


def run_batch(seeds,candidates,ticks,week,cash,stress,workers,description):
    results=[];start=time.monotonic()
    payloads=[(seed,candidates,ticks,week,cash,stress) for seed in seeds]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(job,p):p[0] for p in payloads}
        for i,f in enumerate(as_completed(futures),1):
            try:results.extend(f.result())
            except Exception as exc:raise RuntimeError(f'{description}: seed {futures[f]} failed') from exc
            if i==1 or i%5==0 or i==len(payloads):
                print(f'{description}: seeds {i}/{len(seeds)}, runs {len(results)}, elapsed {time.monotonic()-start:.1f}s',flush=True)
    return sorted(results,key=lambda r:(r['seed'],r['candidate']))


def save_rows(path,rows):
    path=Path(path)
    path.with_suffix('.json').write_text(json.dumps(rows,indent=2,allow_nan=False))
    if not rows:return
    keys=sorted(set().union(*(row.keys() for row in rows)))
    with path.with_suffix('.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()})


def select(calibration):
    by={}
    baseline={(r['seed'],r['week']):r['pnl'] for r in calibration if r['strategy']=='no_trade'}
    for row in calibration:
        if row['strategy'] in ('no_trade','buy_hold','original'):continue
        key=row['candidate'];by.setdefault(key,[]).append(row)
    sensitivity=[]
    for key,rows in by.items():
        excess=[r['pnl']-baseline[(r['seed'],r['week'])] for r in rows]
        avg=mean(excess);sigma=stdev(excess) if len(excess)>1 else 0
        failures=sum(r['liquidations']+r['position_violations']+r['team_position_violations'] for r in rows)
        # Frozen risk-adjusted calibration score; never rank parameters on OOS.
        score=avg-.5*sigma-1e9*failures
        sensitivity.append({'candidate':key,'strategy':rows[0]['strategy'],'params':rows[0]['components'][0][1],
            'week':rows[0]['week'],'mean_pnl':mean(r['pnl'] for r in rows),
            'mean_excess_no_trade':avg,'std_excess':sigma,'selection_score':score,'failures':failures,
            'mean_fills':mean(r['fills'] for r in rows),'n':len(rows)})
    chosen={}
    for row in sorted(sensitivity,key=lambda r:(-r['selection_score'],r['candidate'])):
        chosen.setdefault(row['strategy'],row)
    return chosen,sensitivity


def ablations(chosen):
    variants=[]
    for name,patch,label in [('event_momentum',{'fee_gate':False},'no_fee_gate'),
        ('event_momentum',{'use_trailing':False},'no_trailing'),
        ('post_halt',{'reversal':False},'no_reversal'),('post_halt',{'earnings_filter':False},'no_earnings_filter'),
        ('market_making',{'micro':False},'no_microprice'),('market_making',{'adverse':False},'no_adverse_filter'),
        ('market_making',{'skew':0.},'no_inventory_skew'),('basket',{'rolling_beta':False},'fixed_beta'),
        ('basket',{'hedge':False},'unhedged')]:
        params={**chosen[name]['params'],**patch}
        variants.append(spec(Candidate(name,params,name+':ablation:'+label)))
    return variants


def source_hashes(root):
    paths=list((Path(root)/'team/alpha').glob('*.py'))+list((Path(root)/'alpha_lab').glob('*.py'))
    for folder in ('engine/exchange','engine/shared','engine/plugins/securities'):
        paths.extend((Path(root)/folder).glob('*.py'))
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
