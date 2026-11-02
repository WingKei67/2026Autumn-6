"""Small regularized predictors, causal feature transforms shared with execution."""
import numpy as np
from round2.features import FEATURES
I={n:i for i,n in enumerate(FEATURES)}
FAMILIES=['event_regime','overshoot_reversal','cross_asset']
def mask(v,family):
    age=v[...,I['age']]
    if family=='event_regime':return (age>=0)&(age<=120)&(np.abs(v[...,I['pct']])>.005)
    if family=='overshoot_reversal':return (age>=10)&(age<=120)&(v[...,I['pct']]<-.005)
    return np.abs(v[...,I['peer3']])>.001

def entry_mask(v,family):
    m=mask(v,family)
    if family=='event_regime':return m&(v[...,I['age']]<=10)
    if family=='overshoot_reversal':return m&(v[...,I['ann_remaining']]>.002)
    return m

def design(v,family):
    v=np.asarray(v);cols=[]
    if family in ['event_regime','overshoot_reversal']:
        for name in ['ann_remaining','peak_remaining','pct','r1','r3','r10','micro','imbalance','vol30','spread','trade_flow']:
            cols.append(v[...,I[name]])
        age=v[...,I['age']]
        for lo,hi in [(0,5),(6,15),(16,30),(31,120)]:
            phase=((age>=lo)&(age<=hi)).astype(float)
            cols.extend([phase,phase*v[...,I['ann_remaining']],phase*v[...,I['pct']]])
    else:
        for name in ['r1','r3','r10','peer1','peer3','peer10','micro','imbalance','vol30','spread','trade_flow']:
            cols.append(v[...,I[name]])
        cols.extend([v[...,I['peer3']]-v[...,I['r3']],v[...,I['peer10']]-v[...,I['r10']]])
    return np.stack(cols,axis=-1)

def fit(x,y,family,horizon,ridge):
    z=design(x,family);mu=z.mean(0);sd=z.std(0);sd[sd<1e-8]=1.
    z=(z-mu)/sd;z=np.c_[np.ones(len(z)),z]
    penalty=np.eye(z.shape[1])*len(z)*ridge;penalty[0,0]=0.
    beta=np.linalg.solve(np.einsum('ni,nj->ij',z,z)+penalty,np.einsum('ni,n->i',z,y))
    return dict(family=family,horizon=horizon,ridge=ridge,mean=mu.tolist(),scale=sd.tolist(),beta=beta.tolist(),train_rows=len(y),train_rmse=float(np.sqrt(np.mean((np.einsum('ni,i->n',z,beta)-y)**2))))

def predict(v,model):
    x=(design(v,model['family'])-np.asarray(model['mean']))/np.asarray(model['scale'])
    return np.asarray(model['beta'])[0]+np.einsum('...i,i->...',x,np.asarray(model['beta'])[1:])
