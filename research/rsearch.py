"""Random search over StrategyConfig params on the first N dev seeds (screening), paired with the cash control.
python -m research.rsearch event2_trader --n 24 --count 20 --seed 5"""
import argparse, json, random, subprocess, sys
SPACE = {'ev_notional': [.1, .2, .35, .5], 'ev_entry': [.3, .5, .7, .9], 'ev_take': [.6, .8, .9, 1.0],
         'ev_window': [.3, .5, 1., 1.5], 'ev_chunk': [60, 100, 200], 'ev_time': [2., 3., 5.],
         'ev_stop': [.3, .5, 1.], 'cap_shares': [400, 700], 'gross_cap': [140000., 170000.], 'ev_maxsyms': [3, 4, 6]}
ap = argparse.ArgumentParser(); ap.add_argument('name'); ap.add_argument('--n', type=int, default=24)
ap.add_argument('--count', type=int, default=20); ap.add_argument('--seed', type=int, default=5); ap.add_argument('--stage', default='dev')
a = ap.parse_args(); rng = random.Random(a.seed)
def run(names, params):
    subprocess.run([sys.executable, '-m', 'research.suite', 'batch', '--stage', a.stage, '--count', str(a.count), '--ticks', '1500',
                    '--workers', '4', '--names', names, '--params', json.dumps(params)], check=True, stdout=subprocess.DEVNULL)
run('cash_trader', {}); run(a.name, {})
for _ in range(a.n):
    run(a.name, {k: rng.choice(v) for k, v in SPACE.items()})
