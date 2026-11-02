#!/usr/bin/env python3
"""Replay fee-bearing maker runs and audit authoritative cancellation charges.

Trading code and parameters are untouched. A read-only metrics observer extracts
final Portfolio.total_fees_paid, then requires P&L to reproduce before attaching
the extra fee breakdown to the recorded result. Week 5 has no cancellation fee.
"""
import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import arena
from alpha_lab.experiment import save_rows,source_hashes


def replay(row):
    import alpha_lab.runner as runner
    original=runner.summarize
    def observed(server,accounts,*args,**kwargs):
        result=original(server,accounts,*args,**kwargs)
        total=sum(server.portfolios[a].total_fees_paid for a in accounts)
        rebate=sum(server.portfolios[a].total_rebates_earned for a in accounts)
        result.update(total_fees_paid=total,cancellation_fees_paid=max(0.,total-result['commissions']),
                      total_net_fees=total-rebate)
        return result
    runner.summarize=observed
    try:
        actual=asyncio.run(runner.run_one(row['seed'],row['components'],ticks=row['ticks'],
            week=row['week'],cash=row['cash'],stress=row['stress']))
        if abs(actual['pnl']-row['pnl'])>1e-6 or actual['fills']!=row['fills']:
            raise AssertionError(f"Nonreproducible replay: {row['candidate']} W{row['week']} seed {row['seed']}")
        return {k:actual[k] for k in ('total_fees_paid','cancellation_fees_paid','total_net_fees')}
    finally:runner.summarize=original


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT/'alpha_results'/'study')
    ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();root=args.input
    protocol=json.loads((root/'protocol.json').read_text())
    if source_hashes(ROOT)!=protocol['source_sha256']:raise ValueError('Model source changed since the executed study')
    files=[root/name for name in ('out_of_sample.json','stress.json','ablations.json','calibration.json')]
    files +=[root/'portfolio_confirmation'/name for name in ('results.json','stress.json')]
    files=[p for p in files if p.exists()]
    jobs=[];records={};audits=[]
    for file in files:
        data=json.loads(file.read_text());records[file]=data
        for i,row in enumerate(data):
            if row['week']==9 and any(name=='market_making' for name,_ in row['components']):
                jobs.append((file,i,row))
            else:
                row.update(total_fees_paid=row['commissions'],cancellation_fees_paid=0.,total_net_fees=row['net_fees'])
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(replay,row):(file,i,row) for file,i,row in jobs}
        for count,future in enumerate(as_completed(futures),1):
            file,i,row=futures[future];extra=future.result()
            records[file][i].update(extra)
            audits.append({'file':str(file.relative_to(root)),'week':row['week'],'seed':row['seed'],
                'candidate':row['candidate'],'stress':row['stress'],'pnl_reproduced':True,**extra})
            if count==1 or count%25==0 or count==len(jobs):print(f'Fee audit: {count}/{len(jobs)} exact P&L/fill replays',flush=True)
    for file,data in records.items():save_rows(file.with_suffix(''),data)
    save_rows(root/'fee_audit',sorted(audits,key=lambda r:(r['file'],r['stress'],r['week'],r['candidate'],r['seed'])))
    print('Authoritative cancellation-fee audit complete.',flush=True)


if __name__=='__main__':main()
