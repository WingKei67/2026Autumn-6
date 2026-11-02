"""Real local WebSocket engineering rehearsal; never a performance backtest.

Run one candidate at a time. A controlled counterparty exercises execution.
Default acceleration compresses venue ticks but does not remove venue latency or
halt timers. --realtime retains one-second venue clock and real bot timers.
"""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def bootstrap(week):
    runtime = Path(tempfile.mkdtemp(prefix="six-rings-ws-"))
    # Before any project import, prevent environment routing or live persistence.
    os.environ.update(EXCHANGE_URL="ws://127.0.0.1:1", EXCHANGE_URLS="ws://127.0.0.1:1",
        ARENA_TOKEN="", AUTH_REQUIRED="false", GAME_WEEK=str(week),
        SCENARIO_PATH=str(ROOT / f"season/week{week:02}.json"),
        SEASON_DIR=str(ROOT / "season"), SEASON_PERSIST="false",
        ROSTER_PATH=str(runtime / "no-roster.json"), SEASON_PATH=str(runtime / "season.json"),
        BASE_PRICES_PATH=str(runtime / "no-base.json"), RECORD_SESSIONS="false")
    sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
    return runtime


async def run(args, runtime):
    import websockets
    import exchange.config as cfg
    from exchange.server import ExchangeServer, Portfolio
    from shared.messages import PlaceOrder, CancelOrder
    import team.trader as trader
    import team.broker as broker
    from research.strategies import make_candidate
    from exchange.scoring import score_equity
    random.seed(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix('.log')
    logging.basicConfig(filename=log_path, filemode='w', level=logging.INFO, force=True)
    trader.STATE_DIR = runtime / 'trader_state'
    trader._config.TEAM_ID = 'research_ws_trader'
    broker.broker_config.TEAM_ID = 'research_ws_broker'
    trader._config.ARENA_TOKEN = broker.broker_config.ARENA_TOKEN = ''
    trader._config.TICK_INTERVAL_SEC = .1 if args.realtime else .005
    trader.BACKOFF_MIN_S = .05
    trader.BACKOFF_MAX_S = .2
    venue = ExchangeServer()
    cfg.SESSIONS_DIR = str(runtime / 'recordings')
    role = 'broker' if args.candidate.endswith('broker') else 'trader'
    seat = f'research_ws_{role}'
    starting = 350_000 if role == 'broker' else 175_000
    venue.portfolios[seat] = Portfolio(team_id=seat, role=role, level=1, cash=starting)
    cp = 'research_counterparty'
    venue.portfolios[cp] = Portfolio(team_id=cp, role='broker', level=1, cash=10_000_000)
    class Sink:
        def __init__(self): self.frames=[]
        async def send(self, raw): self.frames.append(json.loads(raw))
    sink=Sink()
    async with websockets.serve(venue.handle_client, '127.0.0.1', 0) as server:
        url = f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        trader._config.EXCHANGE_URL = url
        broker.broker_config.EXCHANGE_URL = url
        owner, bot = make_candidate(args.candidate)
        bot.exchange_url = url
        if role == 'trader':
            bot._state_path = runtime/'state.json'
            if not args.realtime: owner.now = lambda: float(venue.tick)
        task=asyncio.create_task(bot.run())
        connects=0
        try:
            for _ in range(200):
                ready = bot._has_server_snapshot if role=='trader' else bot._has_snapshot
                if ready: break
                await asyncio.sleep(.01)
            assert ready, 'initial account snapshot missing'
            await venue.open_session()
            preopen_orders=sum(len(b._orders) for b in venue.books.values())
            for index in range(args.ticks):
                await venue.advance_tick()
                # Counterparty creates real orders and executions. Engineering
                # fixtures are deliberately not used to estimate profit/rank.
                if venue.session_open and venue.auction_phase is None:
                    syms = [x for x in venue.books if not cfg.is_future(x)] if args.all_symbols else ['INTC']
                    for sym in syms:
                        mark=venue.ref_prices[sym]
                        for order in list(venue.books[sym]._orders.values()):
                            if order.team_id==cp:
                                await venue._handle_cancel_order(sink,CancelOrder(team_id=cp,symbol=sym,order_id=order.order_id),cp)
                        for side,price in [('buy',round(mark*.999,2)),('sell',round(mark*1.001,2))]:
                            await venue._handle_place_order(sink,PlaceOrder(team_id=cp,symbol=sym,side=side,
                                quantity=100,price=price,order_type='limit'),cp)
                    sym='INTC'; mark=venue.ref_prices[sym]
                    # An occasional taker tests resting orders without promised fills.
                    if role=='broker' and index%17==0:
                        side='sell' if (index//17)%2==0 else 'buy'
                        await venue._handle_place_order(sink,PlaceOrder(team_id=cp,symbol=sym,side=side,
                            quantity=2,price=round(mark*(.99 if side=='sell' else 1.01),2),order_type='ioc'),cp)
                for sym,book in venue.books.items():
                    await venue._broadcast(venue._make_snapshot(sym,book))
                await venue._broadcast(venue._build_leaderboard())
                await asyncio.sleep(1.0 if args.realtime else args.tick_seconds)
                if args.reconnect and index==args.ticks//2:
                    previous=bot._ws
                    await previous.close()
                    for _ in range(450):
                        if bot._ws is not previous and ((bot._has_server_snapshot and not bot._resync) if role=='trader' else bot._has_snapshot):
                            connects+=1; break
                        await asyncio.sleep(.01)
                    assert connects, 'forced reconnect did not restore account snapshot'
            await venue.close_session(persist=False)
            while venue.auction_phase=='preclose':
                await venue.advance_tick()
                await asyncio.sleep(1 if args.realtime else args.tick_seconds)
            await venue._broadcast(venue._build_leaderboard())
            await asyncio.sleep(.4)
            pf=venue.portfolios[seat]
            row=next(r for r in getattr(venue._build_leaderboard(),role+'s') if r['team_id']==seat)
            if role=='trader':
                errors=bot.ledger.reconcile(**bot._row_state(row))
                fills=len(bot._seen_trades); rejects=bot.rejects
                positions=bot.portfolio.positions; cash=bot.portfolio.cash
            else:
                errors=[]
                positions=bot.state.positions; cash=bot.state.cash
                fills=len(bot._seen_trades)
                rejects=sum('Exchange error [' in s for s in log_path.read_text().splitlines())
            if {s:q for s,q in positions.items() if q}!={s:q for s,q in pf.positions.items() if q}: errors.append('position mismatch')
            if abs(cash-pf.cash)>.01: errors.append(f'cash mismatch {cash-pf.cash}')
            resting=sum(1 for b in venue.books.values() for o in b._orders.values() if o.team_id==seat)
            path=venue.equity_history.get(seat,[])
            result=dict(candidate=args.candidate, week=args.week, seed=args.seed, ticks=venue.tick,
                mode='realtime' if args.realtime else 'accelerated', tick_seconds=1 if args.realtime else args.tick_seconds,
                bot_clock='wall' if args.realtime else 'trader venue tick; broker wall', fills=fills,
                rejects=rejects, forced_reconnects=connects, accounting_errors=errors,
                liquidated=pf.liquidated, after_close_resting_orders=resting,
                preopen_orders_all_seats=preopen_orders, cash=pf.cash, positions=pf.positions,
                score=score_equity([v for _, v in path]), log=str(log_path), runtime=str(runtime),
                limitations='Controlled liquidity; accelerated timing changes relative latency and cannot establish performance. Venue engine, wire adapters, matching, fees, risk, latency and halt timers unchanged.')
            args.output.write_text(json.dumps(result,indent=2))
            print(json.dumps(result,indent=2))
            assert not errors, errors
            assert not pf.liquidated
            assert resting==0, 'orders remain after session close'
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await task
            if bot._ws:
                with contextlib.suppress(Exception): await bot._ws.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',default='season_trader')
    p.add_argument('--week',type=int,default=6,choices=range(5,11))
    p.add_argument('--seed',type=int,default=7301)
    p.add_argument('--ticks',type=int,default=1100)
    p.add_argument('--tick-seconds',type=float,default=.03)
    p.add_argument('--realtime',action='store_true')
    p.add_argument('--all-symbols',action='store_true')
    p.add_argument('--extra-latency',type=float,default=0.0)
    p.add_argument('--reconnect',action='store_true')
    p.add_argument('--output',type=Path,default=ROOT/'research/results/ws_rehearsal.json')
    args=p.parse_args()
    runtime=bootstrap(args.week)
    asyncio.run(run(args,runtime))
if __name__=='__main__': main()
