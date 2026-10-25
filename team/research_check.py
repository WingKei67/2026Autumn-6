"""Exploratory evidence check for supplied historical 5-second sampled CSVs.

DO NOT call these live fill results. This estimates 120-second taker/taker
round trips using observed bid/ask points, ignoring queue effects and
without claiming stationarity or independence of overlapping files.
"""
from __future__ import annotations
import io, zipfile
from pathlib import Path
import importlib.util,sys
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent
import os
candidates = [HERE, HERE.parent, Path('/mnt/data/arena_source/2026Autumn-6')]
SRC = Path(os.environ['ARENA_SOURCE_ROOT']) if os.environ.get('ARENA_SOURCE_ROOT') else next(
    (root for root in candidates if (root/'engine'/'shared').is_dir() and (root/'arena').is_dir()), HERE)
sys.path[:0]=[str(SRC/'engine'),str(SRC)]
spec=importlib.util.spec_from_file_location('sixrings_research',HERE/'trader.py')
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)

import sys
ARCHIVE=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/mnt/data/6_Rings_Week4_Implemented.zip')
HORIZON=24  # 24 samples, ~120 seconds; live bot samples every 0.5s
records=[]
z=zipfile.ZipFile(ARCHIVE)
for filename in z.namelist():
    if not filename.endswith('.csv'):continue
    d=pd.read_csv(io.BytesIO(z.read(filename)))
    d=d.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    d['segment']=(d.timestamp.diff().fillna(5)>12).cumsum()
    for _,sub in d.groupby('segment'):
        if len(sub) < 90:continue
        for sym in mod.MyTrader.ALPHA_SYMBOLS:
            mid=sub[f'{sym}_mid'].to_numpy(dtype=float)
            bid=sub[f'{sym}_bid'].to_numpy(dtype=float)
            ask=sub[f'{sym}_ask'].to_numpy(dtype=float)
            imb=sub[f'{sym}_imbalance'].to_numpy(dtype=float)
            if not np.all(np.isfinite(mid)) or np.min(mid)<=0:continue
            for i in range(54,len(sub)-HORIZON,4):
                feat=mod.MyTrader._features(mid[:i+1].tolist(),imb[i])
                if feat is None:continue
                proxy,sigma,fast,momentum,zscore=feat
                spread=(ask[i]-bid[i])/mid[i]
                hurdle=0.006+spread+0.0015+sigma
                signal=proxy>hurdle and momentum>0 and zscore<2.6 and spread<0.002
                future_bid=bid[i+HORIZON]
                if min(ask[i],future_bid)<=0:continue
                net_return=future_bid*(1-.003)/(ask[i]*(1+.003))-1
                # Denote truly discontinuous event steps separately.
                clean=abs(mid[i+HORIZON]/mid[i]-1)<.15
                records.append((filename.split('/')[-1],sym,signal,net_return,clean))

a=pd.DataFrame(records,columns=['file','symbol','signal','net_return','clean'])
print('RESEARCH DATA exploratory only (not live backtest; 5-second sample, 120s hold)')
print('nonindependent sampled windows',len(a),'distinct files',a.file.nunique())
for name, d in [('all',a),('exclude >15% moves',a[a.clean])]:
   sel=d[d.signal]
   print(name,'candidate entries',len(sel),'win rate',round((sel.net_return>0).mean()*100,1) if len(sel) else None,
         'mean cost-adjusted return pct',round(sel.net_return.mean()*100,3) if len(sel) else None,
         'baseline all mean pct',round(d.net_return.mean()*100,3) if len(d) else None)
   if len(sel):print(sel.groupby('file').net_return.agg(['count','mean']).to_string())
