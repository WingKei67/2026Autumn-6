"""Production rehearsal: the REAL Trader 1 process (`python -m team.trader`) against an ISOLATED local exchange.

Never connects to the hosted arena: EXCHANGE_URL is forced to a 127.0.0.1 port opened by this script, no .env is
read from the repo (asserted), tokens are blank. The venue is the real ExchangeServer; a controlled counterparty
supplies liquidity in every share symbol. This is ENGINEERING evidence (startup, wire handling, order lifecycle,
risk limits, reconnect, shutdown); it is not a performance estimate.

usage: python -m research.process_rehearsal --strategy baseline|event2 --week 6 --ticks 700 --tick-seconds 1.0 \
           --drop-at 350 --output research/results/proc_baseline_w6.json
"""
from __future__ import annotations
import argparse, asyncio, json, os, random, signal, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAT = '6_rings_trader_1'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--strategy', default='baseline', choices=['baseline', 'event2'])
    ap.add_argument('--week', type=int, default=6, choices=range(5, 11))
    ap.add_argument('--ticks', type=int, default=700)
    ap.add_argument('--tick-seconds', type=float, default=1.0)
    ap.add_argument('--drop-at', type=int, default=-1, help='venue tick at which the server drops the bot socket')
    ap.add_argument('--seed', type=int, default=9001)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    assert not (ROOT / '.env').exists(), 'refusing to run with a .env present (possible live routing)'
    runtime = Path(tempfile.mkdtemp(prefix='six-rings-proc-'))
    os.environ.update(EXCHANGE_URL='ws://127.0.0.1:1', EXCHANGE_URLS='ws://127.0.0.1:1', ARENA_TOKEN='',
        AUTH_REQUIRED='false', GAME_WEEK=str(a.week), SCENARIO_PATH=str(ROOT / f'season/week{a.week:02}.json'),
        SEASON_DIR=str(ROOT / 'season'), SEASON_PERSIST='false', ROSTER_PATH=str(runtime / 'no-roster.json'),
        SEASON_PATH=str(runtime / 'season.json'), BASE_PRICES_PATH=str(runtime / 'no-base.json'), RECORD_SESSIONS='false')
    sys.path[:0] = [str(ROOT), str(ROOT / 'engine')]
    asyncio.run(run(a, runtime))


