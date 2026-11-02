"""Selection-aware paired comparison of many configs vs a reference (Westfall-Young style max-t bootstrap).

Seeds are the resampling unit and are shared across configs, so cross-config correlation is preserved.
Reports unadjusted mean/CI, and the family-wise adjusted p-value of each config: P(max_j t*_j >= t_obs_j)
under the null obtained by centering each config's paired deltas.
usage: python -m research.maxboot --stage dev --role broker --family improve_broker --ref cash_broker [--sha ..] [--min-n 15]
"""
import argparse, csv, json, random, statistics as st
from research.paired import load, ROOT

def tstat(x):
    n = len(x); s = st.stdev(x) if n > 1 else 0
    return st.mean(x) / (s / n**.5) if s > 0 else 0.

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--stage', default='dev'); ap.add_argument('--family', required=True)
    ap.add_argument('--ref', required=True); ap.add_argument('--sha'); ap.add_argument('--min-n', type=int, default=15)
    ap.add_argument('--B', type=int, default=5000); ap.add_argument('--metric', default='score', choices=['score', 'pnl'])
    a = ap.parse_args()
    rows = list(csv.DictReader(open(ROOT / 'research/EXPERIMENT_REGISTRY.csv')))
    sha = a.sha or [r for r in rows if r['stage'] == a.stage][-1]['source_hash']
    data = load(a.stage, sha)
    ref = data[a.ref]
    cfgs = {k: v for k, v in data.items() if k.startswith(a.family)}
    seeds = sorted(set.intersection(*[set(v) & set(ref) for v in cfgs.values()]))
    print(f'{len(cfgs)} configs, {len(seeds)} common seeds (seed intersection, not survivors-only per config)')
    D = {k: [v[s][a.metric] - ref[s][a.metric] for s in seeds] for k, v in cfgs.items()}
    obs = {k: tstat(x) for k, x in D.items()}
    rng = random.Random(11); maxes = []
    cent = {k: [d - st.mean(x) for d in x] for k, x in D.items()}
    for _ in range(a.B):
        idx = [rng.randrange(len(seeds)) for _ in seeds]
        maxes.append(max(tstat([cent[k][i] for i in idx]) for k in cent))
    maxes.sort(); crit = maxes[int(.95 * a.B)]
    print(f'max-t 95% critical value {crit:.2f}')
    for k in sorted(D, key=lambda k: -obs[k]):
        p = sum(m >= obs[k] for m in maxes) / a.B
        print(f'{k[:70]:72s} mean {st.mean(D[k]):+.3f} t {obs[k]:+.2f} adj.p {p:.3f} {"*" if p<.05 else ""}')

if __name__ == '__main__': main()
