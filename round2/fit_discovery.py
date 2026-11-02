from round2.bootstrap import ROOT
from round2.features import FEATURES,labels
from round2.models import FAMILIES,mask,entry_mask,fit,predict,I
import numpy as np,json,csv
from pathlib import Path
OUT=ROOT/'round2/results'
CACHE={}
def load(path):
    if path not in CACHE:CACHE[path]=np.load(path)['data']
    return CACHE[path]
def ci(values):
    v=np.asarray(values);rng=np.random.default_rng(91731)
    if len(v)<2:return [None,None]
    return np.quantile(v[rng.integers(0,len(v),(2000,len(v)))].mean(1),[.025,.975]).tolist()
def save(path,rows):
    if not rows:return
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def screen():
    manifest=json.loads((OUT/'market_manifest.json').read_text());history=json.loads((OUT/'history_manifest.json').read_text())
    groups={'discovery':[r for r in manifest if r['seed']<4100],'validation':[r for r in manifest if r['seed']>=4100],'historical':history}
    cache={};screens=[];models=[];frozen={};sensitivity=[]
    training=np.concatenate([load(r['path'])[:,6:] for r in groups['discovery']])
    thresholds={name:(.01 if name in ['ann_remaining','peak_remaining','pct'] else float(np.quantile(np.abs(training[:,j]),.9))) for j,name in enumerate(FEATURES)}
    (OUT/'screen_thresholds.json').write_text(json.dumps(thresholds,indent=2))
    for group,files in groups.items():
        for horizon in [3,5,10,20,40,60,120]:
            sums={}
            for r in files:
                data=load(r['path']);v=data[:,6:];mid,long,short=labels(data,horizon)
                # Every fifth tick reduces mechanical duplication; inference clusters by seed/day.
                good=np.isfinite(mid)&(data[:,0]%5==0)&(data[:,1]<10)
                for j,name in enumerate(FEATURES):
                    if name in ['age','resume_age','spread','vol30']:continue
                    cond=good.copy()
                    if name in ['ann_remaining','peak_remaining','pct']:cond&=v[:,I['age']]<=120
                    x=v[cond,j];y=mid[cond]
                    if len(x)<10 or np.std(x)<1e-9:continue
                    corr=float(np.corrcoef(x,y)[0,1]);direction=np.sign(x)
                    payoff=np.where(direction>0,long[cond],short[cond])
                    # Public gap >1%; other predictors top decile from their OWN past-file
                    # only for descriptive screens, never used as trading thresholds.
                    threshold=thresholds[name]
                    selected=np.abs(x)>max(1e-9,threshold)
                    if not selected.any():continue
                    for rule in ['momentum','reversal']:
                        directional=payoff if rule=='momentum' else np.where(direction>0,short[cond],long[cond])
                        key=(name,horizon,rule);sums.setdefault(key,[]).append(dict(cluster=r.get('seed',r.get('week')),corr=corr,net=float(directional[selected].mean()),n=int(selected.sum())))
            for (name,h,rule),rows in sums.items():
                by_cluster={}
                for item in rows:by_cluster.setdefault(item['cluster'],[]).append(item['net'])
                cluster_net=[float(np.mean(v)) for v in by_cluster.values()]
                corr_cluster={}
                for item in rows:corr_cluster.setdefault(item['cluster'],[]).append(item['corr'])
                clo,chi=ci([np.mean(v) for v in corr_cluster.values()])
                lo,hi=ci(cluster_net);screens.append(dict(direction_rule=rule,correlation_ci_low=clo,correlation_ci_high=chi,split=group,feature=name,horizon=h,clusters=len(by_cluster),opportunities=sum(x['n'] for x in rows),mean_predictive_correlation=float(np.mean([x['corr'] for x in rows])),conditional_net_touch_return=float(np.mean(cluster_net)),ci_low=lo,ci_high=hi))
    save(OUT/'signal_screens.csv',screens)
    # Each family chooses horizon/ridge on validation later-touch net returns.
    # No test seed is loaded, and predictors include no forward field.
    for family in FAMILIES:
        choices=[]
        for h in [5,10,20,40]:
            datasets={}
            for split in ['discovery','validation']:
                xs=[];ys=[];clusters=[];ticks=[]
                for r in groups[split]:
                    a=load(r['path']);_,y,_=labels(a,h);v=a[:,6:]
                    keep=mask(v,family)&np.isfinite(y)&(a[:,0]%3==0)&(a[:,1]<10)
                    xs.append(v[keep]);ys.append(y[keep]);clusters.extend([r['seed']*10+r['week']]*int(keep.sum()));ticks.extend(a[keep,0])
                datasets[split]=(np.concatenate(xs),np.concatenate(ys),np.asarray(clusters),np.asarray(ticks))
            x,y,_,_=datasets['discovery']
            if len(y)<100:continue
            for ridge in [.01,.1]:
                model=fit(x,y,family,h,ridge);vx,vy,vc,vt=datasets['validation'];p=predict(vx,model)
                selected=entry_mask(vx,family)&(p>.002)
                # Non-overlapping opportunities per seed; use the best positive forecast
                # at each decision epoch; units are returns, not guaranteed portfolio P&L.
                values=[];count=0;seed_values={}
                for seed in np.unique(vc):
                    idx=np.flatnonzero((vc==seed)&selected);last=-10000;pay=[]
                    for t in np.unique(vt[idx]):
                        ks=idx[vt[idx]==t];k=ks[np.argmax(p[ks])]
                        if t-last>=h:pay.append(vy[k]);last=t
                    if pay:seed_values.setdefault(seed//10,[]).append(float(np.mean(pay)));count+=len(pay)
                values=[float(np.mean(v)) for v in seed_values.values()]
                low,high=ci(values);score=float(np.mean(values)) if values else -1.
                row=dict(family=family,horizon=h,ridge=ridge,validation_clusters=len(values),opportunities=count,mean_net_touch_return=score,ci_low=low,ci_high=high)
                sensitivity.append(row);model['validation']=row;choices.append(model)
        chosen=max(choices,key=lambda m:m['validation']['mean_net_touch_return'])
        chosen['supported_proxy']=bool(chosen['validation']['validation_clusters']>=10 and chosen['validation']['ci_low'] is not None and chosen['validation']['ci_low']>0)
        frozen[family]=chosen;print(family,chosen['validation'],'supported',chosen['supported_proxy'],flush=True)
    save(OUT/'model_sensitivity.csv',sensitivity);(OUT/'models_stockonly.json').write_text(json.dumps(frozen,indent=2))
if __name__=='__main__':screen()
