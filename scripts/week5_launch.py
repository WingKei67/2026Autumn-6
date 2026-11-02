"""Supervise the three real seats. Starts live connections ONLY with --live."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEATS = ('6_rings_trader_1', '6_rings_trader_2', '6_rings_broker')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live', action='store_true', help='connect the real bots and allow live orders')
    args = ap.parse_args()
    if not args.live:
        print('Prepared seats: ' + ', '.join(SEATS))
        print('No connections made. Start graded trading with --live.')
        return
    children = {}
    restarts = {seat: 0 for seat in SEATS}
    def launch(seat):
        env = dict(os.environ, TEAM_ID=seat, TICK_INTERVAL='0.1', PYTHONUNBUFFERED='1')
        module = 'team.broker' if seat.endswith('broker') else 'team.trader'
        return subprocess.Popen([sys.executable, '-m', module], cwd=ROOT, env=env)
    def stop(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        for seat in SEATS:
            children[seat] = launch(seat)
        while True:
            time.sleep(1)
            for seat, child in list(children.items()):
                if child.poll() is not None:
                    restarts[seat] += 1
                    print(f'{seat} exited={child.returncode}, restart={restarts[seat]}', flush=True)
                    if restarts[seat] > 5:
                        raise RuntimeError(f'{seat}: repeated crashes; inspect seat log')
                    time.sleep(2)
                    children[seat] = launch(seat)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children.values():
            if child.poll() is None: child.terminate()
        for child in children.values():
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()


if __name__ == '__main__':
    main()
