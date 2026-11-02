"""Paired correctness comparison on previously viewed seeds; NOT fresh OOS."""
import json,time,hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from audit_study import job,ROOT
if __name__=='__main__':
    out=ROOT/'audit_review/results';names=['event_momentum','post_halt','market_making','basket','no_trade']
    protocol={'seeds':list(range(1000,1050)),'weeks':[5,9],'names':names,'mode':'corrected','stress':'base','ticks':1500,'purpose':'paired correction effect on previously seen reproduction seeds; not independent OOS','study_code_sha256':hashlib.sha256((ROOT/'audit_review/code/audit_study.py').read_bytes()).hexdigest()}
    (out/'paired_corrections_protocol.json').write_text(json.dumps(protocol,indent=2));t=time.monotonic()
    with (out/'paired_corrections.jsonl').open('w') as f,ProcessPoolExecutor(max_workers=8) as pool:
        fs=[pool.submit(job,(seed,week,'corrected','base',names)) for week in [5,9] for seed in range(1000,1050)]
        for i,future in enumerate(as_completed(fs),1):
            for r in future.result():f.write(json.dumps(r,allow_nan=False)+'\n')
            f.flush()
            if i%10==0:print(i,'/100 seed/week jobs;',i*5,'trials;',round(time.monotonic()-t),'seconds',flush=True)
