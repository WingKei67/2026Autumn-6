"""Decompose paired results: by week, long/short realized P&L (FIFO round trips from own fills), orders/rejects.
python -m research.analyze_trader --stage dev2 --a prod_event2_trader --b prod_baseline_trader [--sha ...]"""
import argparse, collections, csv, json, statistics as st
from research.paired import ROOT

def load(stage, name, sha):
    out = {}
    for r in {r['id']: r for r in csv.DictReader(open(ROOT / 'research/EXPERIMENT_REGISTRY.csv'))}.values():
        if r['stage'] == stage and r['status'] == 'completed' and r['source_hash'] == sha and r['name'] == name \
                and (r['stress'], r['population']) == ('base', 'mixed') and not json.loads(r['config']).get('params'):
            out[int(r['seed'])] = json.load(open(ROOT / r['artifact']))
    return out

def trips(fills, me='research_trader'):
    """FIFO realized P&L per symbol split by direction of the opening leg (stock only; excludes dividends/IPO grants)."""
    inv = collections.defaultdict(list); pnl = {'long': 0., 'short': 0.}; n = collections.Counter()
    for f in sorted(fills, key=lambda f: f.get('timestamp', 0)):
        side = 1 if f['buyer_id'] == me else -1
        q, px, sym = f['quantity'], f['price'], f['symbol']
        lots = inv[sym]
        while q and lots and (lots[0][0] > 0) != (side > 0):
            lq, lpx = lots[0]; m = min(q, abs(lq))
            kind = 'long' if lq > 0 else 'short'
            pnl[kind] += (px - lpx) * m * (1 if lq > 0 else -1); n[kind] += 1
            lots[0][0] += m if lq < 0 else -m; q -= m
            if lots[0][0] == 0: lots.pop(0)
        if q: lots.append([side * q, px])
    return pnl, n

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--stage', required=True); ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True); ap.add_argument('--sha')
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(open(ROOT / 'research/EXPERIMENT_REGISTRY.csv')) if r['stage'] == a.stage]
    sha = a.sha or rows[-1]['source_hash']
    A, B = load(a.stage, a.a, sha), load(a.stage, a.b, sha)
    seeds = sorted(set(A) & set(B)); print(len(seeds), 'paired seeds')
    byw = collections.defaultdict(list)
    for s in seeds:
        byw[A[s]['week']].append((A[s]['risk_adjusted'] - B[s]['risk_adjusted'], A[s]['pnl'] - B[s]['pnl']))
    for w in sorted(byw):
        x = byw[w]; print(f'week {w} n={len(x)} dScore {st.mean(d for d,_ in x):+.3f} dPnL {st.mean(p for _,p in x):+.0f}')
    dp = [A[s]['pnl'] - B[s]['pnl'] for s in seeds]
    print('dPnL mean %.0f median %.0f p10 %.0f p90 %.0f' % (st.mean(dp), st.median(dp), sorted(dp)[len(dp)//10], sorted(dp)[9*len(dp)//10]))
    for tag, D in (('A', A), ('B', B)):
        L = S = nl = ns = 0
        for s in seeds:
            p, n = trips(D[s]['fills_detail']); L += p['long']; S += p['short']; nl += n['long']; ns += n['short']
        mdd = [D[s]['max_drawdown'] for s in seeds]
        print(f"{tag}: realized long {L/len(seeds):+.0f}/run ({nl/len(seeds):.1f} lots) short {S/len(seeds):+.0f}/run ({ns/len(seeds):.1f} lots) | orders {st.mean(D[s]['orders'] for s in seeds):.1f} fills {st.mean(D[s]['fills'] for s in seeds):.1f} fees {st.mean(D[s]['fees'] for s in seeds):.0f} | mdd mean {st.mean(mdd):.4f} max {max(mdd):.4f} | gross mean {st.mean(D[s]['max_gross'] for s in seeds):.0f} | wire errors {dict(collections.Counter(k for s in seeds for k in D[s]['errors']))} | liq {sum(D[s]['liquidated'] for s in seeds)} acct {sum(bool(D[s]['accounting_errors']) for s in seeds)}")
if __name__ == '__main__': main()
