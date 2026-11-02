#!/usr/bin/env python3
"""Completely offline real-exchange alpha experiments. Never opens a socket."""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
import platform
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import arena  # engine path bootstrap; no live run() calls
from alpha_lab.data import inventory
from alpha_lab.experiment import grids,spec,run_batch,save_rows,select,ablations,Candidate,source_hashes
from alpha_lab.runner import default_cash,run_one


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ROOT/'alpha_results'/'study')
    ap.add_argument('--calibration-seeds',type=int,default=12)
    ap.add_argument('--evaluation-seeds',type=int,default=50)
    ap.add_argument('--ticks',type=int,default=1500)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--cash',type=float,default=None)
    ap.add_argument('--stage',choices=('calibrate','evaluate','stress','history','all'),default='all')
    ap.add_argument('--skip-inventory',action='store_true')
    args=ap.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    print(f'Offline experiment pid={os.getpid()}, stage={args.stage}, output={out}',flush=True)
    (out/'runner.pid').write_text(str(os.getpid()))
    cash=default_cash() if args.cash is None else args.cash
    if args.stage in ('all','calibrate'):
        if not args.skip_inventory:(out/'data_inventory.json').write_text(json.dumps(inventory(ROOT/'sessions'),indent=2))
        candidates=grids()
        metadata={'python':platform.python_version(),'pid':os.getpid(),'calibration_seeds':list(range(args.calibration_seeds)),
            'evaluation_seeds':list(range(1000,1000+args.evaluation_seeds)),
            'ticks':args.ticks,'cash':cash,'cash_source':'team.config.CAPITAL[6_rings_trader_1]' if args.cash is None else 'explicit CLI override',
            'fee':.003,'execution_delay_ticks':1,'grid_count':len(candidates),
            'calibration_weeks':{'event_momentum':5,'post_halt':5,'market_making':5,'basket':9},
            'selection':'mean paired excess over no_trade minus 0.5 standard deviation; safety failures penalized 1e9',
            'source_sha256':source_hashes(ROOT),
            'scenario_sha256':{str(w):hashlib.sha256((ROOT/'season'/f'week{w:02}.json').read_bytes()).hexdigest() for w in (5,9)},
            'scenario_definitions':{str(w):json.loads((ROOT/'season'/f'week{w:02}.json').read_text()) for w in (5,9)},
            'live_trader_sha256':hashlib.sha256((ROOT/'team/trader.py').read_bytes()).hexdigest(),
            'live_broker_sha256':hashlib.sha256((ROOT/'team/broker.py').read_bytes()).hexdigest(),
            'model':'actual exchange matching/risk/fees/calendar/auctions; synthetic external liquidity and flow; independent venue per candidate',
            'strictly_offline':True}
        (out/'protocol.json').write_text(json.dumps(metadata,indent=2))
        calibration=[]
        for week in (5,9):
            selected=[spec(c) for c in candidates if (c.name=='basket')==(week==9)]
            selected.append(spec(Candidate('no_trade',{},'no_trade')))
            calibration+=run_batch(metadata['calibration_seeds'],selected,args.ticks,week,cash,'base',args.workers,f'calibration W{week}')
            save_rows(out/'calibration',calibration)
        chosen,sensitivity=select(calibration)
        (out/'frozen_selection.json').write_text(json.dumps(chosen,indent=2))
        save_rows(out/'sensitivity',sensitivity)
        print('Calibration complete. Parameters frozen before OOS starts.',flush=True)
    metadata=json.loads((out/'protocol.json').read_text())
    chosen=json.loads((out/'frozen_selection.json').read_text())
    for week,expected in metadata.get('scenario_sha256',{}).items():
        if hashlib.sha256((ROOT/'season'/f'week{int(week):02}.json').read_bytes()).hexdigest()!=expected:
            raise ValueError('Scenario changed after calibration')
    if args.ticks!=metadata['ticks'] or cash!=metadata['cash']:raise ValueError('Protocol mismatch: use the calibration ticks/cash')
    if source_hashes(ROOT)!=metadata['source_sha256']:raise ValueError('Source changed after calibration; rerun calibration in a new output directory')
    seeds=metadata['evaluation_seeds'];ticks=metadata['ticks']
    selected=[spec(Candidate(name,row['params'],name)) for name,row in chosen.items()]
    benchmarks=[spec(Candidate(name,{},name)) for name in ('original','buy_hold','no_trade')]
    # Cross-week calibration scores are not comparable: combine only the best
    # Week 5 candidates, selected without looking at OOS performance.
    top=sorted((r for name,r in chosen.items() if name!='basket'),key=lambda r:-r['selection_score'])[:2]
    combined={'name':'combined','params':{},'key':'combined',
        'components':[(r['strategy'],r['params']) for r in top]}
    (out/'combined_definition.json').write_text(json.dumps(combined,indent=2))
    if args.stage in ('all','evaluate'):
        results=[]
        for week in (5,9):
            results+=run_batch(seeds,selected+benchmarks+[combined],ticks,week,cash,'base',args.workers,f'OOS W{week}')
            save_rows(out/'out_of_sample',results)
        variants=[]
        for week in (5,9):
            applicable=[x for x in ablations(chosen) if (x['name']=='basket')==(week==9)]
            variants+=run_batch(seeds,applicable+[spec(Candidate('no_trade',{},'no_trade'))],ticks,week,cash,'base',args.workers,f'ablation W{week}')
            save_rows(out/'ablations',variants)
        curves=[]
        # Fixed illustrative seed, not the best-performing path.
        for candidate in selected+benchmarks+[combined]:
            r=asyncio.run(run_one(seeds[0],candidate.get('components') or [(candidate['name'],candidate['params'])],
                ticks=ticks,week=9,cash=cash,keep_curve=True))
            r['candidate']=candidate['key'];curves.append(r)
        (out/'example_curves.json').write_text(json.dumps(curves,indent=2))
    if args.stage in ('all','stress'):
        results=[]
        for stress in ('fees_60bps','latency_3ticks','volatile'):
            for week in (5,9):
                results+=run_batch(seeds,selected+benchmarks+[combined],ticks,week,cash,stress,args.workers,f'{stress} W{week}')
                save_rows(out/'stress',results)
    if args.stage in ('all','history'):
        from scripts.alpha_history import study_repaired
        historical=study_repaired(ROOT,chosen,cash,args.workers)
        save_rows(out/'historical',historical)
        save_rows(out/'historical_funded',historical)
    print(f'Complete: {out}',flush=True)


if __name__=='__main__':main()
