"""Budget-limited completion. Never overwrite an existing experiment output."""
from round2.experiments import OUT,NEW,hashes,execute
import argparse,json,hashlib
from concurrent.futures import ProcessPoolExecutor,as_completed

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stage',choices=['stress','history','history_resume'],required=True)
    ap.add_argument('--workers',type=int,default=4);args=ap.parse_args()
    p=json.loads((OUT/'frozen_protocol.json').read_text())
    assert hashes()==p['code_sha256']
    assert hashlib.sha256((OUT/'frozen_models.json').read_bytes()).hexdigest()==p['models_sha256']
    path=OUT/('priority_stress.jsonl' if args.stage=='stress' else 'historical_remaining.jsonl' if args.stage=='history_resume' else 'historical_frozen.jsonl')
    if path.exists():raise SystemExit(f'Refusing to overwrite {path}; inspect saved results first.')
    if args.stage=='stress':
        candidates=[(n,p['parameters'][n],n) for n in NEW]+[(n,{},n) for n in ['original_active','round1_combined','no_trade']]
        # Fixed before reading any test results. Reduced from 50 to 20 seeds
        # and from four to two stresses because the user requested economy.
        jobs=[(s,w,stress,candidates,'priority_stress') for stress in ['fees_60bps','volatile'] for s in range(6000,6020) for w in [5,9]]
        (OUT/'priority_stress_protocol.json').write_text(json.dumps(dict(seeds=list(range(6000,6020)),weeks=[5,9],stresses=['fees_60bps','volatile'],candidates=candidates,reason='Budget-limited completion fixed before test results inspection'),indent=2))
        execute(path,jobs,args.workers)
    else:
        from round2.history_eval import job
        items=json.loads((OUT/'history_manifest.json').read_text())
        jobs=[(r,n,'frozen_synthetic') for r in items for n in NEW+['original_active','no_trade']]
        if args.stage=='history_resume':
            done={(r['week'],r['strategy']) for r in map(json.loads,(OUT/'historical_frozen.jsonl').read_text().splitlines())}
            jobs=[j for j in jobs if (j[0]['week'],j[1]) not in done]
        with path.open('x') as f,ProcessPoolExecutor(max_workers=args.workers) as pool:
            for i,fu in enumerate(as_completed([pool.submit(job,j) for j in jobs]),1):
                r=fu.result();f.write(json.dumps(r,allow_nan=False)+'\n');f.flush()
                print(i,len(jobs),r['week'],r['strategy'],round(r['pnl'],2),flush=True)
if __name__=='__main__':main()
