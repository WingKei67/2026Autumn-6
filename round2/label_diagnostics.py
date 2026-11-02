"""Evaluate saved validation labels without refitting or touching test policies."""
from round2.bootstrap import ROOT
from round2.features import labels
from round2.labels_review import labels_with_halts
from round2.models import entry_mask,predict
import json,csv
import numpy as np
OUT=ROOT/'round2/results'

def main():
    models=json.loads((OUT/'frozen_models.json').read_text())
    files=[r for r in json.loads((OUT/'market_manifest.json').read_text()) if r['seed']>=4100]
    rows=[]
    for item in files:
        a=np.load(item['path'])['data'];cache={}
        for family,m in models.items():
            h=m['horizon']
            if h not in cache:cache[h]=(labels(a,h)[1],labels_with_halts(a,h))
            old,new=cache[h];y=new[1];v=a[:,6:]
            candidate=entry_mask(v,family)&(predict(v,m)>.003)&(a[:,1]<10)&(a[:,0]%3==0)
            oo=candidate&np.isfinite(old);nn=candidate&np.isfinite(y)
            rows.append(dict(seed=item['seed'],week=item['week'],family=family,candidates=int(candidate.sum()),old_label_count=int(oo.sum()),corrected_label_count=int(nn.sum()),previously_dropped_scored=int((nn&~oo).sum()),old_mean_net_touch=float(old[oo].mean()) if oo.any() else None,corrected_mean_net_touch=float(y[nn].mean()) if nn.any() else None,**new[3]))
    p=OUT/'label_selection_diagnostics.csv'
    if p.exists():raise SystemExit('Preserving existing diagnostic output')
    with p.open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print('Saved',len(rows),'validation diagnostics; no models refitted.')
if __name__=='__main__':main()
