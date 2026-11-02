"""Single CLI for isolated experiments, frozen splits and paired reports."""
from __future__ import annotations
import argparse, asyncio, csv, hashlib, json, os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/results'
REGISTRY = ROOT/'research/EXPERIMENT_REGISTRY.csv'
SPLITS = {'train': list(range(1,7)), 'validation': list(range(101,161)),
          'sealed': list(range(1001,1101)),
          # Added after seeing train-only results: larger tuning pool; never overlaps other splits.
          'dev': list(range(11,71))}
FIELDS = ['id','stage','name','hypothesis','source_commit','source_hash','config','seed','week',
          'population','stress','ticks','status','started','completed','score','pnl','fees',
          'rebates','liquidated','artifact','reason']

def digest():
    h = hashlib.sha256()
    for folder in ['research','team','engine','season','arena','week2']:
        for p in sorted((ROOT/folder).rglob('*')):
            if p.suffix not in {'.py','.json'} or 'results' in p.parts: continue
            if folder == 'research' and p.name not in {'harness.py', 'strategies.py'}: continue  # analysis/test files do not affect results
            h.update(str(p.relative_to(ROOT)).encode()); h.update(p.read_bytes())
    return h.hexdigest()

def worker(job):
    args, dest = job
    cmd = [sys.executable, '-m', 'research.suite', 'one', '--name', args['name'],
           '--seed', str(args['seed']), '--week', str(args['week']), '--ticks', str(args['ticks']),
           '--population', args['population'], '--stress', args['stress'],
           '--params', json.dumps(args.get('params',{})), '--output', str(dest)]
    with open(str(dest)+'.log','w') as f:
        p = subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    return args, dest, p.returncode

def register(row):
    exists = REGISTRY.exists()
    with REGISTRY.open('a',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); 
        if not exists: w.writeheader()
        w.writerow({k:row.get(k,'') for k in FIELDS})

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['freeze','one','batch','report','score-study'])
    ap.add_argument('--name',default='baseline_trader'); ap.add_argument('--seed',type=int,default=1)
    ap.add_argument('--week',type=int,default=6); ap.add_argument('--ticks',type=int,default=1200)
    ap.add_argument('--population',default='mixed'); ap.add_argument('--stress',default='base')
    ap.add_argument('--params',default='{}'); ap.add_argument('--output')
    ap.add_argument('--stage',choices=SPLITS,default='train'); ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--names',default='baseline_trader,season_trader,short_trader,aggressive_trader,baseline_broker,season_broker,passive_broker')
    ap.add_argument('--count',type=int); ap.add_argument('--weeks',default='6,7,8,9,10')
    args=ap.parse_args(); OUT.mkdir(exist_ok=True)
    if args.command=='freeze':
        path=ROOT/'research/SPLITS.json'
        if path.exists(): assert json.loads(path.read_text())['seeds']==SPLITS
        else: path.write_text(json.dumps({'seeds':SPLITS,'created':time.time(),
            'protocol':'Each seed is one independent run; week rotates over 6–10; never split ticks. Select on validation only. Sealed once after freeze of finalists. No tuning after sealed evaluation.',
            'budget':'Successive screening: 6 training seeds/config, 6 validation, then 100 independent seeds/finalist if run time permits. Max four worker processes.'},indent=2))
        print(path); return
    if args.command=='one':
        from research.harness import run_one
        result=asyncio.run(run_one(args.name,args.seed,args.week,args.ticks,args.population,args.stress,json.loads(args.params)))
        dest=Path(args.output) if args.output else OUT/'smoke.json'
        dest.write_text(json.dumps(result,indent=2)); print(json.dumps({k:result[k] for k in ['name','risk_adjusted','pnl','fills','orders','errors','accounting_errors']})); return
    if args.command=='batch':
        if not (ROOT/'research/SPLITS.json').exists(): raise RuntimeError('Freeze splits first')
        sha=digest(); commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        seeds=SPLITS[args.stage][:args.count]; weeks=[int(w) for w in args.weeks.split(',')]
        jobs=[]; rows={}
        for name in args.names.split(','):
            for i,seed in enumerate(seeds):
                config=dict(name=name,seed=seed,week=weeks[i%len(weeks)],ticks=args.ticks,
                            population=args.population,stress=args.stress,params=json.loads(args.params))
                identity=hashlib.sha256(json.dumps(dict(config,source=sha,stage=args.stage),sort_keys=True).encode()).hexdigest()[:16]
                dest=OUT/(identity+'.json')
                if dest.exists(): continue
                row=dict(id=identity,stage=args.stage,name=name,hypothesis=name.replace('_',' '),source_commit=commit,
                    source_hash=sha,config=json.dumps(config,sort_keys=True),seed=seed,week=config['week'],
                    population=args.population,stress=args.stress,ticks=args.ticks,status='started',started=time.time(),artifact=str(dest.relative_to(ROOT)))
                register(row); rows[str(dest)]=row; jobs.append((config,dest))
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(worker,j) for j in jobs]):
                config,dest,code=future.result(); row=rows[str(dest)]
                row.update(completed=time.time(),status='failed' if code else 'completed')
                if not code:
                    r=json.loads(dest.read_text()); row.update(score=r['risk_adjusted'],pnl=r['pnl'],fees=r['fees'],rebates=r['rebates'],liquidated=r['liquidated'],reason='pending paired evaluation')
                    if r['accounting_errors'] or r['liquidated']: row['reason']='DISQUALIFIED accounting/liquidation'
                else: row['reason']='execution failure; inspect artifact .log'
                register(row); print(row['name'],row['seed'],row['status'],row.get('score',''),flush=True)
        return
    if args.command=='score-study':
        from research.harness import scoring
        import random
        rng=random.Random(44); shocks=[rng.gauss(.0003,.001) for _ in range(60)]
        result=[]
        for size in [0,.25,.5,1,2,4]:
            eq=[175000.]
            for r in shocks: eq.append(eq[-1]*(1+scoring.RISK_FREE_PER_SNAPSHOT+size*r))
            result.append(dict(size=size,**scoring.score_equity(eq),equity=eq))
        for label,rs in [('steady',[.0002]*60),('tail',[.0003]*59+[-.2]),('losing',[-.001]*60)]:
            eq=[175000.]
            for r in rs: eq.append(eq[-1]*(1+r))
            result.append(dict(case=label,**scoring.score_equity(eq),equity=eq))
        (OUT/'score_study.json').write_text(json.dumps(result,indent=2)); return
    if args.command=='report':
        from research.report import build
        build()

if __name__=='__main__': main()
