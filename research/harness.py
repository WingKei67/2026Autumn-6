"""Real exchange + actual seat wire dispatch. Deterministic transport, no network.

Run in a fresh process per experiment: exchange registry/config are global.
Only transport and halt timers are virtualized; no alternate fill model.
"""
from __future__ import annotations
import asyncio
from collections import deque, Counter
import json
import logging
import os
from pathlib import Path
import random
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path[:0] = [str(ROOT), str(ROOT / 'engine')]
# Do not load .env, even if this suite is later copied into a live checkout.
import shared.envfile
shared.envfile.load_env = lambda *a, **k: False
os.environ.update(EXCHANGE_URL='ws://127.0.0.1:1', ARENA_TOKEN='',
                  SEASON_PERSIST='false', GAME_WEEK='0')
import arena
import exchange.config as cfg
import broker.config as bc
import trader.config as tc
import team.trader as trader
from team.broker import MyBroker, _RiskBrokerBot
from exchange.scenario import load_week
from exchange.scoring import score_equity
import exchange.scoring as scoring
from exchange.server import ExchangeServer
from sim.arena import HeadlessArena
from sim.bots import make_population
from shared.messages import PlaceOrder, CancelOrder, IPOSubscribe, SessionEvent
from alpha_lab.exchange import OfflineExchange
from plugins.securities import defaults


class Wire:
    """Two serialized FIFO queues; bot uses the unchanged production listener."""
    def __init__(self, clock, delay=0.):
        self.incoming = deque(); self.outgoing = deque(); self.clock = clock
        self.delay = delay; self.closed = False; self.errors = Counter()
        self.fills = {}; self.orders = 0; self.quantity = 0
    async def send(self, raw):
        self.outgoing.append(json.loads(raw))
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.incoming or self.incoming[0][0] > self.clock[0] + 1e-8:
            raise StopAsyncIteration
        return self.incoming.popleft()[1]
    async def close(self): self.closed = True


class VenueSocket:
    def __init__(self, wire): self.wire = wire
    async def send(self, raw):
        w = self.wire; m = json.loads(raw)
        if m['type'] == 'error': w.errors[m['code']] += 1
        if m['type'] == 'trade_execution': w.fills[m['trade_id']] = m
        w.incoming.append((w.clock[0] + w.delay, raw))


class ResearchExchange(ExchangeServer):
    # Same halt state transitions with a venue-tick timer; full duration retained.
    _halt_symbol = OfflineExchange._halt_symbol
    _halt_all_symbols = OfflineExchange._halt_all_symbols
    resume_due = OfflineExchange.resume_due


class ResearchArena(HeadlessArena):
    """Fix preset cancellation shortcut in the research adapter, not the venue."""
    def cancel_all(self, bot, symbol=None, venue=None):
        v = 0 if venue is None else venue
        s = self.servers[v]
        for sym, book in s.books.items():
            if symbol and symbol != sym: continue
            for oid, order in list(book._orders.items()):
                if order.team_id == bot.team_id:
                    self.cancel_queue.append((bot, sym, oid, v))
        return 0
    async def flush_cancels(self):
        while self.cancel_queue:
            bot, sym, oid, v = self.cancel_queue.popleft()
            await self.servers[v]._handle_cancel_order(bot.clients[v],
                CancelOrder(team_id=bot.team_id, symbol=sym, order_id=oid), bot.team_id)
    async def order(self, *args, **kwargs):
        await self.flush_cancels()
        await super().order(*args, **kwargs)


