"""Research-only strategy variants; original team classes remain the control.

Only public event/book/account fields enter decisions. These classes do not
connect by themselves. Use the isolated research harness/rehearsal. Passive
orders retain reservation/cancel confirmation handling, but distributed maker
fill/snapshot ordering remains an explicit promotion gate.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

from arena import Signal
from week2.week02_alpha_bot import _depth
from arena.broker import _AdaptedBrokerBot
from shared.messages import PlaceOrder
from team.trader import MyTrader, _Bot
from team.broker import MyBroker, _RiskBrokerBot
import team.trader as trader_module
import team.broker as broker_module


# Cash-settled futures (ARENA10): the ledger/risk models are share-only, so research variants never trade them.
import os
NON_SHARE_SYMBOLS = frozenset(x.strip() for x in os.environ.get("FUTURES", "ARENA10").split(",") if x.strip())


@dataclass(frozen=True)
class StrategyConfig:
    exposure: float = 1.0
    passive: bool = False
    spread_bps: float = 60.0
    quote_size: int = 2
    quote_refresh: float = 5.0
    skew_bps: float = 0.0
    signal: str = "event"  # event, imbalance, reversion, combined
    enable_short: bool = False
    entry_fraction: float = .5
    take_fraction: float = .9
    ipo: bool = False
    cap_shares: int = 150      # per-symbol share cap in SeasonTraderBot._place_order (x exposure)
    gross_cap: float = 70000.  # gross notional cap in _place_order (x exposure)
    # --- EventTrader (signal == 'event2') ---
    ev_notional: float = 0.15  # fraction of net worth targeted per single-name event (split across market-wide events)
    ev_entry: float = 0.5      # marketable entry limit at pre*(1+sign*ev_entry*|pct|)
    ev_take: float = 0.9       # exit when mid reaches pre*(1+sign*ev_take*overshoot*|pct|)
    ev_time: float = 3.0       # time stop, in multiples of ramp length
    ev_stop: float = 0.5       # stop when price retraces ev_stop*|pct| against entry
    ev_window: float = 0.5     # stop adding after ev_window*ramp length
    ev_chunk: int = 60         # max shares per IOC
    ev_kill: float = 0.03      # no new entries after this drawdown from the equity peak
    ev_min_edge: float = 1.0   # require designed edge > ev_min_edge * round-trip fees
    ev_delay: float = 0.0      # seconds to wait after the event before entering
    ev_maxsyms: int = 4        # market-wide events: trade at most this many symbols (most liquid first)
    join: bool = False        # broker: quote at/inside the observed touch instead of ref±spread
    improve: bool = False     # broker: improve the touch by one tick when spread >= 3 ticks

    def __post_init__(self):
        if not 0 < self.exposure <= 2 or self.quote_size < 1 or self.quote_refresh < 1:
            raise ValueError("research exposure must be (0,2], quote_size >=1, refresh >=1")
        if self.signal not in {"event", "imbalance", "reversion", "combined", "event2"}:
            raise ValueError("unknown signal")


def _week(data):
    return int(data.get("week", (data.get("scenario") or {}).get("week", 0)))


class SeasonTrader(MyTrader):
    def __init__(self, config=None):
        super().__init__()
        self.research_config = config or StrategyConfig()
        self._shorts_allowed = False
        self._short_gate_known = False
        self._ssr = set()
        self._research_entry = {}

    def on_event(self, event, message, data):
        super().on_event(event, message, data)
        if event in {"WEEK_CHANGED", "SESSION_OPEN"}:
            self._week5_active = 5 <= _week(data) <= 10
            flags = (data.get("scenario") or data).get("flags", {})
            self._shorts_allowed = bool(flags.get("shorts_allowed", False))
        if event == "SESSION_OPEN":
            if not data.get("late_join", False):
                self._ssr.clear()
                self._short_gate_known = True
            # The current handshake does not replay SSR. Missing it while
            # disconnected cannot be interpreted as permission to short.
            self._shorts_allowed = self._shorts_allowed and self._short_gate_known
        if event == "SSR_ON" and data.get("symbol"):
            self._ssr.add(data["symbol"])
        if event == "MARKET_STATE":
            self._ssr.update(data.get("ssr_active", []))
        if event == "CALENDAR_EVENT" and self.research_config.enable_short and self._shorts_allowed:
            for symbol in data.get("affected", []):
                p = self.plans.get(symbol)
                if (p and p.pct < 0 and p.held == 0 and p.core_sold == 0
                        and (self._portfolio is None or self._portfolio.positions.get(symbol, 0) == 0)):
                    p.mode, p.state = "short", "enter"

    def on_reconnect(self):
        super().on_reconnect()
        self._short_gate_known = False
        self._shorts_allowed = False

    def on_ipo(self, symbol, lo, hi, shares, data):
        # Isolate IPO hypothesis; activation fix must not silently add IPO risk.
        if not self.research_config.ipo or not self._portfolio or hi <= 0:
            return None
        qty = min(int(150 * self.research_config.exposure), shares,
                  max(0, int(self._portfolio.cash * .1 / hi)))
        return (qty, hi) if qty else None

    def _size(self, sym, px, nw, pos):
        cap = int(150 * self.research_config.exposure)
        return max(0, min(cap - max(pos, 0), int(nw * .25 * self.research_config.exposure / px)))

    def _step(self, p, market, portfolio, nw, halted):
        if p.mode != "short":
            # Parent uses fixed timing for control-comparable positive events.
            return super()._step(p, market, portfolio, nw, halted)
        sym = p.symbol
        bot = getattr(self, "_bot", None)
        if (self._market_halted or sym in self._halted_symbols
                or (bot is not None and sym in bot._entry_from_board)):
            return None, 0
        bid, ask = market.best_bid(sym), market.best_ask(sym)
        age = self.now() - p.t0
        pos = int(portfolio.positions.get(sym, 0))
        cfg = self.research_config
        if p.state == "enter":
            limit = p.pre * (1 + cfg.entry_fraction * p.pct)
            if halted or sym in self._ssr or age > p.ramp_s * .5 or (bid and bid < limit):
                p.state = "hold" if p.held else "done"
                return None, 0
            target = self._size(sym, bid or p.pre, nw, 0)
            want = target - p.held
            if want <= 0:
                p.state = "hold"
            elif bid:
                return Signal(symbol=sym, side="sell", quantity=min(60, want), price=round(limit, 2)), 3
        if p.state == "hold" and p.held and ask:
            take = p.pre * (1 + cfg.take_fraction * p.overshoot * p.pct)
            stop = p.entry_px * (1 + abs(p.pct) / 2)
            if ask <= take or ask >= stop or age > 3 * p.ramp_s:
                return Signal(symbol=sym, side="buy", quantity=min(60, p.held, max(0, -pos)),
                              price=round(ask * 1.001, 2)), 5
        if p.state == "hold" and not p.held:
            p.state = "done"
        return None, 0

    def on_fill(self, side, symbol, quantity, price):
        p = self.plans.get(symbol)
        if p and p.mode == "short":
            p.log.append((self.now(), side, quantity, price))
            if side == "sell":
                p.entry_px = (p.entry_px * p.held + price * quantity) / (p.held + quantity)
                p.held += quantity
            else:
                p.held = max(0, p.held - quantity)
            f = self._inflight.get(symbol)
            if f and f[0] == side:
                f[1] = max(0, f[1] - quantity)
        else:
            super().on_fill(side, symbol, quantity, price)
        if side == "buy":
            self._research_entry.setdefault(symbol, (self.now(), price))
        elif self._portfolio and self._portfolio.positions.get(symbol, 0) <= 0:
            self._research_entry.pop(symbol, None)

    def on_tick(self, market, portfolio):
        cfg = self.research_config
        event_signal = super().on_tick(market, portfolio)
        if cfg.signal == "event":
            return event_signal
        if cfg.signal == "combined" and event_signal is not None:
            return event_signal
        bot = getattr(self, "_bot", None)
        if self._market_halted or (bot and (bot._resync or bot._first_book)):
            return None
        for sym in market.symbols():
            if sym in self._inflight or sym in self._halted_symbols or sym in self.plans:
                continue
            bid, ask = market.best_bid(sym), market.best_ask(sym)
            if not bid or not ask:
                continue
            pos = portfolio.positions.get(sym, 0)
            born, entry = self._research_entry.get(sym, (self.now(), ask))
            if pos > 0 and (self.now() - born > 20 or bid < entry * .985 or bid > entry * (1 + 2*self.taker + .003)):
                return Signal(symbol=sym, side="sell", quantity=min(pos, 30), price=bid)
            hist = market.prices(sym)
            if pos > 0 or len(hist) < 20:
                continue
            book = market._books.get(sym, {})
            bids, asks = book.get("bids", []), book.get("asks", [])
            bqty = sum(x[1] for x in bids[:3]); aqty = sum(x[1] for x in asks[:3])
            imbalance = (bqty-aqty) / max(1, bqty+aqty)
            mean = sum(hist[-20:]) / 20
            reversion_edge = mean / ask - 1
            # Observable price dislocation must pay both taker fees and spread.
            edge = reversion_edge
            if cfg.signal == "imbalance":
                edge = (bid+ask)/2/ask - 1 + max(0, imbalance) * .003
            if edge > 2*self.taker + .002 and (cfg.signal != "imbalance" or imbalance > .6):
                return Signal(symbol=sym, side="buy", quantity=min(30, self._size(sym, ask, portfolio.net_worth(market), 0)), price=ask)
        return None


class EvPlan:
    __slots__ = ("sym", "sign", "pre", "pct", "ov", "t0", "ramp_s", "pos0", "target", "state", "entry_px", "n_ent", "attempts")
    def __init__(self, sym, sign, pre, pct, ov, t0, ramp_s, pos0):
        self.sym, self.sign, self.pre, self.pct, self.ov, self.t0, self.ramp_s, self.pos0 = sym, sign, pre, pct, ov, t0, ramp_s, pos0
        self.target = 0; self.state = "enter"; self.entry_px = 0.; self.n_ent = 0; self.attempts = 0


class EventTrader(SeasonTrader):
    """Public-direction event trader: enter on the fired event's signed move, long or short,
    sized from a notional budget and book depth, exit at a fraction of the overshoot / time / stop."""

    def __init__(self, config=None):
        super().__init__(config)
        self.ev = {}
        self._ev_peak = 0.
        self.ev_stats = {"events": 0, "entries": 0, "skipped_edge": 0}

    # -- events ----------------------------------------------------------
    def on_event(self, event, message, data):
        super().on_event(event, message, data)
        self.plans.clear()                      # parent plans are not used by this strategy
        if event == "CALENDAR_EVENT":
            self._ev_event(data)
        if event in {"WEEK_CHANGED", "SESSION_OPEN"} and not data.get("late_join", False):
            self.ev.clear()

    def _on_shock(self, data):
        targets = data.get("prices") or {}
        for sym in data.get("affected") or []:
            pre = self._refs.get(sym) or self._mids.get(sym); target = targets.get(sym)
            if pre and target:
                self._ev_event({"pct": float(target) / pre - 1.0, "affected": [sym],
                                "ramp_ticks": self.ramp_ticks, "overshoot": self.overshoot})

    def _ev_event(self, data):
        cfg = self.research_config
        pct = float(data.get("pct") or 0.0)
        if not pct or abs(pct) > .25:
            return
        ramp_s = max(1, int(data.get("ramp_ticks") or 15)) * 1.0
        ov = float(data.get("overshoot") or 1.0)
        syms = [x for x in (data.get("affected") or []) if x not in NON_SHARE_SYMBOLS and self._mids.get(x)]
        if not syms:
            return
        self.ev_stats["events"] += 1
        cost = 2 * max(self.taker, .0015) * cfg.ev_min_edge
        edge = (cfg.ev_take * ov - cfg.ev_entry) * abs(pct)
        if edge <= cost:
            self.ev_stats["skipped_edge"] += 1
            return
        if len(syms) > cfg.ev_maxsyms:     # market-wide: favour the cheapest spread (relative)
            syms.sort(key=lambda x: self._rel_spread.get(x, 1.))
            syms = syms[:cfg.ev_maxsyms]
        port = getattr(self, "_portfolio", None)
        nw = port.net_worth(self._market) if (port is not None and getattr(self, "_market", None) is not None) else 175000.
        budget = cfg.ev_notional * nw * cfg.exposure / max(1, len(syms)) * (len(syms) if len(syms) == 1 else 1)
        for sym in syms:
            pre = self._mids[sym]
            pos0 = int(port.positions.get(sym, 0)) if port is not None else 0
            plan = EvPlan(sym, 1 if pct > 0 else -1, pre, pct, ov, self.now(), ramp_s, pos0)
            plan.target = int(budget / pre)
            self.ev[sym] = plan

    _rel_spread = {}

    # -- decisions ---------------------------------------------------------
    def on_tick(self, market, portfolio):
        self._market, self._portfolio_ref = market, portfolio
        bot = getattr(self, "_bot", None)
        if bot is not None and (bot._resync or bot._first_book):
            return None
        if self._market_halted:
            return None
        for sym in market.symbols():
            bid, ask = market.best_bid(sym), market.best_ask(sym)
            if bid and ask:
                self._mids[sym] = (bid + ask) / 2
                self._rel_spread[sym] = (ask - bid) / ((ask + bid) / 2)
        sig = self._resumption_order(market, portfolio) or self._ev_signal(market, portfolio) or self._react(market, portfolio)
        if sig is None or sig.symbol in self._inflight or sig.symbol in self._halted_symbols:
            return None
        return sig

    def _ev_signal(self, market, portfolio):
        cfg = self.research_config
        nw = portfolio.net_worth(market)
        self._ev_peak = max(self._ev_peak, nw)
        halted = nw < self._ev_peak * (1 - cfg.ev_kill)
        best = None
        for sym, p in list(self.ev.items()):
            sig, urg = self._ev_step(p, market, portfolio, halted)
            if p.state == "done":
                self.ev.pop(sym, None)
            if sig is not None and (best is None or urg > best[0]):
                best = (urg, sig)
        return best[1] if best else None

    def _ev_step(self, p, market, portfolio, halted):
        cfg = self.research_config; sym = p.sym
        if sym in self._halted_symbols or (getattr(self, "_bot", None) is not None and sym in self._bot._entry_from_board):
            return None, 0
        bid, ask = market.best_bid(sym), market.best_ask(sym)
        mid = (bid + ask) / 2 if (bid and ask) else self._mids.get(sym)
        if not mid:
            return None, 0
        age = self.now() - p.t0
        pos = int(portfolio.positions.get(sym, 0))
        held = p.sign * (pos - p.pos0)          # shares of the event position currently held (signed by direction)
        sign = p.sign
        if p.state == "enter":
            if age < cfg.ev_delay:
                return None, 0
            if sign < 0 and not (cfg.enable_short and self._shorts_allowed and sym not in self._ssr):
                p.state = "done"; return None, 0
            limit = p.pre * (1 + sign * cfg.ev_entry * abs(p.pct))
            want = p.target - max(held, 0)
            if halted or want <= 0 or age > p.ramp_s * cfg.ev_window:
                p.state = "hold" if held > 0 else "done"; return None, 0
            if sign > 0:
                if not ask or ask > limit: return None, 0
                qty = min(want, cfg.ev_chunk, _depth(market, sym, "asks", limit))
                if qty <= 0 or not portfolio.can_buy(sym, qty, limit): return None, 0
                return Signal(symbol=sym, side="buy", quantity=qty, price=round(limit, 2)), 3.0
            if not bid or bid < limit: return None, 0
            qty = min(want, cfg.ev_chunk, _depth(market, sym, "bids", limit))
            if qty <= 0: return None, 0
            return Signal(symbol=sym, side="sell", quantity=qty, price=round(limit, 2)), 3.0
        if p.state == "hold":
            if held <= 0:
                p.state = "done"; return None, 0
            if not p.entry_px:
                p.entry_px = mid
            take = p.pre * (1 + sign * cfg.ev_take * p.ov * abs(p.pct))
            stop = p.entry_px * (1 - sign * cfg.ev_stop * abs(p.pct))
            hit = (mid >= take or mid <= stop) if sign > 0 else (mid <= take or mid >= stop)
            if hit or age > p.ramp_s * cfg.ev_time:
                qty = min(held, cfg.ev_chunk * 2)
                if sign > 0 and bid:
                    return Signal(symbol=sym, side="sell", quantity=qty, price=round(bid * .995, 2)), 5.0
                if sign < 0 and ask:
                    return Signal(symbol=sym, side="buy", quantity=qty, price=round(ask * 1.005, 2)), 5.0
        return None, 0

    def on_fill(self, side, symbol, quantity, price):
        p = self.ev.get(symbol)
        if p is not None and not p.entry_px and ((side == "buy") == (p.sign > 0)):
            p.entry_px = price
        super().on_fill(side, symbol, quantity, price)



class SeasonTraderBot(_Bot):
    async def _on_session_event(self, msg):
        await super()._on_session_event(msg)
        if msg.event in {"SESSION_OPEN", "WEEK_CHANGED"}:
            self._owner.on_event(msg.event, msg.message, msg.data or {})

    async def _place_order(self, signal):
        """Same final reconciliation gates; configurable cash-funded risk budget.

        Shorts are unlevered (gross <= .8 starting capital), explicitly public
        flag gated, and forbidden under observed SSR. Locate rejection still
        follows the original reconnect/reconciliation path.
        """
        owner = self._owner; cfg = owner.research_config
        if (self.auction or not owner._week5_active or not self._session_open
                or self._resync or self._first_book or signal.symbol in self._entry_from_board
                or signal.symbol in NON_SHARE_SYMBOLS
                or owner._market_halted or signal.symbol in owner._halted_symbols
                or not self._has_server_snapshot or time.monotonic()-self._snapshot_at > 10
                or getattr(self.portfolio, "liquidated", False) or owner._inflight):
            return
        if not math.isfinite(signal.price) or signal.price <= 0:
            return
        refs = owner._refs
        marks = {s: refs.get(s) or self.market.mid_price(s) or 0 for s,q in self.portfolio.positions.items() if q}
        if any(px <= 0 for px in marks.values()):
            return
        sym = signal.symbol; pos = self.portfolio.positions.get(sym, 0)
        ref = refs.get(sym) or self.market.mid_price(sym)
        if not ref or not .93*ref <= signal.price <= 1.07*ref:
            return
        cap = int(cfg.cap_shares*cfg.exposure)
        gross = sum(abs(q)*marks[s] for s,q in self.portfolio.positions.items() if q)
        gross_cap = cfg.gross_cap*cfg.exposure
        # Closing shorts/longs always permitted below the entry equity floor.
        reducing = signal.side == "buy" and pos < 0 or signal.side == "sell" and pos > 0
        if reducing:
            qty = min(signal.quantity, abs(pos))
        else:
            if self.portfolio.net_worth(self.market) < trader_module.TRADER_EQUITY_HALT:
                return
            qty = min(signal.quantity, max(0, cap-abs(pos)),
                      max(0, int((gross_cap-gross)//max(ref, signal.price))))
            if signal.side == "buy":
                reserve = trader_module.TRADER_STARTING_EQUITY - gross_cap
                available = max(0, (self.portfolio.cash-reserve)*.95)
                qty = min(qty, int(available//(signal.price*(1+max(owner.taker,.005)))))
            elif not (cfg.enable_short and owner._shorts_allowed and sym not in owner._ssr):
                return
        if qty <= 0:
            return
        signal = signal.model_copy(update={"quantity": qty})
        owner.on_sent(signal); owner._inflight[sym].append(signal.price)
        if sym in owner._resume_pending:
            owner._resume_sent.add(sym)
        await self._send(PlaceOrder(team_id=trader_module._config.TEAM_ID, symbol=sym,
                                   side=signal.side, order_type="ioc", price=signal.price, quantity=qty))


class SeasonBroker(MyBroker):
    def __init__(self, config=None):
        self.research_config = config or StrategyConfig()

    def spread(self, symbol, price, history):
        if not price:
            return None
        cfg = self.research_config
        returns = [abs(b/a-1) for a,b in zip(history[-20:-1], history[-19:]) if a > 0]
        vol = sum(returns)/len(returns) if returns else 0
        return max(.03, price*max(cfg.spread_bps/10000, min(.03, 8*vol)))

    def skew(self, symbol, inventory):
        return -inventory*self.research_config.skew_bps/10000


class _Tee:
    """Observe raw public BookSnapshot frames without altering the production listener."""
    def __init__(self, ws, touch):
        self._ws, self._touch = ws, touch
    def __getattr__(self, name):
        return getattr(self._ws, name)
    def __aiter__(self):
        return self._gen()
    async def _gen(self):
        async for raw in self._ws:
            try:
                d = json.loads(raw)
                if d.get("type") == "book_snapshot":
                    b, a = d.get("bids") or [], d.get("asks") or []
                    self._touch[d["symbol"]] = (b[0][0] if b else None, a[0][0] if a else None, time.monotonic())
            except Exception:
                pass
            yield raw


class SeasonBrokerBot(_RiskBrokerBot):
    async def listen_to_exchange(self):
        real = self._ws
        self._ws = _Tee(real, self._touch)
        try:
            await super().listen_to_exchange()
        finally:
            if isinstance(self._ws, _Tee):
                self._ws = real

    def __init__(self, owner, exchange_url=None):
        super().__init__(owner, exchange_url)
        self._touch = {}
        self._post_only_allowed = False
        self._quote_at = {}

    async def _on_session_event(self, msg):
        await super()._on_session_event(msg)
        d = msg.data or {}
        if msg.event in {"WEEK_CHANGED", "SESSION_OPEN"}:
            self._graded_week5 = 5 <= _week(d) <= 10
            flags = (d.get("scenario") or d).get("flags", {})
            self._post_only_allowed = bool(flags.get("post_only_allowed", False))
        if msg.event == "SESSION_PRECLOSE":
            self._continuous = False
            await self.cancel_all_quotes()

    async def quote_symbol(self, symbol):
        cfg = self._owner.research_config
        if not self._ready(symbol):
            if any(k[0] == symbol for k in self._orders):
                await self.cancel_quotes(symbol)
            return
        if any(k[0] == symbol for k in self._orders):
            # Leave FIFO priority intact between planned refreshes.
            if not cfg.passive or time.monotonic()-self._quote_at.get(symbol,0) >= cfg.quote_refresh:
                await self.cancel_quotes(symbol)
            return
        if symbol in self._resume_pending:
            await self._resume_probe(symbol)
            return
        ref = self.state.exchange_prices.get(symbol)
        if not ref or ref <= 0:
            return
        spread = self.state.compute_spread(symbol)
        pos = self.state.positions.get(symbol, 0)
        skew = -ref * self._owner.research_config.skew_bps / 10000 * pos
        quotes = [("buy", round(ref+skew-spread/2,2)), ("sell", round(ref+skew+spread/2,2))]
        if cfg.join:
            bb, ba, seen = self._touch.get(symbol, (None, None, 0))
            if not bb or not ba or time.monotonic() - seen > 3 or ba - bb < .02 - 1e-9:
                return
            tick = .01
            inc = tick if (cfg.improve and ba - bb >= 3*tick - 1e-9) else 0.
            # inventory skew moves the quote away from the touch on the side we are loaded on
            lean = int(round(skew / tick)) if skew else 0
            buy = min(bb + inc + min(0, lean)*tick, ba - tick)
            sell = max(ba - inc + max(0, lean)*tick, bb + tick)
            quotes = [("buy", round(buy, 2)), ("sell", round(sell, 2))]
        for side, price in quotes:
            await self._send(PlaceOrder(team_id=broker_module.broker_config.TEAM_ID, symbol=symbol,
                                       side=side, order_type="limit", price=price, quantity=cfg.quote_size))
        self._quote_at[symbol] = time.monotonic()
        self.state.last_quote_prices[symbol] = ref

    async def _send(self, msg):
        if not isinstance(msg, PlaceOrder):
            return await super()._send(msg)
        cfg = self._owner.research_config
        kind = "post_only" if cfg.passive and msg.order_type != "ioc" else "ioc"
        if kind == "post_only" and not self._post_only_allowed:
            return
        sym, side = msg.symbol, msg.side
        if sym in NON_SHARE_SYMBOLS:
            return
        if not self._ready(sym) or (sym,side) in self._orders:
            return
        marks = self.state.exchange_prices; ref = marks.get(sym,0)
        if not ref or not math.isfinite(msg.price) or not .93*ref <= msg.price <= 1.07*ref:
            return
        if any(q and marks.get(s,0) <= 0 for s,q in self.state.positions.items()):
            return
        pos = self.state.positions.get(sym,0)
        buy = sum(e[0] for (s,d),e in self._orders.items() if s == sym and d == "buy")
        sell = sum(e[0] for (s,d),e in self._orders.items() if s == sym and d == "sell")
        qty = min(msg.quantity, max(0, int(20*cfg.exposure)-pos-buy) if side == "buy" else max(0,pos-sell))
        pending = sum(e[0]*max(e[1],marks.get(s,e[1])) for (s,d),e in self._orders.items() if d == "buy")
        gross = sum(abs(q)*marks[s] for s,q in self.state.positions.items() if q)
        if side == "buy":
            if self._last_equity < broker_module.BROKER_EQUITY_HALT:
                return
            cap = broker_module.BROKER_GROSS_CAP*cfg.exposure
            reserve = broker_module.BROKER_STARTING_EQUITY*.6/cfg.exposure
            qty = min(qty, max(0,int(((self.state.cash-reserve)/(1+self._taker)-pending)//msg.price)),
                      max(0,int((cap-gross-pending)//max(ref,msg.price))))
        if qty <= 0:
            return
        self._orders[sym,side] = [qty,msg.price,None,kind == "ioc",self._probe_submission == (sym,side)]
        await _AdaptedBrokerBot._send(self,msg.model_copy(update={"quantity":qty,"order_type":kind}))
        return True


def make_candidate(name, params=None):
    """Return owner and real adapter; caller must isolate state before construction."""
    params = dict(params or {})
    if name in {"baseline_trader", "cash_trader"}:
        owner = MyTrader()
        if name == "cash_trader":
            owner.on_tick = lambda market, portfolio: None
            owner.on_ipo = lambda *args: None
        return owner, _Bot(owner)
    if name in {"baseline_broker", "cash_broker"}:
        owner = MyBroker(); bot = _RiskBrokerBot(owner)
        if name == "cash_broker":
            async def no_quote(symbol):
                return None
            bot.quote_symbol = no_quote
        return owner, bot
    presets = {
        "season_trader": {}, "season_broker": {},
        "passive_broker": {"passive": True},
        "short_trader": {"enable_short": True},
        "aggressive_trader": {"exposure": 2.0},
        "aggressive_broker": {"exposure": 2.0, "passive": True, "quote_size": 10},
        "skew_broker": {"passive": True, "spread_bps": 30, "skew_bps": 1.0},
        "touch_broker": {"passive": True, "join": True, "quote_size": 5, "quote_refresh": 3.0},
        "improve_broker": {"passive": True, "join": True, "improve": True, "quote_size": 5, "quote_refresh": 3.0},
        "touch_skew_broker": {"passive": True, "join": True, "quote_size": 5, "quote_refresh": 3.0, "skew_bps": 2.0},
        "imbalance_trader": {"signal": "imbalance"},
        "reversion_trader": {"signal": "reversion"},
        "combined_trader": {"signal": "combined"},
        "ipo_trader": {"ipo": True},
        "event2_trader": {"signal": "event2", "enable_short": True, "cap_shares": 400, "gross_cap": 140000., "ev_chunk": 100},
        "event2_long_trader": {"signal": "event2", "enable_short": False, "cap_shares": 400, "gross_cap": 140000., "ev_chunk": 100},
    }
    if name not in presets:
        raise ValueError(f"unknown candidate {name}")
    config = StrategyConfig(**(presets[name] | params))
    if name.endswith("broker"):
        owner = SeasonBroker(config)
        return owner, SeasonBrokerBot(owner)
    owner = EventTrader(config) if config.signal == "event2" else SeasonTrader(config)
    return owner, SeasonTraderBot(owner)
