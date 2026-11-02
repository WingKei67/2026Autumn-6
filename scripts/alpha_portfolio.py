#!/usr/bin/env python3
"""Confirm a profit-ranked combined portfolio on a SECOND, untouched seed set.

The primary study's risk-adjusted selection can choose an almost-inactive
sleeve. This explicitly different profit-ranked portfolio is chosen from
calibration means, then tested only on new seeds 2000--2049. Never relabel its
primary-study performance as a fresh holdout.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import arena
from alpha_lab.experiment import run_batch,save_rows,spec,Candidate,source_hashes


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT/'alpha_results'/'study')
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--resume',action='store_true',help='reuse fully completed week/regime batches with identical definitions')
    args=ap.parse_args();root=args.input
    protocol=json.loads((root/'protocol.json').read_text())
    if source_hashes(ROOT)!=protocol['source_sha256']:raise ValueError('Model source changed since calibration')
    chosen=json.loads((root/'frozen_selection.json').read_text())
    top=sorted((r for name,r in chosen.items() if name!='basket' and r['mean_excess_no_trade']>0),
        key=lambda r:-r['mean_excess_no_trade'])[:2]
    if len(top)<2:raise ValueError('Fewer than two positive calibration sleeves; no stronger-portfolio claim is justified')
    components=[(r['strategy'],r['params']) for r in top]
    combined={'name':'combined_profit','params':{},'key':'combined_profit','components':components}
    seeds=list(range(2000,2050));out=root/'portfolio_confirmation';out.mkdir(exist_ok=True)
    definition={'components':components,'seeds':seeds,'selection':'Top two positive calibration mean excess values; equal capital sleeves',
        'disclosure':'Portfolio design motivated by primary-study results; confirmation seeds are independent and were not used for either selection.',
        'source_sha256':source_hashes(ROOT),'ticks':protocol['ticks'],'cash':protocol['cash']}
    if args.resume and (out/'definition.json').exists():
        old=json.loads((out/'definition.json').read_text())
        if json.dumps(old,sort_keys=True)!=json.dumps(definition,sort_keys=True):raise ValueError('Cannot resume a different portfolio protocol')
    (out/'definition.json').write_text(json.dumps(definition,indent=2))
    candidates=[spec(Candidate(r['strategy'],r['params'],r['strategy'])) for r in top]
    benchmarks=[spec(Candidate(name,{},name)) for name in ('original','no_trade')]
    results=json.loads((out/'results.json').read_text()) if args.resume and (out/'results.json').exists() else []
    for week in (5,9):
        if len([r for r in results if r['week']==week])==5*len(seeds):continue
        results=[r for r in results if r['week']!=week]
        results+=run_batch(seeds,candidates+[combined]+benchmarks,protocol['ticks'],week,
            protocol['cash'],'base',args.workers,f'Confirmation W{week}')
        save_rows(out/'results',results)
    stresses=json.loads((out/'stress.json').read_text()) if args.resume and (out/'stress.json').exists() else []
    for stress in ('fees_60bps','latency_3ticks','volatile'):
        for week in (5,9):
            if len([r for r in stresses if r['week']==week and r['stress']==stress])==3*len(seeds):continue
            stresses=[r for r in stresses if not (r['week']==week and r['stress']==stress)]
            stresses+=run_batch(seeds,[combined]+benchmarks,protocol['ticks'],week,
                protocol['cash'],stress,args.workers,f'Confirmation {stress} W{week}')
            save_rows(out/'stress',stresses)
    print('Fresh portfolio confirmation complete:',out,flush=True)


if __name__=='__main__':main()
