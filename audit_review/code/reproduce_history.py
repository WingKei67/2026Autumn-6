import sys,json,asyncio
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[2];ARCHIVE=ROOT/'audit_review/extracted/2026Autumn-6-fresh'
sys.path.insert(0,str(ARCHIVE))
from network_guard import install
install()
import arena
from scripts.alpha_history import run_repaired
PARAMS={'event_momentum':{'confirmation':3,'holding':120,'trailing':2.},'post_halt':{'z':2.5},'basket':{'z':3.5,'lookback':60}}
def job(x):
 p,n=x
 return asyncio.run(run_repaired(p,n,PARAMS.get(n,{}),175000.))
if __name__=='__main__':
 inputs=json.loads((ROOT/'audit_review/results/historical_inputs.json').read_text());assert all(x['matches'] for x in inputs)
 rows=[]
 with ProcessPoolExecutor(max_workers=3) as pool:
  for f in as_completed([pool.submit(job,(x['file'],n)) for x in inputs for n in [*PARAMS,'buy_hold','no_trade']]):
   r=f.result();rows.append(r);print(r['strategy'],Path(r['file']).name,round(r['pnl'],3),flush=True)
   (ROOT/'audit_review/results/historical_reproduced.json').write_text(json.dumps(rows,indent=2))
