"""
6 Rings — broker (market maker).

Posts two-sided quotes and earns the spread plus maker rebates on every
passive fill. Three decisions, three methods — override any of them.
Inventory management (skew) is the key to staying solvent: the exchange
charges margin interest and liquidates teams below maintenance.

Week-5 risk (breakers, stops and liquidation live) on top of the stock quoting:
    ① Spread: widen with realised volatility (an earnings ramp sweeps a
       fixed-width ladder), never wider than MAX_SPREAD_FRAC of the price
    ② Skew: shift both quotes against inventory, SKEW_AT_CAP_BPS at the cap
    ③ Hard inventory limit: per symbol, position + same-side resting quotes
       never exceed INVENTORY_CAP, and the team (every 6_rings_* seat, from the
       leaderboard) never goes past TEAM_POSITION_LIMIT − TEAM_LIMIT_BUFFER.
       A side with no room is simply not quoted; the venue counts resting
       orders against the team limit, so the cap includes them.
    ④ Halts: on TRADING_HALT / MARKET_HALT cancel the symbol's quotes (they
       would be picked off at the stale price when trading resumes) and
       post nothing until TRADING_RESUMED / MARKET_RESUMED; then quote again
       on the next pass. A SYMBOL_HALTED reject (reconnect mid-halt) also halts.
       Under the short-sale rule (SSR_ON, or an SSR_RESTRICTED reject — it may
       have tripped before we connected) the ask side never goes short.
    ⑤ Liquidation: net worth under EQUITY_FLOOR × start → quote only the side
       that reduces inventory. On our LIQUIDATION, cancel everything and stop.
    ⑥ Close: cancel the quotes, do NOT flatten — the stock flatten sends
       market orders after SESSION_CLOSED, and those are always rejected.
    ⑦ Message budget: the venue's quota (20 orders + cancels per tick) is
       shared by the WHOLE team. The stock ladder (3 levels, 5 bps requote)
       used ~27 messages/s on its own, so cancels got RATE_LIMITED, the old
       quotes stayed on the book as orphans (self-trade cancels, and resting
       size the inventory cap never saw). Now: 2 levels, 10 bps, and a
       requote (its cancels + its new orders together) only goes out when it
       fits in MSGS_PER_S; otherwise the old quote stays until the next pass.

Week-6 (scenario tournament: closing cross, the VLTA listing):
    ⑧ Closing auction: on SESSION_PRECLOSE stop two-sided quoting (matching is
       frozen, a resting quote would just be crossed at the closing price)
       and flatten INTO the cross instead — one limit order per symbol on the
       side that reduces inventory, CLOSE_CROSS_FRAC through the mark so it
       executes at the clearing price (limit orders only; the band is 8 %).
       Each symbol's cancels + flatten order go through the ⑦ budget, so the
       work is spread over the 10 auction ticks. BROKER_FLATTEN_AT_CLOSE=0
       turns the flatten off (quotes are still pulled).
    ⑨ New listing: a symbol with less than VOL_WINDOW marks (VLTA right after
       it lists, or anything right after we connect) is quoted at
       NEW_LISTING_SPREAD_FRAC, not the stock width — no volatility estimate
       yet, and a fresh IPO jumps around.
    ⑩ Counterparties: every fill also logs who was on the other side, whether
       we were maker or taker, and what we really paid (the SDK's "fee=" is
       half the taker's fee, not ours). Per-counterparty totals are logged
       at the close.

Run:
    TEAM_ID=6_rings_broker python -m team.broker
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time

from arena import Broker
from arena.broker import _AdaptedBrokerBot
import broker.config as _config
from broker.broker import PlaceOrder
from shared.messages import Leaderboard, SessionEvent, TradeExecution

# ① Spread
VOL_WINDOW = 20               # snapshots of the venue mark used for the move estimate
VOL_MULT = 4.0                # spread ≥ 4 × the mean absolute snapshot-to-snapshot move
MAX_SPREAD_FRAC = 0.02        # never wider than 2 % of the price (LULD band is 8 %)
NEW_LISTING_SPREAD_FRAC = 0.01   # ⑨ too few marks for a volatility estimate → 1 % wide

# ② Skew
SKEW_AT_CAP_BPS = 20.0        # at ±INVENTORY_CAP both quotes move 20 bps against the inventory

# ③ Hard limits
INVENTORY_CAP = int(os.environ.get("BROKER_INVENTORY_CAP", "150"))   # |position| + same-side resting
TEAM_PREFIX = os.environ.get("TEAM_PREFIX", "6_rings_")
TEAM_POSITION_LIMIT = int(os.environ.get("TEAM_POSITION_LIMIT", "1000"))
TEAM_LIMIT_BUFFER = 50        # leaderboard lag + the traders' orders in flight

# ④ Halts
HALT_GRACE_S = 30.0           # no RESUMED this long after a halt should have ended → assume resumed
DEFAULT_HALT_S = 300.0        # the venue's HALT_DURATION_SEC, when a reject doesn't say

# ⑤ Liquidation
EQUITY_FLOOR = 0.70           # net worth < 70 % of start → only reduce (the venue liquidates at 50 %)

# ⑦ Message budget (the venue meters the team, not the seat)
MSGS_PER_S = float(os.environ.get("BROKER_MSGS_PER_S", "10"))   # leaves ~half the team quota to the traders
# ⑧ Closing auction
FLATTEN_AT_CLOSE = os.environ.get("BROKER_FLATTEN_AT_CLOSE", "1") not in ("0", "false", "no")
CLOSE_CROSS_FRAC = 0.02       # flatten limit 2 % through the mark: crosses at the clearing price

if "QUOTE_LEVELS" not in os.environ:
    _config.QUOTE_LEVELS = 2                  # stock 3: 12 messages per requote → 8
if "REQUOTE_THRESHOLD_BPS" not in os.environ:
    _config.REQUOTE_THRESHOLD_BPS = 10.0      # stock 5: half the requotes

log = logging.getLogger("team.broker")


class MyBroker(Broker):
    """Volatility-scaled spread, inventory skew, and the week-5 risk plumbing."""

    def __init__(self) -> None:
        self.prices: dict[str, float] = {}     # the venue mark per symbol (set by the bot)

    def spread(self, symbol, price, history):
        """The stock width, widened to VOL_MULT × the recent mean move."""
        if not price:
            return None
        if len(history) <= VOL_WINDOW:         # ⑨ no estimate yet (new listing / just connected)
            return price * NEW_LISTING_SPREAD_FRAC
        recent = history[-(VOL_WINDOW + 1):]
        move = sum(abs(b - a) for a, b in zip(recent, recent[1:])) / VOL_WINDOW
        base = max(min(_config.BASE_SPREAD, price * _config.MAX_SPREAD_BPS / 10_000),
                   _config.MIN_SPREAD_ABS)
        return min(max(base, VOL_MULT * move), price * MAX_SPREAD_FRAC)

    def skew(self, symbol, inventory):
        """Long → quote lower, short → higher; linear in inventory, capped at the cap."""
        price = self.prices.get(symbol)
        if not price or not inventory:
            return None
        frac = max(-1.0, min(1.0, inventory / INVENTORY_CAP))
        return -frac * price * SKEW_AT_CAP_BPS / 10_000

    async def _run_venues(self) -> None:
        """The stock entry point, with the risk-aware bot below."""
        urls = [u.strip() for u in _config.EXCHANGE_URLS if u.strip()] or [None]
        bots = [_RiskBrokerBot(self, url) for url in urls]
        for b in bots[1:]:                     # one Yahoo poll, shared (as the stock code does)
            b.state.yahoo_prices = bots[0].state.yahoo_prices
            b._own_yahoo_feed = False
        self.prices = bots[0].state.exchange_prices
        await asyncio.gather(*(b.run() for b in bots))


class _RiskBrokerBot(_AdaptedBrokerBot):
    """The stock quoting loop with hard inventory limits, halts and no flatten."""

    def __init__(self, owner: MyBroker, exchange_url: str | None = None) -> None:
        super().__init__(owner, exchange_url)
        self.halted: dict[str, float] = {}     # symbol → when we stop waiting for its RESUMED
        self.market_halt_until = 0.0
        self.liquidated = False
        self.team_board: dict[str, int] = {}   # symbol → team position (all our seats)
        self.my_board: dict[str, int] = {}     # symbol → this seat's position in that row
        self.ssr: set[str] = set()             # symbols under the short-sale rule: no new shorts
        self.start_nw = float(os.environ.get("STARTING_CASH", "0"))   # 0 → first net worth we see
        self.nw = 0.0
        self._floor_logged = False
        self._tokens, self._tokens_t = MSGS_PER_S, time.monotonic()
        self.closing = False                   # ⑧ closing auction running
        self._closed: set[str] = set()         # symbols already pulled / flattened into the cross
        self.by_counterparty: dict[str, list] = {}   # ⑩ team → [fills, shares we bought, shares we sold]

    def _take(self, n: int) -> bool:
        """⑦ Spend n messages from the budget, or spend nothing and say no."""
        now = time.monotonic()
        self._tokens = min(MSGS_PER_S, self._tokens + (now - self._tokens_t) * MSGS_PER_S)
        self._tokens_t = now
        if self._tokens < n:
            return False
        self._tokens -= n
        return True

    # ── ③⑤ Room to quote ─────────────────────────────────────────────────

    def below_floor(self) -> bool:
        return bool(self.start_nw and self.nw and self.nw < EQUITY_FLOOR * self.start_nw)

    def room(self, symbol: str) -> tuple[int, int]:
        """Shares we may have resting on (bid, ask) right now. The venue counts
        resting orders against the limit, so this is position + resting ≤ cap."""
        pos = int(self.state.positions.get(symbol, 0))
        team = self.team_board.get(symbol, 0) - self.my_board.get(symbol, 0) + pos
        cap = TEAM_POSITION_LIMIT - TEAM_LIMIT_BUFFER
        buy = min(INVENTORY_CAP - pos, cap - team)
        sell = min(INVENTORY_CAP + pos, cap + team)
        if symbol in self.ssr:                 # SSR: a marketable short is rejected → sell only what we hold
            sell = min(sell, max(pos, 0))
        if self.below_floor():                 # only the side that flattens us
            buy, sell = min(buy, max(-pos, 0)), min(sell, max(pos, 0))
        return max(buy, 0), max(sell, 0)

    # ── ④ Halts ──────────────────────────────────────────────────────────

    def is_halted(self, symbol: str) -> bool:
        now = time.time()
        if now < self.market_halt_until:
            return True
        until = self.halted.get(symbol)
        if until is not None and now >= until:
            del self.halted[symbol]
            log.warning("RESUME %s assumed: no TRADING_RESUMED %.0f s after the halt should have ended",
                        symbol, HALT_GRACE_S)
            return False
        return until is not None

    async def halt(self, symbol: str, duration: float, why: str) -> None:
        self.halted[symbol] = time.time() + duration + HALT_GRACE_S
        log.warning("HALT %s for %.0f s (%s) — quotes cancelled, nothing posted until it resumes",
                    symbol, duration, why)
        await self.cancel_quotes(symbol)

    async def _on_session_event(self, msg: SessionEvent) -> None:
        d = msg.data or {}
        if msg.event == "TRADING_HALT" and d.get("symbol"):
            await self.halt(d["symbol"], float(d.get("duration") or DEFAULT_HALT_S),
                            d.get("reason", "venue halt"))
        elif msg.event == "MARKET_HALT":
            self.market_halt_until = time.time() + float(d.get("duration") or DEFAULT_HALT_S) + HALT_GRACE_S
            log.warning("HALT market-wide (level %s) — all quotes cancelled", d.get("level"))
            await self.cancel_all_quotes()
        elif msg.event == "TRADING_RESUMED" and d.get("symbol"):
            self.halted.pop(d["symbol"], None)
            log.info("RESUME %s — quoting again on the next pass", d["symbol"])
        elif msg.event == "MARKET_RESUMED":
            self.market_halt_until = 0.0
            log.info("RESUME market-wide — quoting again on the next pass")
        elif msg.event == "SSR_ON" and d.get("symbol"):
            self.ssr.add(d["symbol"])
            log.info("SSR %s — no new shorts in it until the next session", d["symbol"])
        elif msg.event in ("SESSION_PREOPEN", "SESSION_OPEN"):
            self.ssr.clear()                   # a new session clears the short-sale rule
            self.closing = False
            self._closed.clear()
        elif msg.event == "SESSION_PRECLOSE":
            # ⑧ The requote loop now runs close_symbol() instead of quoting
            self.closing = True
            self._closed.clear()
            log.info("Pre-close — pulling quotes%s (%s ticks to the cross)",
                     ", flattening into the closing cross" if FLATTEN_AT_CLOSE else "",
                     d.get("ticks", "?"))
            return
        elif msg.event == "LIQUIDATION" and d.get("team_id") == _config.TEAM_ID:
            self.liquidated = True
            log.error("RISK this seat was LIQUIDATED — quotes cancelled, quoting stopped")
            await self.cancel_all_quotes()
        elif msg.event == "SESSION_CLOSED":
            # ⑥ The stock handler flattens with market orders after the close:
            # every one is rejected. Cancel the quotes and keep the inventory.
            self._session_open = False
            self.closing = False
            self.log_counterparties()
            log.info("Session closed — quotes cancelled, no flatten (orders after the close are rejected)")
            await self.cancel_all_quotes()
            return
        await super()._on_session_event(msg)

    async def listen_to_exchange(self) -> None:
        """The stock listener, plus a peek at rejects that mean "stop posting":
        SYMBOL_HALTED (we reconnected mid-halt — the venue does not replay the
        halt) and LIQUIDATED."""
        ws = self._ws
        self._ws = _Peek(ws, self._peek)
        try:
            await super().listen_to_exchange()
        finally:
            self._ws = ws

    async def _peek(self, raw) -> None:
        text = raw if isinstance(raw, str) else raw.decode()
        if "SYMBOL_HALTED" not in text and "LIQUIDATED" not in text and "SSR_RESTRICTED" not in text:
            return
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if msg.get("type") != "error":
            return
        if msg.get("code") == "SYMBOL_HALTED":
            sym = str(msg.get("message", "")).split(" ", 1)[0]
            if sym and sym not in self.halted:
                m = re.search(r"Resume in (\d+)s", msg.get("message", ""))
                await self.halt(sym, float(m.group(1)) if m else DEFAULT_HALT_S, "learnt from a reject")
        elif msg.get("code") == "SSR_RESTRICTED":
            sym = str(msg.get("message", "")).split(" ", 1)[0]
            if sym and sym not in self.ssr:
                self.ssr.add(sym)
                log.info("SSR %s (learnt from a reject) — no new shorts in it", sym)
        elif msg.get("code") == "LIQUIDATED" and not self.liquidated:
            self.liquidated = True
            await self.cancel_all_quotes()

    # ── Leaderboard: team positions and net worth ────────────────────────

    def _on_leaderboard(self, msg: Leaderboard) -> None:
        super()._on_leaderboard(msg)
        team: dict[str, int] = {}
        for e in (*msg.traders, *msg.brokers):
            if str(e.get("team_id", "")).startswith(TEAM_PREFIX):
                for s, q in (e.get("positions") or {}).items():
                    team[s] = team.get(s, 0) + int(q)
                if e.get("team_id") == _config.TEAM_ID:
                    self.my_board = {s: int(q) for s, q in (e.get("positions") or {}).items()}
                    self.nw = float(e.get("net_worth") or 0.0)
                    if not self.start_nw and self.nw > 0:
                        self.start_nw = self.nw
                    if e.get("liquidated"):
                        self.liquidated = True
        self.team_board = team
        if self.below_floor() and not self._floor_logged:
            self._floor_logged = True
            log.error("RISK net worth %.2f < %.0f%% of start %.2f — quoting only the side that reduces",
                      self.nw, EQUITY_FLOOR * 100, self.start_nw)
        elif not self.below_floor():
            self._floor_logged = False

    # ── ⑩ Who traded with us ─────────────────────────────────────────────

    def _on_trade(self, msg: TradeExecution) -> None:
        super()._on_trade(msg)                 # positions + the stock "Fill:" line
        me = _config.TEAM_ID
        if me not in (msg.buyer_id, msg.seller_id):
            return
        bought = msg.buyer_id == me
        other = msg.seller_id if bought else msg.buyer_id
        maker = (msg.maker_id == me) if msg.maker_id else (msg.aggressor != ("buy" if bought else "sell"))
        paid = -msg.maker_rebate if maker else msg.fee
        log.info("  ↳ %s %d %s @ %.2f %s %s — we were %s, paid %+.2f",
                 "bought" if bought else "sold", msg.quantity, msg.symbol, msg.price,
                 "from" if bought else "to", other or "?",
                 "MAKER (they crossed our quote)" if maker else "TAKER (we crossed theirs)", paid)
        row = self.by_counterparty.setdefault(other or "?", [0, 0, 0])
        row[0] += 1
        row[1 if bought else 2] += msg.quantity

    def log_counterparties(self) -> None:
        if not self.by_counterparty:
            return
        log.info("Counterparties this session (fills · shares we bought from them · sold to them):")
        for team, (n, b, s) in sorted(self.by_counterparty.items(), key=lambda kv: -kv[1][0]):
            log.info("    %-24s %4d fills  bought %5d  sold %5d", team, n, b, s)

    # ── Quoting: the stock ladder, each side cut to its room ─────────────

    def needs_requote(self, symbol: str, centre: float) -> bool:
        """The stock rule, except that a side with no room is meant to be empty —
        otherwise a one-sided quote is cancelled and replaced every pass."""
        if self.state.last_quote_prices.get(symbol) is None:
            return True
        resting = self.state.resting_orders.get(symbol, {})
        buy_room, sell_room = self.room(symbol)
        if (buy_room and not resting.get("buy_qty")) or (sell_room and not resting.get("sell_qty")):
            return True                        # a side we may quote is empty
        if abs(centre - self.state.last_quote_prices[symbol]) >= self.requote_threshold(centre):
            return True
        age = time.time() - self.state.last_quote_time.get(symbol, 0.0)
        return age >= _config.QUOTE_MAX_AGE_SEC

    async def quote_symbol(self, symbol: str) -> None:
        """The stock ladder (QUOTE_LEVELS per side, each bigger and wider), with
        each side's total cut to room(); nothing while halted or liquidated."""
        if self.liquidated or self.is_halted(symbol):
            return
        if self.closing:
            await self.close_symbol(symbol)
            return
        centre = self.quote_centre(symbol)
        if not centre or not self.needs_requote(symbol, centre):
            return
        half = self.state.compute_spread(symbol) / 2
        skew = self.state.compute_skew(symbol)
        bid_price = math.floor((centre - half + skew) * 100) / 100
        ask_price = math.ceil((centre + half + skew) * 100) / 100
        if bid_price <= 0 or ask_price <= bid_price:
            return
        if not self._session_open:
            return
        # The new ladder, each side cut to its room
        buy_left, sell_left = self.room(symbol)
        half_step = max(half, 0.01)
        orders, posted = [], {"buy": 0, "sell": 0}
        for lvl in range(_config.QUOTE_LEVELS):
            size = _config.QUOTE_SIZE * (lvl + 1)
            for side, px in (("buy", math.floor((bid_price - lvl * half_step) * 100) / 100),
                             ("sell", math.ceil((ask_price + lvl * half_step) * 100) / 100)):
                qty = min(size, (buy_left if side == "buy" else sell_left) - posted[side])
                if qty > 0 and px > 0:
                    orders.append((side, px, qty))
                    posted[side] += qty
        # ⑦ Cancels + new orders go out together or not at all
        if not self._take(self._resting_count(symbol) + len(orders)):
            return                             # over budget: keep the old quote this pass
        await self.cancel_quotes(symbol)
        for side, px, qty in orders:
            await self._send(PlaceOrder(team_id=_config.TEAM_ID, symbol=symbol, side=side,
                                        order_type="limit", price=px, quantity=qty))
        resting = self.state.resting_orders.setdefault(
            symbol, {"buy_id": None, "buy_qty": 0, "sell_id": None,
                     "sell_qty": 0, "buy_ids": [], "sell_ids": []})
        resting["buy_qty"], resting["sell_qty"] = posted["buy"], posted["sell"]
        self.state.last_quote_prices[symbol] = centre
        self.state.last_quote_time[symbol] = time.time()
        log.info("Quoted %s bid=%.2f×%d ask=%.2f×%d centre=%.2f pos=%+d",
                 symbol, bid_price, posted["buy"], ask_price, posted["sell"], centre,
                 self.state.positions.get(symbol, 0))

    def _resting_count(self, symbol: str) -> int:
        """How many cancels cancel_quotes(symbol) will send."""
        r = self.state.resting_orders.get(symbol, {})
        return len(set(r.get("buy_ids") or []) | set(r.get("sell_ids") or [])
                   | {i for i in (r.get("buy_id"), r.get("sell_id")) if i})

    # ── ⑧ Closing auction ────────────────────────────────────────────────

    async def close_symbol(self, symbol: str) -> None:
        """Once per symbol in the pre-close: pull its quotes and send one limit
        order that takes the position to zero at the closing cross. Cancels +
        that order go through the message budget together; over budget → the
        next pass (0.5 s) tries again."""
        if symbol in self._closed:
            return
        pos = int(self.state.positions.get(symbol, 0))
        ref = self.state.exchange_prices.get(symbol)
        order = None
        if FLATTEN_AT_CLOSE and pos and ref and ref > 0:
            side = "sell" if pos > 0 else "buy"
            px = ref * (1 - CLOSE_CROSS_FRAC) if side == "sell" else ref * (1 + CLOSE_CROSS_FRAC)
            px = math.ceil(px * 100) / 100 if side == "sell" else math.floor(px * 100) / 100
            order = PlaceOrder(team_id=_config.TEAM_ID, symbol=symbol, side=side,
                               order_type="limit", price=px, quantity=abs(pos))
        n = self._resting_count(symbol) + (1 if order else 0)
        if n and not self._take(n):
            return
        await self.cancel_quotes(symbol)
        if order:
            await self._send(order)
            log.info("Close %s: %s %d @ %.2f limit into the closing cross (mark %.2f)",
                     symbol, order.side, order.quantity, order.price, ref)
        self._closed.add(symbol)


class _Peek:
    """The websocket, unchanged, except that every received frame is shown to
    `peek` before the stock listener parses it."""

    def __init__(self, ws, peek) -> None:
        self._ws, self._peek = ws, peek

    def __getattr__(self, name):
        return getattr(self._ws, name)

    async def __aiter__(self):
        async for raw in self._ws:
            await self._peek(raw)
            yield raw


if __name__ == "__main__":
    MyBroker().run()
