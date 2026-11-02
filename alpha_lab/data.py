"""Credential-free inventory and aggregated historical market observations."""
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
from statistics import median


def recording_paths(root):
    paths=[]
    for p in sorted(Path(root).glob('session*')):
        if p.suffix=='.jsonl' or (p.name.endswith('.jsonl.gz') and not Path(str(p)[:-3]).exists()): paths.append(p)
    return paths


def rows(path):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as f:
        for line in f:
            try: yield json.loads(line)
            except (ValueError,TypeError): continue


def inventory(root):
    result=[]
    for p in recording_paths(root):
        types=Counter(); events=Counter(); spreads=[]; symbols=set(); ticks=[]; last={}; moves=[]
        n=0
        for row in rows(p):
            n+=1; msg=row.get('msg',row)
            if not isinstance(msg,dict): continue
            kind=msg.get('type','unknown'); types[kind]+=1
            if kind=='session_event': events[msg.get('event','unknown')]+=1
            if kind=='book_snapshot':
                sym=msg.get('symbol'); symbols.add(sym)
                t=row.get('tick',0); ticks.append(t)
                mid=msg.get('mid_price')
                if mid and mid>0:
                    if msg.get('spread') is not None: spreads.append(msg['spread']/mid*10000)
                    if sym in last and t!=last[sym][0]: moves.append(abs(mid/last[sym][1]-1)*10000)
                    last[sym]=(t,mid)
        def q(values,p):
            if not values:return None
            ordered=sorted(values);return ordered[min(len(ordered)-1,int(p*len(ordered)))]
        result.append({'file':str(p),'rows':n,'types':dict(types),'events':dict(events),
            'symbols':sorted(s for s in symbols if s),'first_tick':min(ticks) if ticks else None,
            'last_tick':max(ticks) if ticks else None,'spread_bps_median':median(spreads) if spreads else None,
            'spread_bps_p95':q(spreads,.95),'absolute_move_bps_median':median(moves) if moves else None,
            'absolute_move_bps_p95':q(moves,.95),
            'limitations':('Includes partial order events, but incoming order semantics and complete queue history are not guaranteed.' if types['order_ack'] else 'Aggregated depth and public prints; no individual historical order/cancel IDs or reliable queue reconstruction.')})
    return result
