"""Corrected diagnostic labels, kept separate from frozen evaluated models.

Intermediate halts must not select which signals are scored. IOC arrival
rejection has zero trading payoff. Exit during a halt waits until resumption.
Terminally censored observations remain NaN and are explicitly counted.
This is a quote-touch screen, not exchange execution or a deployable model.
"""
import numpy as np

def labels_with_halts(data,horizon):
    result=[np.full(len(data),np.nan) for _ in range(3)]
    stats=dict(eligible_decisions=0,entry_rejected=0,delayed_exits=0,terminal_censored=0,missing_entry_quote=0)
    for sid in np.unique(data[:,1]):
        idx=np.flatnonzero(data[:,1]==sid);a=data[idx];ticks=a[:,0]
        for i in range(len(a)):
            if a[i,5] or ticks[i]+horizon>ticks[-1]:continue
            stats['eligible_decisions']+=1
            j=np.searchsorted(ticks,ticks[i]+1)
            if j==len(a) or ticks[j]!=ticks[i]+1:
                stats['missing_entry_quote']+=1;continue
            if a[j,5]:
                stats['entry_rejected']+=1
                for v in result:v[idx[i]]=0.
                continue
            k=np.searchsorted(ticks,ticks[i]+horizon)
            delayed=k<len(a) and bool(a[k,5])
            while k<len(a) and a[k,5]:k+=1
            if k==len(a):stats['terminal_censored']+=1;continue
            if delayed:stats['delayed_exits']+=1
            result[0][idx[i]]=a[k,2]/a[j,2]-1
            result[1][idx[i]]=a[k,3]*.997/(a[j,4]*1.003)-1
            result[2][idx[i]]=(a[j,3]*.997-a[k,4]*1.003)/a[j,2]
    return (*result,stats)
