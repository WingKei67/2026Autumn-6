"""6 Rings / AlgoArena: aggressive, execution-aware, portfolio-risk-managed trader.

Compatible with the Arena SDK supplied in the 2026Autumn-6 course archive.

IMPORTANT
---------
* Trading is long-only; shorting requires different margin/risk controls.
* IOC orders deliberately sacrifice maker rebates to avoid stale resting orders.
* The strategy requires an exchange PortfolioUpdate before trading; no startup
  cash or empty-position placeholder is trusted.
* Price-based signals are hypotheses, not calibrated predictive probabilities.
* All allocation/risk constants are configurable research parameters.

Run: TEAM_ID=6_rings_trader_1 python -m team.trader
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from arena import Signal, Trader
from arena.trader import _AdaptedTraderBot
from shared.messages import (
    BookSnapshot, ErrorMsg, Leaderboard, OrderAck, PlaceOrder,
    PortfolioUpdate, SessionEvent, TradeExecution, parse_message,
)

LOG = logging.getLogger('six_rings_v4')


@dataclass
class PendingIOC:
    symbol: str
    side: str
    quantity: int
    submitted_tick: int
    reason: str
    filled: int = 0
    acknowledged_tick: int | None = None


@dataclass
class ManagedPosition:
    """Cost basis for *our confirmed fills* only; no fictional legacy basis."""
    quantity: int = 0
    average_price: float = 0.0
    first_tick: int = 0
    peak: float = 0.0
    reason: str = ''


@dataclass
class EventTrade:
    symbol: str
    start_tick: int
    kind: str
    pct: float
    reference: float
    trough: float
    entered: bool = False


class MyTrader(Trader):
    VERSION = '6rings-aggressive-v4.2-ioc'
    # Wide discovery universe: inventory is always monitored even if it is
    # outside this list. Higher spread names typically fail the cost gate.
    ALPHA_SYMBOLS = (
        'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN',
        'GOOGL', 'META', 'AMD', 'INTC', 'NFLX',
    )
    MAX_GROSS = 0.80
    TARGET_GROSS = 0.65
    MAX_WEIGHT = 0.12
    MAX_NEW_ORDER_SHARES = 60
    MAX_EXIT_ORDER_SHARES = 100
    MIN_CASH_RESERVE = 8000.0
    MAX_LOSS_FROM_PEAK = 0.045   # from actual synchronized live equity peak
    MAX_TRADE_RISK = 0.0035      # equity fraction / estimated stop fraction
    LEGACY_TRAIL = 0.028
    NEW_POSITION_TRAIL = 0.025
    IOC_UNCONFIRMED_TICKS = 18   # await ack; fail closed if no response
    GLOBAL_GAP = 5
    SYMBOL_GAP = 22
    REVERSAL_GAP = 40
    MAX_ORDER_ATTEMPTS = 180
    MAX_SPREAD = 0.0035
    MAX_ORDINARY_SPREAD = 0.0020
    # Supplied recordings show no positive net 120-second momentum candidate.
    # Enable only after independent out-of-sample evidence of positive net edge.
    ENABLE_ORDINARY_ALPHA = False
    EVENT_MAX_AGE = 125
    STATUS_EVERY = 120
    SYNC_PROBE_WAIT_TICKS = 8
    SYNC_PROBE_MAX_PRICE = 60.0

    def __init__(self):
        self.tick = 0
        self.synced = False
        self.sync_count = 0
        self.sync_probes = 0
        self.sync_version = 0
        self.last_synced_version = -1
        self.session_open = False
        self.paused = False
        self.risk_only = False
        self.hard_block = False
        self.fee = 0.003
        self.rebate = 0.0005
        self.last_status = -10000
        self.last_global_order = -10000
        self.last_symbol_order = defaultdict(lambda: -10000)
        self.last_symbol_side: dict[str, str] = {}
        self.attempts = 0
        self.pending: PendingIOC | None = None
        self.positions_at_sync: dict[str, int] = {}
        self.last_portfolio: dict[str, int] = {}
        self.last_cash = 0.0
        self.server_equity: float | None = None
        self.initial_equity: float | None = None
        self.peak_equity: float | None = None
        self.highs: dict[str, float] = {}
        self.managed: dict[str, ManagedPosition] = {}
        self.last_quotes: dict[str, float] = {}
        self.events: dict[str, EventTrade] = {}
        self.econ_shock: EventTrade | None = None
        self.econ_reference: dict[str, float] = {}
        self.econ_lows: dict[str, float] = {}
        self.cooldown_until = defaultdict(int)
        self.ipo_attempted = False
        self.confirmed_ipo = False
        LOG.info('STRATEGY %s initialized: IOC execution + snapshot provenance', self.VERSION)

    # ------- Transport provenance and execution callbacks -------
    def on_connected(self):
        """Called for each (re)connection; demand a *new* server update."""
        self.synced = False
        self.sync_count = 0
        self.sync_probes = 0
        self.last_synced_version = -1
        self.server_equity = None
        self.session_open = False
        self.pending = None  # old IOC cannot rest; no replay on reconnect
        self.last_quotes.clear()
        self.events.clear()
        self.econ_shock = None
        self.econ_reference.clear()
        self.econ_lows.clear()
        # Don't discard the peak or acquired cost basis on a clean reconnect.
        LOG.warning('CONNECTION RESET; waiting for authoritative PortfolioUpdate')

    def on_portfolio_update(self, update: PortfolioUpdate):
        if not math.isfinite(update.net_worth) or update.net_worth <= 0:
            self.hard_block = True
            LOG.error('INVALID EXCHANGE EQUITY %s: trading disabled', update.net_worth)
            return
        self.server_equity = float(update.net_worth)
        self.sync_version += 1
        self.last_portfolio = {str(s): int(q) for s, q in update.positions.items() if q}
        self.last_cash = float(update.cash)

    def on_order_ack(self, ack: OrderAck):
        p = self.pending
        if p and (p.symbol, p.side) == (ack.symbol, ack.side):
            p.acknowledged_tick = self.tick
            LOG.info('IOC ACK id=%s sym=%s side=%s filled_so_far=%d/%d',
                     ack.order_id, ack.symbol, ack.side, p.filled, p.quantity)

    def on_order_error(self, code: str, message: str):
        LOG.error('VENUE ERROR [%s]: %s', code, message)
        # Server errors lack an order id. After an uncorrelated error, do not
        # assume that a particular request succeeded or failed.
        if code in {'INSUFFICIENT_FUNDS', 'INSUFFICIENT_POSITION',
                    'RISK_REJECT', 'POSITION_LIMIT', 'INVALID_ORDER'}:
            self.paused = True
            LOG.error('TRADING PAUSED after risk/order rejection; investigate')

    def on_fill(self, side: str, symbol: str, quantity: int, price: float):
        q, px = int(quantity), float(price)
        if q <= 0 or not math.isfinite(px) or px <= 0:
            return
        LOG.info('FILL %-4s %-6s qty=%d price=%.4f', side, symbol, q, px)
        p = self.pending
        if p and (p.side, p.symbol) == (side, symbol):
            p.filled = min(p.quantity, p.filled + q)
        if side == 'buy':
            old = self.managed.get(symbol)
            if old and old.quantity > 0:
                size = old.quantity + q
                old.average_price = (old.average_price * old.quantity + px*q)/size
                old.quantity = size
                old.peak = max(old.peak, px)
            else:
                self.managed[symbol] = ManagedPosition(
                    quantity=q, average_price=px, first_tick=self.tick,
                    peak=px, reason=p.reason if p else 'unknown_fill')
        elif side == 'sell' and symbol in self.managed:
            old = self.managed[symbol]
            old.quantity -= q
            if old.quantity <= 0:
                del self.managed[symbol]

    # ------- Session and event signals -------
    def on_event(self, event: str, message: str, data: dict):
        data = data if isinstance(data, dict) else {}
        LOG.info('EVENT %s: %s', event, message)
        if event in {'SESSION_CLOSED', 'SESSION_PREOPEN'}:
            self.session_open = False
            return
        if event in {'SESSION_OPEN', 'AUCTION_RESULT'}:
            self.session_open = True
        if event == 'FEE_SCHEDULE':
            for key, field in [('taker', 'fee'), ('rebate', 'rebate')]:
                try:
                    val = float(data[key])
                    if 0 <= val < 0.05:
                        setattr(self, field, val)
                except (KeyError, ValueError, TypeError):
                    pass
            LOG.info('FEES taker=%.1f bps maker_rebate=%.1f bps',
                     self.fee*1e4, self.rebate*1e4)
        if event in {'MARGIN_CALL', 'LIQUIDATION'}:
            self.risk_only = True
            LOG.error('RISK-ONLY forced by %s', event)
        if event == 'IPO_SUB_RESULT':
            self.confirmed_ipo = 'recorded' in message.lower()
        if event not in {'CALENDAR_EVENT', 'SHOCK'}:
            return
        pct = self._event_pct(message, data)
        kind = str(data.get('kind') or data.get('shock_id') or '').lower()
        event_text = (kind + ' ' + message).lower()
        symbol = str(data.get('symbol') or '').upper()
        if not symbol:
            symbol = next((s for s in self.ALPHA_SYMBOLS
                           if re.search(r'\b' + re.escape(s) + r'\b', message.upper())), '')
        is_macro = (('econ' in event_text) or ('market-wide' in event_text)
                    or ('flash_crash' in event_text) or ('rate_' in event_text))
        if is_macro:
            self.econ_shock = EventTrade(
                symbol='*', start_tick=self.tick, kind='macro', pct=pct,
                reference=1.0, trough=1.0)
            self.econ_reference = dict(self.last_quotes)
            self.econ_lows = dict(self.last_quotes)
            LOG.warning('MACRO EVENT pct=%+.3f; watching synchronized market path', pct)
        elif symbol:
            ref = self.last_quotes.get(symbol)
            if ref:
                self.events[symbol] = EventTrade(symbol, self.tick, kind or 'earnings',
                                                 pct, ref, ref)
                if pct < -0.01:
                    self.cooldown_until[symbol] = self.tick + 140
                LOG.info('SINGLE NAME EVENT %s pct=%+.3f reference=%.3f', symbol, pct, ref)

    @staticmethod
    def _event_pct(message: str, data: dict) -> float:
        for key in ('pct', 'return', 'move_pct'):
            try:
                raw = data[key]
                val = float(raw)
                if math.isfinite(val):
                    return val/100 if abs(val) > 1 else val
            except (KeyError, TypeError, ValueError):
                pass
        match = re.search(r'([+-]\s*\d+(?:\.\d+)?)\s*%', message)
        return float(match.group(1).replace(' ', ''))/100 if match else 0.0

    def on_ipo(self, symbol, lo, hi, shares, data):
        if symbol != 'ORCA' or self.ipo_attempted or shares <= 0 or not (0 < lo <= hi):
            return None
        self.ipo_attempted = True
        LOG.info('ORCA IPO INDICATION 50 shares at %.2f maximum', hi)
        return min(50, int(shares)), float(hi)

    # ------- Pure signal and quote helpers -------
    @staticmethod
    def _quotes(market, symbol: str):
        try:
            bid, ask = float(market.best_bid(symbol)), float(market.best_ask(symbol))
            if 0 < bid <= ask and math.isfinite(bid) and math.isfinite(ask):
                return (bid, ask, (bid+ask)*0.5, (ask-bid)/((bid+ask)*0.5))
        except (ValueError, TypeError, AttributeError):
            pass
        return None

    @staticmethod
    def _features(history, imbalance=0.0):
        """Return heuristic forecast and volatility; do NOT call calibrated."""
        try:
            h = [float(x) for x in history[-120:]]
        except (TypeError, ValueError, AttributeError):
            return None
        if len(h) < 55 or min(h) <= 0 or not all(map(math.isfinite, h)):
            return None
        lr = [math.log(h[i]/h[i-1]) for i in range(1, len(h))]
        local = lr[-36:]
        sigma = math.sqrt(sum((x-sum(local)/len(local))**2 for x in local)/len(local))
        fast = h[-1]/h[-9]-1
        medium = h[-1]/h[-33]-1
        slow = h[-1]/h[-54]-1
        z = (h[-1]-statistics.mean(h[-30:])) / max(
            statistics.pstdev(h[-30:]), 0.0001*h[-1])
        # Momentum + level 1 order flow. Units: *expected-return proxy*,
        # not a validated probabilistic forecast; gated by trading costs.
        forecast = 0.38*medium + 0.18*slow - 0.12*max(fast, 0) + 0.0012*imbalance
        # Strong trend but extremely stretched price: avoid buying the top.
        if z > 2.6:
            forecast *= 0.25
        return forecast, max(sigma, 0.0001), fast, medium, z

    def _book_imbalance(self, market, symbol):
        try:
            value = float(market.order_book_imbalance(symbol))
            return max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0
        except (ValueError, TypeError, AttributeError):
            return 0.0

    def _cost_hurdle(self, spread_frac: float, sigma: float, event=False):
        # Aggressive but net-of-fees: buy-ask / sell-bid for IOC executions.
        return 2.0*self.fee + spread_frac + (0.001 if event else 0.0015) + 1.0*sigma

    def _can_send(self, portfolio, sym, side, qty, price, positions,
                  gross, equity, reason):
        if self.pending or self.paused or self.hard_block or self.attempts >= self.MAX_ORDER_ATTEMPTS:
            return False
        if self.tick-self.last_global_order < self.GLOBAL_GAP:
            return False
        # Exit orders must NOT be throttled by the entry-side reversal
        # cooldown: the earnings overshoot can last only ~15 seconds.
        if side == 'buy':
            if self.tick-self.last_symbol_order[sym] < self.SYMBOL_GAP:
                return False
            previous = self.last_symbol_side.get(sym)
            if previous != side and previous is not None:
                if self.tick-self.last_symbol_order[sym] < self.REVERSAL_GAP:
                    return False
        if qty <= 0 or not math.isfinite(price) or price <= 0:
            return False
        if side == 'buy':
            if self.risk_only or not self.session_open:
                return False
            if portfolio.cash - qty*price*(1+self.fee+0.004) < self.MIN_CASH_RESERVE:
                return False
            if not portfolio.can_buy(sym, qty, price*(1+self.fee+0.004)):
                return False
            if (positions.get(sym, 0)*price + qty*price)/equity > self.MAX_WEIGHT + 0.0001:
                return False
            if (gross + qty*price)/equity > self.MAX_GROSS + 0.0001:
                return False
        elif side == 'sell':
            if positions.get(sym, 0) < qty or not portfolio.can_sell(sym, qty):
                return False
        else:
            return False
        return True

    def _order(self, portfolio, sym, side, qty, price, positions, gross,
               equity, reason):
        qty = int(qty)
        if not self._can_send(portfolio, sym, side, qty, price, positions,
                              gross, equity, reason):
            return None
        self.pending = PendingIOC(sym, side, qty, self.tick, reason)
        self.last_global_order = self.tick
        self.last_symbol_order[sym] = self.tick
        self.last_symbol_side[sym] = side
        self.attempts += 1
        LOG.info('IOC ORDER %-4s %-6s qty=%d limit=%.4f reason=%s',
                 side, sym, qty, price, reason)
        return Signal(symbol=sym, side=side, quantity=qty, price=round(price, 4))

    # ------- The on_tick decision engine -------
    def on_tick(self, market, portfolio):
        self.tick += 1
        # Provenance: not merely repeated default snapshots. Must have received
        # a real PortfolioUpdate on this *connection*.
        if self.server_equity is None:
            self.sync_count += 1
            if self.pending and self.tick-self.pending.submitted_tick > self.IOC_UNCONFIRMED_TICKS:
                self.paused = True
                LOG.error('SYNC PROBE failed: no PortfolioUpdate after one IOC. '
                          'Risk disabled and no further orders; inspect venue.')
                return None
            if (self.session_open and not self.paused and not self.hard_block
                and self.sync_count >= self.SYNC_PROBE_WAIT_TICKS
                and self.sync_probes == 0 and self.pending is None):
                # In the supplied server, mid-session *reconnections* receive
                # leaderboard/book data but not always a PortfolioUpdate.
                # A SINGLE tiny IOC fill provokes one, allowing safe position
                # reconciliation without assuming the default 100k balance.
                # This is an explicit, bounded simulated purchase.
                try:
                    options = [(q[1], sym) for sym in market.symbols()
                               if (q := self._quotes(market, sym)) is not None
                               and q[1] <= self.SYNC_PROBE_MAX_PRICE
                               and q[3] <= self.MAX_SPREAD]
                except (TypeError, AttributeError):
                    options = []
                if options:
                    ask, sym = min(options)
                    self.sync_probes = 1
                    self.pending = PendingIOC(sym, 'buy', 1, self.tick, 'sync_probe')
                    LOG.warning('SYNC PROBE: buying exactly ONE %s share IOC @ %.4f '
                                'to obtain authoritative portfolio', sym, ask)
                    return Signal(symbol=sym, side='buy', quantity=1, price=round(ask,4))
            if self.tick-self.last_status >= 80:
                LOG.warning('SYNC WAIT: no verified PortfolioUpdate; normal strategy disabled')
                self.last_status = self.tick
            return None
        new_server_update = self.sync_version != self.last_synced_version
        self.last_synced_version = self.sync_version
        if not self.synced:
            self.synced = True
            LOG.info('SYNC READY server equity=%.2f cash=%.2f positions=%s',
                     self.server_equity, self.last_cash, self.last_portfolio)
        positions = dict(self.last_portfolio)
        cash = self.last_cash
        if any(q < 0 for q in positions.values()):
            self.hard_block = True
            LOG.error('UNMANAGED SHORT INVENTORY %s. Long-only strategy refuses '
                      'to trade until shorts are covered manually.',
                      {s:q for s,q in positions.items() if q<0})
            return None
        exchange_equity = float(self.server_equity)
        if not math.isfinite(exchange_equity) or exchange_equity <= 0:
            self.hard_block = True
            return None
        try:
            available = set(market.symbols())
        except (TypeError, AttributeError):
            return None
        observe = available.intersection(set(positions) | set(self.ALPHA_SYMBOLS))
        quotes = {s: q for s in observe if (q := self._quotes(market, s)) is not None}
        missing = set(positions) - set(quotes)
        if missing:
            if self.tick-self.last_status >= 50:
                LOG.warning('RISK WAIT missing quotes for held inventory: %s', sorted(missing))
                self.last_status = self.tick
            return None
        local_equity = cash + sum(q*quotes[s][2] for s,q in positions.items())
        # Server and local snapshots use slightly different marks, but a large
        # discrepancy means we do not know our actual exposure.
        tol = max(1000.0, 0.022*exchange_equity)
        if new_server_update and abs(local_equity-exchange_equity) > tol:
            if self.tick-self.last_status >= 50:
                LOG.error('VALUATION DISAGREEMENT exchange=%.2f local=%.2f; no orders',
                          exchange_equity, local_equity)
                self.last_status = self.tick
            return None
        equity = local_equity  # mark to current mid; server refreshes asynchronously
        if self.initial_equity is None:
            self.initial_equity = equity
            self.peak_equity = equity
            self.positions_at_sync = dict(positions)
            LOG.info('PORTFOLIO BASELINE equity=%.2f positions=%s (legacy tracked)',
                     equity, positions)
        self.peak_equity = max(self.peak_equity, equity)
        if equity <= self.peak_equity*(1-self.MAX_LOSS_FROM_PEAK):
            if not self.risk_only:
                LOG.error('DRAWDOWN CIRCUIT BREAKER equity=%.2f peak=%.2f',
                          equity, self.peak_equity)
            self.risk_only = True
        # Tick-level highs for every holding, including inherited positions.
        for sym, qty in positions.items():
            if qty > 0:
                self.highs[sym] = max(self.highs.get(sym, quotes[sym][2]), quotes[sym][2])
                if sym in self.managed:
                    self.managed[sym].peak = max(self.managed[sym].peak, quotes[sym][2])
        for sym in list(self.highs):
            if positions.get(sym, 0) <= 0:
                self.highs.pop(sym, None)
                self.managed.pop(sym, None)
        if self.pending:
            p = self.pending
            if p.acknowledged_tick is not None and self.tick > p.acknowledged_tick + 2:
                LOG.info('IOC settled %s %s executed=%d/%d', p.side, p.symbol,
                         p.filled, p.quantity)
                # An IOC that did not fill never opened an event position.
                if p.filled == 0 and p.reason=='event_positive' and p.symbol in self.events:
                    self.events[p.symbol].entered = False
                self.pending = None
            elif self.tick-p.submitted_tick > self.IOC_UNCONFIRMED_TICKS:
                self.paused = True
                LOG.error('UNCONFIRMED IOC: manual inspection required; blocking trading')
                return None
        gross = sum(abs(q)*quotes[s][2] for s,q in positions.items())
        if self.tick-self.last_status >= self.STATUS_EVERY:
            LOG.info('RISK equity=%.2f peak=%.2f cash=%.2f gross=%.2f (%.1f%%) '
                     'risk_only=%s pending=%s fee=%.1fbps',
                     equity, self.peak_equity, cash, gross, 100*gross/equity,
                     self.risk_only, bool(self.pending), self.fee*1e4)
            self.last_status = self.tick
        if self.pending or self.paused or self.hard_block or self.attempts >= self.MAX_ORDER_ATTEMPTS:
            return None
        mids = {s: q[2] for s,q in quotes.items()}
        if self.econ_shock:
            for sym, mid in mids.items():
                if sym in self.econ_reference:
                    self.econ_lows[sym] = min(self.econ_lows.get(sym, mid), mid)
        self.last_quotes = mids
        market_returns = []
        for sym in self.ALPHA_SYMBOLS:
            if sym in quotes:
                try:
                    h = market.prices(sym)
                    if len(h) >= 22 and h[-22] > 0:
                        market_returns.append(h[-1]/h[-22]-1)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        broad_return = statistics.median(market_returns) if market_returns else 0.0
        # Event paths are measured from the last quote *before* public news.
        for sym, event in list(self.events.items()):
            if sym in mids:
                event.trough = min(event.trough, mids[sym])
            if self.tick-event.start_tick > self.EVENT_MAX_AGE:
                del self.events[sym]
        if self.econ_shock and self.tick-self.econ_shock.start_tick > self.EVENT_MAX_AGE:
            self.econ_shock = None
        # 1) Continuous portfolio risk: all positions, not just signal names.
        exits = []
        for sym, qty in positions.items():
            if qty <= 0 or sym not in quotes:
                continue
            bid, ask, mid, spread = quotes[sym]
            weight = qty*mid/equity
            reduction = 0
            priority, reason = 0.0, ''
            if self.risk_only:
                # Circuit breaker: reduce all remaining positive exposure.
                reduction, priority, reason = qty, 200+weight, 'drawdown_reduce'
            elif weight > self.MAX_WEIGHT:
                reduction = max(1, math.ceil((qty*mid-self.MAX_WEIGHT*0.96*equity)/mid))
                priority, reason = 150+weight, 'concentration'
            elif gross/equity > self.MAX_GROSS:
                reduction = max(1, math.ceil((gross-self.TARGET_GROSS*equity)/mid))
                priority, reason = 140+weight, 'gross_exposure'
            else:
                trail = self.NEW_POSITION_TRAIL if sym in self.managed else self.LEGACY_TRAIL
                # Avoid stop-outs from tiny single-tick wiggles; stronger shock
                # volatility widens trailing distance, not stop size to infinity.
                try:
                    h = market.prices(sym)
                    recent = [math.log(h[i]/h[i-1]) for i in range(max(1,len(h)-26),len(h)) if h[i] > 0 and h[i-1] > 0]
                    if len(recent) >= 12:
                        sigma = statistics.pstdev(recent)
                        trail = min(0.055, max(trail, 6*sigma))
                except (ValueError, TypeError, AttributeError):
                    pass
                peak = self.highs.get(sym, mid)
                if bid < peak*(1-trail):
                    reduction, priority, reason = qty, 110+weight, 'trailing_stop'
                elif sym in self.events and self.events[sym].pct <= -0.018:
                    reduction, priority, reason = qty, 100+weight, 'negative_event'
                else:
                    own = self.managed.get(sym)
                    if own and own.quantity:
                        held_ticks = self.tick-own.first_tick
                        event_info = self.events.get(sym)
                        # For earnings continuation, aim to monetize the
                        # short-lived ramp / overshoot, not hold indefinitely.
                        event_target = bool(
                            own.reason == 'event_positive' and event_info
                            and event_info.pct > 0.0
                            and bid/event_info.reference-1 >= 1.10*event_info.pct)
                        # Exit profitable positions on established reversal.
                        if event_target:
                            reduction, priority, reason = min(qty, own.quantity), 98+weight, 'event_target'
                        elif (bid/own.average_price - 1 > self._cost_hurdle(spread, .0002)
                              and mid < own.peak * 0.990):
                            reduction, priority, reason = min(qty, own.quantity), 95+weight, 'protect_profit'
                        elif held_ticks > (55 if own.reason=='event_positive' else
                                            115 if own.reason=='event_rebound' else 320):
                            reduction, priority, reason = min(qty, own.quantity), 55+weight, 'time_exit'
            if reduction > 0:
                reduction = min(qty, reduction, self.MAX_EXIT_ORDER_SHARES)
                # Marketable IOC sells at bid; conservative: never bid below the
                # currently displayed best price to increase fill probability.
                exits.append((priority, sym, reduction, bid, reason))
        if exits:
            for _, sym, qty, bid, reason in sorted(exits, reverse=True):
                order = self._order(portfolio, sym, 'sell', qty, bid, positions,
                                    gross, equity, reason)
                if order:
                    return order
        if self.risk_only or gross/equity >= self.TARGET_GROSS:
            return None
        # 2) Public event response: large positive announced shocks produce
        # early trend-following opportunities while the venue ramps prices.
        opportunities = []
        for sym, event in self.events.items():
            if sym not in quotes or event.entered or event.pct <= 0.015:
                continue
            age = self.tick-event.start_tick
            if age > 30 or age < 0 or self.tick < self.cooldown_until[sym]:
                continue
            bid, ask, mid, spread = quotes[sym]
            if spread > self.MAX_SPREAD:
                continue
            observed_move = mid/event.reference-1
            # Avoid chasing if the announced move has nearly finished.
            remaining_proxy = max(0.0, 0.50*(event.pct-max(observed_move, 0)))
            cost = self._cost_hurdle(spread, .001, event=True)
            if remaining_proxy > cost and observed_move > -0.004:
                opportunities.append((remaining_proxy/cost, sym, 'event_positive'))
        # 3) Market-wide selloff: wait for trough and rebound; don't buy while
        # the print is still moving lower. Cross-sectional reference is median.
        if self.econ_shock and self.econ_shock.pct <= -0.012:
            e = self.econ_shock
            age = self.tick-e.start_tick
            if age <= 95 and age >= 28 and market_returns:
                for sym in self.ALPHA_SYMBOLS:
                    if sym not in quotes or sym not in self.last_quotes:
                        continue
                    # stock-level reference before macro news: frozen below
                    ref = self.econ_reference.get(sym)
                    mid = quotes[sym][2]
                    low = self.econ_lows.get(sym, ref)
                    if low is not None:
                        low = min(low, mid)
                        self.econ_lows[sym] = low
                    if not ref or not low:
                        continue
                    drawdown, rebound = low/ref-1, mid/low-1
                    cost = self._cost_hurdle(quotes[sym][3], .0005, event=True)
                    if drawdown <= -0.012 and 0.004 <= rebound <= 0.014 and rebound*2.2 > cost:
                        opportunities.append((rebound*2.2/cost, sym, 'event_rebound'))
        # 4) General alpha: only enter when the heuristic edge beats *both*
        # taker fees, spread, volatility buffer, and stretched-price filter.
        if (self.ENABLE_ORDINARY_ALPHA and broad_return > -0.009
            and self.econ_shock is None):
            for sym in self.ALPHA_SYMBOLS:
                if sym not in quotes or self.tick < self.cooldown_until[sym]:
                    continue
                bid, ask, mid, spread = quotes[sym]
                if spread > self.MAX_ORDINARY_SPREAD:
                    continue
                try:
                    feats = self._features(market.prices(sym),
                                            self._book_imbalance(market, sym))
                except (TypeError, ValueError, AttributeError):
                    continue
                if not feats:
                    continue
                proxy, sigma, fast, momentum, z = feats
                hurdle = self._cost_hurdle(spread, sigma)
                if proxy > hurdle and momentum > 0 and z < 2.6:
                    opportunities.append((proxy/hurdle, sym, 'alpha_momentum'))
        if not opportunities:
            self.last_quotes = mids
            return None
        # Rank edge / cost, not alphabetical or 1st symbol encountered.
        for strength, sym, reason in sorted(opportunities, reverse=True):
            bid, ask, mid, spread = quotes[sym]
            if spread > self.MAX_SPREAD:
                continue
            held = max(0, positions.get(sym, 0))
            max_symbol_value = max(0, self.MAX_WEIGHT*equity-held*mid)
            free_exposure = max(0, self.TARGET_GROSS*equity-gross)
            risk_stop = max(0.022, 5.0*min(0.004, spread+0.0005))
            risk_notional = self.MAX_TRADE_RISK*equity/risk_stop
            limit_value = min(max_symbol_value, free_exposure, risk_notional,
                              max(0, cash-self.MIN_CASH_RESERVE)/(1+self.fee+0.004))
            qty = min(self.MAX_NEW_ORDER_SHARES, int(limit_value/ask))
            if qty <= 0:
                continue
            result = self._order(portfolio, sym, 'buy', qty, ask, positions,
                                 gross, equity, reason)
            if result:
                if reason.startswith('event') and sym in self.events:
                    self.events[sym].entered = True
                self.last_quotes = mids
                return result
        self.last_quotes = mids
        return None

    def run(self):
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
                            datefmt='%H:%M:%S')
        LOG.info('ACTIVE STRATEGY %s', self.VERSION)
        asyncio.run(_ExecutionAwareBot(self).run())


class _ExecutionAwareBot(_AdaptedTraderBot):
    """Minimal SDK adapter extension: IOC + server-snapshot/ack provenance.

    Uses the same message dispatcher and inherited connection/trading-loop
    machinery as the course's `arena.trader._AdaptedTraderBot`.
    """
    async def connect(self):
        await super().connect()
        self._owner.on_connected()

    async def _place_order(self, signal):
        # IOC: unfilled remainder is cancelled immediately. No stale limit
        # order can be left behind to later cross our next trade.
        await self._send(PlaceOrder(
            team_id=self.portfolio.team_id, symbol=signal.symbol,
            side=signal.side, order_type='ioc', price=signal.price,
            quantity=signal.quantity))

    async def listen(self):
        async for raw in self._ws:
            try:
                msg = parse_message(json.loads(raw))
            except (KeyError, ValueError) as exc:
                LOG.warning('Unrecognized exchange message: %s', exc)
                continue
            if isinstance(msg, BookSnapshot):
                self.market.update_book(msg)
            elif isinstance(msg, TradeExecution):
                self.market.update_trade(msg)
                self._on_trade(msg)  # inherited SDK adapter calls owner.on_fill
            elif isinstance(msg, PortfolioUpdate):
                self.portfolio.apply_server_update(msg)
                self._owner.on_portfolio_update(msg)
            elif isinstance(msg, SessionEvent):
                # inherited adapter handles SESSION_OPEN and dispatches on_event
                await self._on_session_event(msg)
                if msg.event == 'SESSION_OPEN':
                    self._owner.session_open = True
                elif msg.event == 'SESSION_PREOPEN':
                    self._owner.session_open = False
                elif msg.event == 'SESSION_CLOSED':
                    self._owner.session_open = False
            elif isinstance(msg, OrderAck):
                self._owner.on_order_ack(msg)
            elif isinstance(msg, ErrorMsg):
                LOG.warning('Exchange error [%s]: %s', msg.code, msg.message)
                self._owner.on_order_error(msg.code, msg.message)
            elif isinstance(msg, Leaderboard):
                self._on_leaderboard(msg)


if __name__ == '__main__':
    MyTrader().run()