async def run_one(name='baseline_trader', seed=1, week=6, ticks=1200,
                  population='mixed', stress='base', params=None):
    from research.strategies import make_candidate
    params = params or {}
    logging.disable(logging.CRITICAL)
    import trader.trader as te
    te._console.quiet = True
    clock = [1700000000.]
    random.seed(seed)
    defaults.FUNDAMENTAL_SEED = f'research:{seed}'
    defaults.FUNDAMENTAL_VOL = (0.5, 1., 2., 4.)[seed % 4] * (2 if stress == 'volatile' else 1)
    scenario = load_week(week)
    role = 'broker' if 'broker' in name else 'trader'
    target = 'research_' + role
    tc.TEAM_ID = bc.TEAM_ID = target
    cash = 350000. if role == 'broker' else 175000.
    spec = [('control', {'team_id': target, 'cash': cash})]
    presets = ['passive_mm','aggressive_mm','momentum','mean_reversion','noise']
    if population == 'thin': presets = ['passive_mm','noise']
    if population == 'competitive': presets += ['aggressive_mm','momentum','noise']
    for i, preset in enumerate(presets):
        spec.append((preset, {'team_id': f'opp_{i}', 'cash': 350000.}))
    with tempfile.TemporaryDirectory(prefix='rings-run-') as tmp:
        cfg.BASE_PRICES_PATH = str(Path(tmp)/'absent.json')
        cfg.REPORTS_DIR = tmp
        cfg.SEASON_PERSIST = 'false'
        cfg.RECORD_SESSIONS = False
        trader.STATE_DIR = Path(tmp)
        with patch('time.time', lambda: clock[0]), patch('time.monotonic', lambda: clock[0]):
            bots = make_population(spec)
            bots[0].role = role
            a = ResearchArena(bots, seed=seed, scenario=scenario, fire_shocks=False)
            a.cancel_queue = deque()
            s = a.server; s.__class__ = ResearchExchange; s.timers = []
            cfg.HALT_DURATION_SEC = a._orig_halt
            s.circuit_breaker.HALT_DURATION_SEC = a._orig_halt
            # Venue delays are outbound, not order-entry delays. Virtual FIFO implements them.
            delay = (a._orig_latency[0]/1000 if scenario.flag('latency_enabled') else 0.)
            if stress == 'latency': delay += 1.0
            if stress == 'fees': cfg.TAKER_FEE_RATE *= 2
            p = s.portfolios[target]; p.role = role
            wire = Wire(clock, delay); endpoint = VenueSocket(wire)
            s.clients[target] = endpoint
            owner, bot = make_candidate(name, params)
            bot._ws = wire
            if role == 'trader':
                bot.market = trader._RefMarket(owner._refs)
                owner.now = lambda: clock[0]
                bot._resync = bot._first_book = True
                bot._connects = 1
            listen = bot.listen if role == 'trader' else bot.listen_to_exchange
            requested = []
            async def drain():
                for _ in range(50):
                    await listen()
                    if not wire.outgoing: break
                    while wire.outgoing:
                        m = wire.outgoing.popleft(); typ = m['type']
                        if typ == 'place_order':
                            wire.orders += 1; wire.quantity += m['quantity']; requested.append(dict(m, tick=s.tick))
                            await s._handle_place_order(endpoint, PlaceOrder(**m), target)
                        elif typ == 'cancel_order':
                            await s._handle_cancel_order(endpoint, CancelOrder(**m), target)
                        elif typ == 'ipo_subscribe':
                            await s._handle_ipo_subscribe(endpoint, IPOSubscribe(**m), target)
                        else: raise AssertionError(f'Unsupported outbound {typ}')
                else: raise AssertionError('wire drain did not converge')
            async def public_data():
                for sym, book in s.books.items():
                    await s._send(endpoint, s._make_snapshot(sym, book))
                if s.tick % max(1, round(cfg.LEADERBOARD_INTERVAL_SEC)) == 0:
                    await s._send(endpoint, s._build_leaderboard())
                await drain()
            await s._send(endpoint, s._fee_schedule_event(cfg.refresh_venue_fees()))
            await s._send(endpoint, SessionEvent(event='WEEK_CHANGED', message='', data=scenario.to_dict()))
            await s._send(endpoint, s._build_leaderboard()); await drain()
            initial_cash = p.cash
            await s.open_session()
            curves = []; max_gross = 0.; quote_index = 0
            for step in range(ticks):
                clock[0] = 1700000000. + step
                await s.advance_tick(); await s.resume_due()
                a._absorb_new_listings()
                for hist in a.histories:
                    for sym in hist: hist[sym].append(a.ref(sym))
                for opponent in a.bots[1:]:
                    await opponent.act(a)
                    await a.flush_cancels()
                await public_data()
                # Polling clock (10 Hz trader / 2 Hz broker) is separate from 1 Hz venue.
                for sub in range(10):
                    clock[0] = 1700000000. + step + sub/10
                    if sub == 5:
                        for sym, book in s.books.items():
                            await s._send(endpoint, s._make_snapshot(sym, book))
                    await drain()
                    if wire.closed: break  # fail closed; reconnect is exercised in WS rehearsal
                    if role == 'trader' and bot._session_open:
                        signal = bot.strategy.generate_signal(bot.market, bot.portfolio)
                        if signal:
                            approved, _ = bot.risk.check_order(signal.symbol, signal.side, signal.quantity,
                                                              signal.price, bot.portfolio, bot.market)
                            if approved: await bot._place_order(signal)
                    elif role == 'broker' and sub in (0, 5):
                        symbols = list(bot._quote_symbols)
                        if symbols:
                            await bot.quote_symbol(symbols[quote_index % len(symbols)])
                            quote_index += 1
                    await drain()
                eq = p.net_worth(s.ref_prices)
                assert all(isinstance(q, int) for q in p.positions.values())
                # Mirror net_worth(): unpriced symbols (fresh IPO allocation) mark at avg_cost.
                def _mv(sym, q):
                    ref = s.ref_prices.get(sym, p.avg_cost.get(sym, 0.0))
                    if cfg.is_future(sym): return (ref - p.avg_cost.get(sym, ref)) * q
                    return q * ref
                assert abs(eq - (p.cash + sum(_mv(sym, q) for sym, q in p.positions.items()))) < 0.02
                gross = sum(abs(q)*s.ref_prices.get(sym,0) for sym,q in p.positions.items())
                max_gross = max(max_gross, gross)
                curves.append([s.tick, eq, gross])
            await s.close_session(persist=False)
            while s.auction_phase == 'preclose':
                clock[0] += 1; await s.advance_tick(); await drain()
            clock[0] += delay + 1
            await s._send(endpoint, s._build_leaderboard()); await drain()
            hist = s.equity_history[target]
            metrics = score_equity([v for _,v in hist])
            ownfills = [f for f in wire.fills.values() if target in (f['buyer_id'], f['seller_id'])]
            fees = sum(f['fee'] for f in ownfills if f.get('taker_id') == target)
            rebates = sum(f.get('maker_rebate',0) for f in ownfills if f.get('maker_id') == target)
            # Exact fill-based position identity after initial public grants. Capture grants from
            # venue grant records indirectly: all stock positions must equal grants + signed fills.
            final_eq = p.net_worth(s.ref_prices)
            row = next(r for r in (s._build_leaderboard().brokers if role=='broker' else s._build_leaderboard().traders)
                       if r['team_id']==target)
            accounting = []
            if role == 'trader': accounting = bot.ledger.reconcile(**bot._row_state(row))
            else:
                if bot.state.positions != p.positions: accounting.append('broker positions mismatch')
                if abs(bot.state.cash-p.cash)>0.02: accounting.append('broker cash mismatch')
            result = dict(name=name, seed=seed, week=week, ticks=ticks, population=population, stress=stress,
                params=params, role=role, **metrics, terminal_equity=final_eq,
                pnl=final_eq-hist[0][1], cash_start=initial_cash, fees=fees, rebates=rebates,
                exchange_fees=p.total_fees_paid, exchange_rebates=p.total_rebates_earned,
                fills=len(ownfills), orders=wire.orders,
                maker_ratio=sum(f.get('maker_id')==target for f in ownfills)/max(1,len(ownfills)),
                fill_ratio=sum(f['quantity'] for f in ownfills)/max(1,wire.quantity),
                turnover=sum(f['price']*f['quantity'] for f in ownfills),
                max_gross=max_gross, utilization=max_gross/cash, liquidated=p.liquidated,
                errors=dict(wire.errors), transport_closed=wire.closed, accounting_errors=accounting,
                equity_history=hist, curve=curves, fills_detail=ownfills, orders_detail=requested,
                opponents=[dict(name=b.preset, role=b.role, **score_equity([v for _,v in s.equity_history.get(b.team_id,[])])) for b in a.bots[1:]],
                runtime=dict(taker=cfg.TAKER_FEE_RATE, rebate=cfg.MAKER_REBATE_RATE,
                    snapshot_ticks=cfg.SEASON_SNAPSHOT_TICKS, risk_free=scoring.RISK_FREE_PER_SNAPSHOT,
                    vol_floor=scoring.VOL_FLOOR, penalty=scoring.SEASON_DRAWDOWN_PENALTY,
                    latency_seconds=delay, scenario=scenario.to_dict()))
            a.close()
            return result