async def run(a, runtime):
    import websockets
    import exchange.config as cfg
    from exchange.server import ExchangeServer, Portfolio
    from exchange.scoring import score_equity
    from shared.messages import PlaceOrder, CancelOrder
    random.seed(a.seed)
    cfg.SESSIONS_DIR = str(runtime / 'recordings')
    venue = ExchangeServer()
    venue.portfolios[SEAT] = Portfolio(team_id=SEAT, role='trader', level=1, cash=175_000)
    cp = 'research_counterparty'
    venue.portfolios[cp] = Portfolio(team_id=cp, role='broker', level=1, cash=10_000_000)

    class Sink:
        async def send(self, raw): pass
    sink = Sink()
    state = ROOT / 'team/state' / f'{SEAT}.json'
    if state.exists(): state.unlink()           # fresh ledger: this is a cold start
    lock = ROOT / 'logs' / f'{SEAT}.lock'
    t_start = time.time()
    res = dict(strategy=a.strategy, week=a.week, ticks=a.ticks, tick_seconds=a.tick_seconds, drop_at=a.drop_at,
               checks={}, notes=[])
    async with websockets.serve(venue.handle_client, '127.0.0.1', 0) as server:
        url = f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        env = dict(os.environ, EXCHANGE_URL=url, EXCHANGE_URLS=url, TEAM_ID=SEAT, ARENA_TOKEN='',
                   TRADER_STRATEGY=a.strategy, NO_COLOR='1', PYTHONUNBUFFERED='1')
        for k in ('EVENT2_CAP_SHARES',): env.pop(k, None)
        out = open(runtime / 'trader_stdout.log', 'w')
        proc = subprocess.Popen([sys.executable, '-m', 'team.trader'], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
        # --- startup: the bot must connect and receive its account snapshot before the session opens
        for _ in range(300):
            if SEAT in venue.clients: break
            await asyncio.sleep(0.05)
        res['checks']['connected_before_open'] = SEAT in venue.clients
        # second instance of the same seat must be refused by the local seat lock
        dup = subprocess.run([sys.executable, '-m', 'team.trader'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        res['checks']['duplicate_seat_refused'] = dup.returncode != 0 and 'owns seat' in (dup.stderr + dup.stdout)
        await asyncio.sleep(0.5)
        await venue.open_session()
        maxpos = {}; minpos = {}; reconnects_seen = 0; was_connected = True; pos_hist = []
        dropped = False; rejects_seen = []
        for index in range(a.ticks):
            await venue.advance_tick()
            if venue.session_open and venue.auction_phase is None:
                for sym in [x for x in venue.books if not cfg.is_future(x)]:
                    mark = venue.ref_prices[sym]
                    for order in list(venue.books[sym]._orders.values()):
                        if order.team_id == cp:
                            await venue._handle_cancel_order(sink, CancelOrder(team_id=cp, symbol=sym, order_id=order.order_id), cp)
                    for side, price in [('buy', round(mark * .999, 2)), ('sell', round(mark * 1.001, 2))]:
                        await venue._handle_place_order(sink, PlaceOrder(team_id=cp, symbol=sym, side=side,
                            quantity=100, price=price, order_type='limit'), cp)
            for sym, book in venue.books.items():
                await venue._broadcast(venue._make_snapshot(sym, book))
            await venue._broadcast(venue._build_leaderboard())
            p = venue.portfolios[SEAT]
            for s, q in p.positions.items():
                if cfg.is_future(s): continue
                maxpos[s] = max(maxpos.get(s, 0), q); minpos[s] = min(minpos.get(s, 0), q)
            connected = SEAT in venue.clients
            if was_connected and not connected: reconnects_seen += 1
            was_connected = connected
            if a.drop_at >= 0 and index == a.drop_at and not dropped and SEAT in venue.clients:
                dropped = True
                await venue.clients[SEAT].close()
                res['notes'].append(f'server dropped bot socket at venue tick {venue.tick}')
            if proc.poll() is not None:
                res['notes'].append(f'trader process exited early rc={proc.returncode} at tick {venue.tick}')
                break
            await asyncio.sleep(a.tick_seconds)
        await venue.close_session(persist=False)
        while venue.auction_phase == 'preclose':
            await venue.advance_tick(); await asyncio.sleep(a.tick_seconds)
        await venue._broadcast(venue._build_leaderboard())
        await asyncio.sleep(2.0)
        resting = sum(1 for b in venue.books.values() for o in b._orders.values() if o.team_id == SEAT)
        pf = venue.portfolios[SEAT]
        # --- shutdown: SIGINT must end the process promptly
        alive_before = proc.poll() is None
        proc.send_signal(signal.SIGINT)
        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill(); rc = 'killed-after-20s'
        res['checks']['clean_shutdown_on_sigint'] = alive_before and (rc in (0, -2, 130, 1) and rc != 'killed-after-20s')
        res['shutdown_rc'] = rc
        out.close()
        # --- inspect the bot's own log + persisted ledger
        logs = sorted((ROOT / 'logs').glob(f'{SEAT}_*.log'), key=lambda x: x.stat().st_mtime)
        text = logs[-1].read_text() if logs else ''
        for key, needle in [('connect_logged', 'CONNECT #1'), ('order_sent', 'Order sent'), ('ack', 'ACK '), ('fill', 'FILL'),
                            ('ioc_complete', 'IOC COMPLETE'), ('reconnect_logged', 'CONNECT #2'), ('book_resync', 'RESYNC')]:
            res['checks'][key] = needle in text
        res['reject_lines'] = [l for l in text.splitlines() if 'REJECT #' in l][:8]
        blocks = (text + '\n' + (runtime / 'trader_stdout.log').read_text()).split('Traceback')[1:]
        # the operator-interrupt (SIGINT) shutdown prints a KeyboardInterrupt traceback; anything else is a crash
        res['traceback_in_log'] = any('KeyboardInterrupt' not in b.split('\n\n')[0] and 'KeyboardInterrupt' not in b[:3000] for b in blocks)
        res['keyboardinterrupt_on_shutdown'] = any('KeyboardInterrupt' in b[:3000] for b in blocks)
        led = json.loads(state.read_text()) if state.exists() else {}
        res['ledger_persisted'] = bool(led)
        res['ledger_cash'] = led.get('cash'); res['exchange_cash'] = pf.cash
        res['ledger_positions'] = {k: v for k, v in (led.get('positions') or {}).items() if v}
        res['exchange_positions'] = {k: v for k, v in pf.positions.items() if v}
        res['max_long_by_symbol'] = {k: v for k, v in maxpos.items() if v > 0}
        res['max_short_by_symbol'] = {k: v for k, v in minpos.items() if v < 0}
        cap = 150
        res['checks']['seat_cap_never_exceeded'] = all(v <= cap for v in maxpos.values()) and all(-v <= cap for v in minpos.values())
        res['checks']['no_orders_resting_after_close'] = resting == 0
        res['checks']['not_liquidated'] = not pf.liquidated
        res['checks']['lock_released'] = True
        try:
            import fcntl
            h = lock.open('a'); fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB); h.close()
        except Exception:
            res['checks']['lock_released'] = False
        if led:
            res['checks']['ledger_matches_exchange'] = (abs((led.get('cash') or 0) - pf.cash) < 1.0 and res['ledger_positions'] == res['exchange_positions'])
        if a.drop_at >= 0:
            # The bot reconnects within ~1s, faster than the once-per-tick poll can see: judge by the bot's OWN log.
            res['checks']['reconnected_after_drop'] = ('DISCONNECT' in text and 'CONNECT #2' in text
                                                       and text.count('RESYNC from leaderboard') >= 2)
        res['score'] = score_equity([v for _, v in venue.equity_history.get(SEAT, [])])
        res['fills_exchange'] = sum(1 for _ in [])  # fills counted in the bot log
        res['bot_log'] = str(logs[-1]) if logs else None
        res['wall_seconds'] = round(time.time() - t_start)
    args = a.output; args.parent.mkdir(parents=True, exist_ok=True)
    args.write_text(json.dumps(res, indent=2, default=str))
    required = ['connected_before_open', 'duplicate_seat_refused', 'clean_shutdown_on_sigint', 'seat_cap_never_exceeded',
                'no_orders_resting_after_close', 'not_liquidated', 'lock_released']
    if a.drop_at >= 0: required += ['reconnected_after_drop']
    if res['ledger_persisted']: required += ['ledger_matches_exchange']
    if res['checks'].get('order_sent'): required += ['ack', 'ioc_complete']
    res['required_checks'] = {k: res['checks'].get(k) for k in required}
    ok = all(res['checks'].get(k) for k in required) and not res['traceback_in_log']
    print(json.dumps(res, indent=2, default=str)); print('RESULT:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
