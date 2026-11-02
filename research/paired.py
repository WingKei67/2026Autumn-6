"""Paired comparison of registry results against a control, per stage.

Usage: python -m research.paired --stage dev --role trader --control cash_trader
Only rows from the CURRENT source hash with status=completed are used, and only
seeds where both candidate and control completed (paired). Bootstrap is over
independent seeds (runs), never ticks.
"""
from __future__ import annotations
import argparse, collections, csv, random, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(stage, sha=None):
    last = {}
    for r in csv.DictReader(open(ROOT / 'research/EXPERIMENT_REGISTRY.csv')):
        last[r['id']] = r
    out = collections.defaultdict(dict)
    for r in last.values():
        if r['stage'] != stage or r['status'] != 'completed': continue
        if sha and r['source_hash'] != sha: continue
        out[r['name']][int(r['seed'])] = dict(score=float(r['score']), pnl=float(r['pnl']),
                                              fees=float(r['fees'] or 0), rebates=float(r['rebates'] or 0),
                                              liq=r['liquidated'] == 'True', week=int(r['week']),
                                              dq=r['reason'].startswith('DISQ'))
    return out


def boot(d, n=4000, seed=7):
    rng = random.Random(seed)
    m = [st.mean(rng.choices(d, k=len(d))) for _ in range(n)]
    m.sort()
    return m[int(.025 * n)], m[int(.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='dev'); ap.add_argument('--role', default='trader')
    ap.add_argument('--control'); ap.add_argument('--sha')
    a = ap.parse_args()
    control = a.control or f'cash_{a.role}'
    rows = list(csv.DictReader(open(ROOT / 'research/EXPERIMENT_REGISTRY.csv')))
    sha = a.sha or [r for r in rows if r['stage'] == a.stage][-1]['source_hash']
    data = load(a.stage, sha)
    base = data[control]
    print(f"{'name':20s}{'n':>4}{'dScore':>9}{'95% CI':>20}{'P(>0)':>7}{'dPnL':>8}{'worstD':>8}{'liq':>4}{'dq':>3}{'fees':>7}{'reb':>7}")
    for name, d in sorted(data.items()):
        if not name.endswith(a.role): continue
        seeds = [s for s in d if s in base]
        if len(seeds) < 2: continue
        ds = [d[s]['score'] - base[s]['score'] for s in seeds]
        dp = [d[s]['pnl'] - base[s]['pnl'] for s in seeds]
        lo, hi = boot(ds)
        print(f"{name:20s}{len(seeds):4d}{st.mean(ds):+9.3f}  [{lo:+.3f},{hi:+.3f}]{sum(x > 0 for x in ds)/len(ds):7.2f}"
              f"{st.mean(dp):+8.0f}{min(ds):+8.2f}{sum(d[s]['liq'] for s in seeds):4d}{sum(d[s]['dq'] for s in seeds):3d}"
              f"{st.mean(d[s]['fees'] for s in seeds):7.1f}{st.mean(d[s]['rebates'] for s in seeds):7.1f}")


if __name__ == '__main__':
    main()
