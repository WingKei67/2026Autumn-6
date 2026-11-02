"""Family C: do public book features predict forward mid returns net of costs?
Observation-only (cash_trader, no orders). Features at tick t use only data <= t;
forward returns are mid[t+h]/mid[t]-1. One row per (seed,symbol,tick). Report
Spearman-style rank correlation by seed so uncertainty is across independent runs."""
import asyncio, json, statistics as st, sys
from collections import defaultdict
from research.harness import run_one

def study(seed, week, ticks=1200, H=(5, 20)):
    mids = defaultdict(list); feat = defaultdict(list)
    def probe(s, step):
        for sym, b in s.books.items():
            bid, ask = b.best_bid(), b.best_ask()
            if not bid or not ask: mids[sym].append(None); feat[sym].append(None); continue
            m = (bid+ask)/2; snap = b.get_snapshot(depth=3)
            bq = sum(x['quantity'] if isinstance(x, dict) else x[1] for x in snap['bids'][:3])
            aq = sum(x['quantity'] if isinstance(x, dict) else x[1] for x in snap['asks'][:3])
            imb = (bq-aq)/max(1, bq+aq)
            micro = (ask*bq+bid*aq)/max(1, bq+aq)
            mids[sym].append(m); feat[sym].append(dict(imb=imb, micro=(micro-m)/m, spr=(ask-bid)/m))
    asyncio.run(run_one('cash_trader', seed, week, ticks, probe=probe))
    rows = []
    for sym in mids:
        m, f = mids[sym], feat[sym]
        for t in range(20, len(m)-max(H)):
            if m[t] is None or f[t] is None or any(m[t+h] is None for h in H) or any(m[t-k] is None for k in (1, 5, 20)): continue
            r = dict(sym=sym, **f[t], rev5=m[t]/m[t-5]-1, rev20=m[t]/(sum(x for x in m[t-20:t] if x)/20)-1)
            for h in H: r[f'f{h}'] = m[t+h]/m[t]-1
            rows.append(r)
    return rows

def corr(x, y):
    mx, my = st.mean(x), st.mean(y)
    sx = sum((a-mx)**2 for a in x)**.5; sy = sum((b-my)**2 for b in y)**.5
    return sum((a-mx)*(b-my) for a, b in zip(x, y))/(sx*sy) if sx and sy else 0.

if __name__ == '__main__':
    seeds = list(range(11, 11+int(sys.argv[1] if len(sys.argv) > 1 else 10)))
    out = defaultdict(lambda: defaultdict(list)); cost = []
    for i, sd in enumerate(seeds):
        rows = study(sd, 6+i % 5)
        cost.append(st.mean(r['spr'] for r in rows))
        for feat in ('imb', 'micro', 'rev5', 'rev20'):
            for h in (5, 20):
                out[feat][h].append(corr([r[feat] for r in rows], [r[f'f{h}'] for r in rows]))
    print('mean relative spread', st.mean(cost), 'round-trip taker fees', 2*0.0015)
    for feat in out:
        for h, v in out[feat].items():
            print(f'{feat:6s} h={h:2d} mean corr {st.mean(v):+.4f}  se {st.stdev(v)/len(v)**.5:.4f}  pos {sum(c>0 for c in v)}/{len(v)}')

def buckets(n=12):
    allrows = []
    for i, sd in enumerate(range(11, 11+n)):
        for r in study(sd, 6+i % 5): r['seed'] = sd; allrows.append(r)
    for lo, hi in [(0, .0005), (.0005, .001), (.001, .002), (.002, .004), (.004, .01), (.01, 1)]:
        sel = [r for r in allrows if lo <= abs(r['rev5']) < hi]
        mom = [r['f5']*(1 if r['rev5'] > 0 else -1) for r in sel]
        by = defaultdict(list)
        for r, x in zip(sel, mom): by[r['seed']].append(x)
        seedmeans = [st.mean(v) for v in by.values()]
        print(f'|rev5| in [{lo},{hi}) n={len(sel):6d} mean signed fwd5 {st.mean(mom)*1e4:+.2f}bp  seedmean {st.mean(seedmeans)*1e4:+.2f}bp (pos {sum(x>0 for x in seedmeans)}/{len(seedmeans)})  rel.spread {st.mean(r["spr"] for r in sel)*1e4:.1f}bp')
