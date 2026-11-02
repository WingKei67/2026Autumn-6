"""Sequential grid over StrategyConfig params for one preset, dev stage. Usage:
python -m research.sweep improve_broker --count 20 --grid '{"quote_size":[3,5,10],"quote_refresh":[3,6],"skew_bps":[0,2,5]}'"""
import argparse, itertools, json, subprocess, sys
ap = argparse.ArgumentParser(); ap.add_argument('name'); ap.add_argument('--grid', required=True)
ap.add_argument('--count', type=int, default=20); ap.add_argument('--control'); ap.add_argument('--stage', default='dev')
ap.add_argument('--ticks', type=int, default=1500); ap.add_argument('--weeks', default='6,7,8,9,10')
a = ap.parse_args(); grid = json.loads(a.grid)
control = a.control or ('cash_broker' if a.name.endswith('broker') else 'cash_trader')
def run(names, params):
    subprocess.run([sys.executable, '-m', 'research.suite', 'batch', '--stage', a.stage, '--count', str(a.count), '--ticks', str(a.ticks),
                    '--workers', '4', '--weeks', a.weeks, '--names', names, '--params', json.dumps(params)], check=True, stdout=subprocess.DEVNULL)
run(control, {})
for vals in itertools.product(*grid.values()):
    run(a.name, dict(zip(grid.keys(), vals)))
