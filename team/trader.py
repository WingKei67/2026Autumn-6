"""
6 Rings — trader bot.

The week-2 ShockSwitchTrader plus SHOCK handling, a reaction guarantee,
a reject-free order flow, an IPO indication, its own books and a
reconnect loop that survives a full session.

Notes:
- The parent ShockSwitchTrader (week2/week02_alpha_bot.py) does nothing in
  quiet markets and trades only on a CALENDAR_EVENT (earnings, macro prints):
    pct > 0 → RIDE: buy at once, sell into the overshoot
    pct < 0 → FADE: sell what we hold, buy back past the trough, sell the bounce
- This file adds six things on top of the parent:
    ① SHOCK: a teacher-injected shock carries target prices but no pct →
       derive pct ourselves and hand it to the parent's same logic
    ② Reaction guarantee: at least one order within REACT_AFTER_TICKS venue ticks of
       every event (course rule)
    ③ No rejects:
         - IOC orders only: any unfilled part is cancelled at once and never
           piles up on the book
         - at most one order in flight per seat, retained until IOC completion
         - update local position and cash on the fill itself, without waiting
           for the PortfolioUpdate that arrives a few ticks later
         - per-seat position cap SEAT_POSITION_CAP (the team's 800-share
           limit is split across the seats)
         - no orders during the auctions or after the close
    ④ IPO indication: bid the top of the range, for as many shares as this
       seat can still hold under its cap, and no more than cash allows
    ⑤ Books (team/ledger.py): position, average entry, realized/unrealized
       P&L and fees per symbol, built from fills only, resynced from the
       exchange on every (re)connect and reconciled against it at the close
    ⑥ Survival: reconnect with exponential backoff + jitter, rebuild state
       from the first snapshot after a reconnect, log every handshake, ack,
       reject and fill to logs/<seat>_<time>.log

Run one seat (the seats' SEAT_POSITION_CAP must sum to at most the team limit):
    TEAM_ID=6_rings_trader_2 TICK_INTERVAL=0.1 SEAT_POSITION_CAP=150 python -m team.trader

Record reconnect evidence (drop the socket once, 300 s after start):
    DROP_AFTER_S=300 TEAM_ID=6_rings_trader_2 python -m team.trader

Backtest on a recorded class session:
    python week2/week02_event_backtest.py sessions/<tape>.jsonl \\
        --module team/trader.py --bot MyTrader --tick 0.1
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import pathlib
import random
import time

import websockets

from arena import Signal                    # must come first: it puts engine/ on sys.path for the imports below
from arena.trader import _AdaptedTraderBot  # the SDK plumbing: connect, receive, call on_tick every tick
import trader.config as _config             # _config.TEAM_ID = the name of this seat
from trader.trader import MarketData, _print_startup
from pydantic import TypeAdapter, ValidationError
from shared.messages import (AnyExchangeMessage, BookSnapshot, ErrorMsg, Leaderboard, OrderAck,
                             CancelOrder, PlaceOrder, PortfolioUpdate, SessionEvent, TradeExecution)
from team.ledger import Ledger
from team.risk import log_state, acquire_seat_lock
from week2.week02_alpha_bot import ShockSwitchTrader

# ① A SHOCK carries no ramp shape (ramp_ticks / overshoot): reuse the last CALENDAR_EVENT's
DEFAULT_RAMP_TICKS = 15       # before any CALENDAR_EVENT has been seen (exchange default)
DEFAULT_OVERSHOOT = 1.3
MAX_SHOCK_PCT = 0.20          # a derived move above 20% means our price is stale → don't trade

# ② Reaction guarantee
REACT_AFTER_TICKS = 20        # fallback after 20 venue ticks; grading window is 60
REACT_BID_DISCOUNT = 0.02     # that order: a 1-share IOC bid 2% under the best bid (won't fill, just "reacts")

# ③ No rejects
SEAT_POSITION_CAP = min(150, max(1, int(os.environ.get("SEAT_POSITION_CAP", "150"))))
# Phase 2 safety: this trader's long notional cannot consume more than 40% of
# its registered $175k starting equity. The exchange liquidates below 50%.
TRADER_STARTING_EQUITY = 175_000.0
TRADER_GROSS_CAP = 70_000.0
TRADER_EQUITY_HALT = 0.70 * TRADER_STARTING_EQUITY
BUY_FEE_BUFFER = 0.005  # worst-case local allowance beyond published taker fee
AUCTION_EVENTS = ("SESSION_PREOPEN", "SESSION_PRECLOSE")   # auctions accept limit orders only; we stay out

# ④ IPO
IPO_CASH_BUFFER = 0.95        # subscribe with at most 95% of cash: an indication above cash is a reject

# ⑤⑥ Books and survival
BACKOFF_MIN_S, BACKOFF_MAX_S = 1.0, 30.0   # reconnect waits 1 s, 2 s, 4 s … up to 30 s, times a random jitter
STABLE_S = 30.0                             # a connection that lived 30 s was healthy: restart the backoff at 1 s
STATUS_EVERY_S = 30.0                       # log the books every 30 s
ACCRUAL_MAX_ABS, ACCRUAL_MAX_FRAC = 5.0, 1e-4   # largest cash gap per leaderboard row taken as interest/carry
DROP_AFTER_S = float(os.environ.get("DROP_AFTER_S", "0"))   # > 0: close the socket once after N s (reconnect evidence)
STATE_DIR = pathlib.Path(__file__).resolve().parent / "state"
LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"

# The wire: every exchange → bot frame is one model of this discriminated union on `type`
WIRE: TypeAdapter[AnyExchangeMessage] = TypeAdapter(AnyExchangeMessage)


class MyTrader(ShockSwitchTrader):
    """ShockSwitchTrader + SHOCK handling + reaction guarantee + reject-free orders + IPO.
    The strategy itself is the parent's event strategy plus the additions above."""

    def __init__(self) -> None:
        super().__init__()
        self._venue_tick = 0
        self.ramp_ticks = DEFAULT_RAMP_TICKS
        self.overshoot = DEFAULT_OVERSHOOT
        self._unanswered: list[tuple[int, set[str]]] = []   # events not yet answered: (venue_tick, symbols)
        self._inflight: dict[str, list] = {}                  # orders in flight: symbol → [side, unfilled qty, sent at]
        self._refs: dict[str, float] = {}                     # symbol → the exchange's fair price ref_price (for SHOCK)
        self._portfolio = None                                # set by _Bot: on_ipo gets no portfolio argument
        self._halted_symbols: set[str] = set()
        self._halt_until = {}
        self._market_halted = False
        self._week5_active = False  # avoid consuming risk in open play
        self._resume_pending: set[str] = set()
        self._resume_sent: set[str] = set()  # ACKs must correspond to POST-resume orders
        self._resume_retry_after: float = 0.0  # avoid rapid-fire rejected probes after reconnect

    # ------------------------------------------------------------------
    # Exchange events
    # ------------------------------------------------------------------

    def on_ipo(self, symbol: str, lo: float, hi: float, shares: int, data: dict):
        """IPO book opened. Return (quantity, max_price), or None to pass.
        Called once when the book opens (and again after a reconnect; a new
        indication replaces the old one).

        - Price: hi keeps the bid eligible at any offer within the range.
          Allocation still depends on demand and available cash; a positive
          allocation is not guaranteed.
        - Size: what this seat can still hold under its cap. Allocated shares
          skip the exchange's position check, but every later order (sells
          included) must respect the team limit, so never bid above the cap.
        """
        # Primary-market allocations bypass normal order risk checks. Week 5
        # has no IPO event; keep this unrelated exposure path closed.
        if self._week5_active:
            return None
        pf = self._portfolio
        pos = int(pf.positions.get(symbol, 0)) if pf else 0
        qty = min(SEAT_POSITION_CAP - max(pos, 0), shares)    # room left under the cap, at most the deal size
        if pf and hi > 0:
            qty = min(qty, int(pf.cash * IPO_CASH_BUFFER // hi))   # shares our cash can pay for
        if qty <= 0:
            logging.info("IPO %s: no room under the %d-share seat cap — passing",
                         symbol, SEAT_POSITION_CAP)
            return None
        logging.info("IPO %s: indicating %d up to $%.2f (range $%.2f–%.2f, %d offered)",
                     symbol, qty, hi, lo, hi, shares)
        return qty, hi

    def on_event(self, event: str, message: str, data: dict) -> None:
        """Market news arrives here (CALENDAR_EVENT, SHOCK, DIVIDEND, IPO_* …).
        The parent only handles CALENDAR_EVENT; we do our part first."""
        # The live server switches from open play to Week 5 at 17:00:20.
        # Throw away any old Week-0 trading plans, but NOT real inventory.
        if event == "WEEK_CHANGED":
            self._week5_active = int(data.get("week", 0)) == 5
            self.plans.clear()
            self._unanswered.clear()
            self._resume_pending.clear()
            self._resume_sent.clear()
            self._halted_symbols.clear()
            self._market_halted = False
            logging.info("RISK WEEK %02d — old plans cleared; graded=%s",
                         int(data.get("week", 0)), self._week5_active)
            return
        # Week 5: disable ALL new orders while a circuit breaker is active.
        if event == "TRADING_HALT":
            symbol = data.get("symbol")
            if symbol:
                self._halted_symbols.add(symbol)
                self._halt_until[symbol] = float(data.get("resume_at", float("inf")))
                self._resume_pending.discard(symbol)
                self._resume_sent.discard(symbol)
                logging.warning("RISK HALT %s — all orders paused", symbol)
            return
        if event == "TRADING_RESUMED":
            self._resume_retry_after = 0.0
            symbol = data.get("symbol")
            if symbol:
                self._halted_symbols.discard(symbol)
                self._resume_pending.add(symbol)
                self._resume_sent.discard(symbol)
                logging.info("RISK RESUMED %s — will submit a 1-share test order", symbol)
            return
        if event == "MARKET_HALT":
            self._market_halted = True
            logging.warning("RISK MARKET HALT — all orders paused")
            return
        if event == "MARKET_RESUMED":
            self._resume_retry_after = 0.0
            self._market_halted = False
            self._resume_pending.update(self._mids)
            self._resume_sent.clear()
            logging.info("RISK MARKET RESUMED — trading enabled")
            return
        if event == "LIQUIDATION":
            logging.info("LIQUIDATION notice team=%s ours=%s", data.get("team_id"),
                         data.get("team_id") == _config.TEAM_ID)
            # LIQUIDATION is broadcast to EVERY connected bot; do not stop
            # trading because someone else's account was liquidated.
            if data.get("team_id") == _config.TEAM_ID:
                logging.error("RISK CRITICAL: OUR account liquidated — stop entries")
                self._market_halted = True
            return
        # An event that names symbols → remember it for the ② reaction guarantee
        if data.get("tick") is not None:
            self._venue_tick = int(data["tick"])
        if event in ("CALENDAR_EVENT", "SHOCK") and data.get("affected"):
            self._unanswered.append((self._venue_tick, set(data["affected"])))
        # Remember the latest ramp shape; SHOCK needs it
        if event == "CALENDAR_EVENT":
            self.ramp_ticks = int(data.get("ramp_ticks") or self.ramp_ticks)
            self.overshoot = float(data.get("overshoot") or self.overshoot)
        if event == "SHOCK":
            self._on_shock(data)   # we translate SHOCK ourselves; the parent never sees it
            return
        super().on_event(event, message, data)

    def _on_shock(self, data: dict) -> None:
        """Turn a SHOCK (target prices, no pct) into one CALENDAR_EVENT per symbol,
        so the parent's RIDE/FADE logic runs unchanged.

        The exchange walks the fair price to the target, so pct = target / fair − 1.
        The book mid can sit far from the fair price, so ref_price comes first
        and the mid is only a fallback.
        """
        targets = data.get("prices") or {}             # symbol → target price
        for sym in data.get("affected") or []:
            pre = self._refs.get(sym) or self._mids.get(sym)
            target = targets.get(sym)
            if not pre or not target or sym in self.plans:
                continue    # unseen symbol / no target / a plan already running → skip
            pct = float(target) / pre - 1.0
            if abs(pct) > MAX_SHOCK_PCT:
                continue
            super().on_event("CALENDAR_EVENT", "", {
                "pct": pct, "ramp_ticks": self.ramp_ticks,
                "overshoot": self.overshoot, "affected": [sym],
            })

    # ------------------------------------------------------------------
    # Per-tick decision
    # ------------------------------------------------------------------

    def on_tick(self, market, portfolio):
        """Called every tick. Return at most one Signal, or None to sit out."""
        bot = getattr(self, "_bot", None)
        if bot is not None and (
            bot._resync or bot._first_book
        ):
            return None
        if self._market_halted:
            return None
        now = self.now()
        # 1. Keep unconfirmed orders reserved, regardless of network delay.
        # Never expire risk by wall clock: a delayed IOC may still execute.
        # Release its remainder only after the venue confirms its ID is absent.
        # 2. Correct the parent's count: in the derisk (sell-down) phase it uses core_sent as
        #    "shares sent to sell", but an IOC may not fill, so count only what actually sold
        #    plus what is still in flight
        for sym, p in self.plans.items():
            if p.state == "derisk":
                f = self._inflight.get(sym)
                p.core_sent = p.core_sold + (f[1] if f and f[0] == "sell" else 0)
        # 3. Ask the parent's strategy first; if it has nothing, check the ② reaction guarantee
        sig = (self._resumption_order(market, portfolio)
               or super().on_tick(market, portfolio)
               or self._react(market, portfolio))
        # 4. An order for this symbol is still in flight → wait for its fill (never double-send)
        if sig is None or sig.symbol in self._inflight or sig.symbol in self._halted_symbols:
            return None
        return sig

    def _step(self, plan, market, portfolio, nw, halted):
        bot = getattr(self, "_bot", None)
        if (plan.symbol in self._halted_symbols or self._market_halted or
                (bot is not None and plan.symbol in bot._entry_from_board)):
            return None, 0.0
        return super()._step(plan, market, portfolio, nw, halted)

    def _size(self, sym: str, px: float, nw: float, pos: int) -> int:
        """The parent's size, capped so the seat never exceeds its share of the team limit."""
        return max(0, min(super()._size(sym, px, nw, pos), SEAT_POSITION_CAP - max(pos, 0)))

    def _resumption_order(self, market, portfolio):
        """Prove the order path reopened without violating the Week 5 8% LULD collar.

        The pre-halt order book can be stale. Prefer a marketable 1-share IOC
        only when its price is within 6% of the venue reference. Otherwise send
        a passive IOC at 99% of the reference, which can ACK without filling.
        The final outbound risk guard still enforces cash / position / exposure.
        """
        if self.now() < self._resume_retry_after:
            return None
        for sym in sorted(self._resume_pending - self._resume_sent):
            if sym in self._halted_symbols or sym in self._inflight:
                continue
            bot = getattr(self, "_bot", None)
            if bot is not None and sym in bot._entry_from_board:
                continue
            ref = float(self._refs.get(sym) or market.mid_price(sym) or 0.0)
            if ref <= 0:
                continue
            # A reducing sell remains eligible at a full share/notional cap
            # or at the cash/equity entry floor. Prefer it for resumption.
            if int(portfolio.positions.get(sym, 0)) > 0:
                bid = market.best_bid(sym)
                price = (round(bid * .999, 2) if bid and .94 * ref <= bid <= 1.055 * ref
                         else round(ref * 1.01, 2))
                return Signal(symbol=sym, side="sell", quantity=1, price=price)
            ask = market.best_ask(sym)
            price = round(ask * 1.001, 2) if ask and 0.94 * ref <= ask <= 1.055 * ref else None
            if price and price <= 1.065 * ref and portfolio.can_buy(sym, 1, price * 1.01):
                return Signal(symbol=sym, side="buy", quantity=1, price=price)
            bid = market.best_bid(sym)
            if bid and int(portfolio.positions.get(sym, 0)) > 0 and 0.94 * ref <= bid <= 1.055 * ref:
                return Signal(symbol=sym, side="sell", quantity=1,
                              price=round(max(0.94 * ref, bid * 0.999), 2))
            # When the book is torn by the halt, an accepted passive IOC
            # is safer than blindly crossing a stale quote.
            price = round(ref * 0.99, 2)
            if price > 0 and portfolio.can_buy(sym, 1, price * 1.01):
                return Signal(symbol=sym, side="buy", quantity=1, price=price)
        return None

    def _react(self, market, portfolio):
        """② A 1-share bid well under the market for an event nothing else answered
        within REACT_AFTER_TICKS venue ticks."""
        now = self._venue_tick
        for t, syms in self._unanswered:
            if now - t < REACT_AFTER_TICKS:
                continue                    # too early: the parent may still trade it
            for sym in sorted(syms):
                if self._market_halted or sym in self._halted_symbols:
                    continue
                bot = getattr(self, "_bot", None)
                if bot is not None and sym in bot._entry_from_board:
                    continue
                ref = market.best_bid(sym) or self._mids.get(sym)
                if ref and portfolio.can_buy(sym, 1, ref):
                    return Signal(symbol=sym, side="buy", quantity=1,
                                  price=round(ref * (1 - REACT_BID_DISCOUNT), 2))
        return None

    # ------------------------------------------------------------------
    # Order callbacks (called by _Bot)
    # ------------------------------------------------------------------

    def on_sent(self, sig: Signal) -> None:
        """The order really went out → mark it in flight."""
        self._inflight[sig.symbol] = [sig.side, sig.quantity, self.now()]

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        """One of our orders traded (one order may fill in several pieces)."""
        super().on_fill(side, symbol, quantity, price)   # the parent updates its plan's share count
        f = self._inflight.get(symbol)
        if f and f[0] == side:
            f[1] -= quantity                 # take the filled part off the in-flight quantity
            if f[1] <= 0:
                f[1] = 0  # retain the slot until terminal confirmation
                # del self._inflight[symbol]   # fully filled → this symbol may send its next order

    def on_reconnect(self) -> None:
        """Avoid stale halt locks if TRADING_RESUMED occurred while disconnected."""
        self._inflight.clear()
        self._resume_sent.clear()
        if self._halted_symbols:
            self._resume_pending.update(self._halted_symbols)
            self._resume_retry_after = self.now() + 5.0
        # Preserve known halts across connection loss. A replayed market state
        # or TRADING_RESUMED is authoritative; connection is not resumption.
        # New socket first waits for a fresh portfolio and book snapshot.


    def run(self) -> None:
        """Connect and trade; log to the terminal (coloured) AND to logs/<seat>_<time>.log (plain)."""
        self._seat_lock = acquire_seat_lock(_config.TEAM_ID)
        LOG_DIR.mkdir(exist_ok=True)
        logfile = LOG_DIR / f"{_config.TEAM_ID}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        plain = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
        terminal = logging.StreamHandler()
        use_colour = terminal.stream.isatty() and "NO_COLOR" not in os.environ
        terminal.setFormatter(_ColourFormatter() if use_colour else plain)
        to_file = logging.FileHandler(logfile)
        to_file.setFormatter(plain)
        logging.basicConfig(level=logging.INFO, force=True, handlers=[terminal, to_file])
        _print_startup()
        logging.info("Session log → %s", logfile)
        asyncio.run(_Bot(self).run())


class _ColourFormatter(logging.Formatter):
    """Terminal-only colours: the level, plus the tag that starts each of our lines."""

    LEVELS = {"DEBUG": "2", "INFO": "32", "WARNING": "33", "ERROR": "31", "CRITICAL": "1;31"}
    TAGS = {
        "TICK": "36", "BOOKS": "1;34", "FILL": "1;35", "Order": "35", "ACK": "2",
        "CONNECT": "1;32", "RESYNC": "32", "RECONCILE": "1;32", "SESSION": "1;36",
        "DISCONNECT": "1;31", "RECONNECT": "33", "INDUCED": "1;33", "REJECT": "1;31",
        "IPO": "1;36", "Session": "33",
    }

    def __init__(self) -> None:
        super().__init__("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        body = super().format(record)                 # message (+ traceback, if any)
        tag, sep, rest = body.partition(" ")
        colour = self.TAGS.get(tag.rstrip(":"))
        if colour:
            body = f"\033[{colour}m{tag}\033[0m{sep}{rest}"
        lvl = record.levelname
        when = self.formatTime(record, "%H:%M:%S")
        return f"\033[2m{when}\033[0m \033[{self.LEVELS.get(lvl, '0')}m{lvl:<7}\033[0m {body}"


# ---------------------------------------------------------------------------
# _Bot: the SDK plumbing, changed where it would cause a reject or lose state
# ---------------------------------------------------------------------------

class _RefMarket(MarketData):
    """SDK MarketData that also keeps the exchange's ref_price.

    The SDK drops ref_price, and it builds a fresh MarketData on every connect,
    so wrapping update_book once in __init__ is lost the moment we connect.
    """

    def __init__(self, refs: dict[str, float]) -> None:
        super().__init__()
        self._refs = refs

    def update_book(self, snapshot: BookSnapshot) -> None:
        super().update_book(snapshot)
        if snapshot.ref_price:
            self._refs[snapshot.symbol] = snapshot.ref_price


class _Bot(_AdaptedTraderBot):
    """The stock plumbing, changed where it would cause a reject or lose state."""

    auction = False     # True = an auction is running, send nothing

    def __init__(self, owner: MyTrader) -> None:
        super().__init__(owner)
        owner._portfolio = self.portfolio   # lets on_ipo read cash and positions
        owner._bot = self
        self._state_path = STATE_DIR / f"{_config.TEAM_ID}.json"
        self.ledger = Ledger.load(self._state_path)
        self.rejects = 0
        self._connects = 0
        self._resync = False        # next PortfolioUpdate / leaderboard row: rebuild the books from the exchange
        self._has_server_snapshot = False  # don't trust the SDK's placeholder $100k on live connect
        self._first_book = False    # next BookSnapshot: log that the book was rebuilt
        self._closing = False       # next PortfolioUpdate / leaderboard row after the close: reconcile
        self._last_row: dict | None = None   # our latest leaderboard row = what the dashboard shows
        self._unknown_tags: set[str] = set()  # message types outside the union, logged once each
        self._ipo_pending: set[str] = set()
        self._ipo_prices: dict[str, float] = {}
        self._inferred_cash_adjustments = 0.0
        self._entry_from_board: set[str] = set()   # symbols whose entry the next leaderboard row sets
        self._t0 = time.monotonic()
        self._dropped = False
        self._acked_orders = set()
        self._terminal_orders = {}
        self._seen_trades = set()
        self._snapshot_at = 0.0
        self._board_tick = -1

    # ── Connection: exponential backoff + jitter ─────────────────────────

    async def run(self) -> None:
        """Connect and trade; on any drop, reconnect with exponential backoff."""
        delay = BACKOFF_MIN_S
        while True:
            up_since = time.monotonic()
            try:
                await self.connect()                            # sends the Handshake
                self._connects += 1
                logging.info("CONNECT #%d as %s", self._connects, _config.TEAM_ID)
                self._session_open = False                      # the exchange re-sends SESSION_OPEN to late joiners
                self.market = _RefMarket(self._owner._refs)     # the old book is stale: rebuild from new snapshots
                self._owner.on_reconnect()
                self._resync = self._first_book = True
                self._has_server_snapshot = False
                self._terminal_orders.clear()
                self._board_tick = -1
                tasks = [asyncio.create_task(self.trading_loop()),
                         asyncio.create_task(self._housekeeping())]
                try:
                    await self.listen()                         # returns or raises when the socket drops
                finally:
                    for t in tasks:
                        t.cancel()
                    for t in tasks:
                        with contextlib.suppress(asyncio.CancelledError):
                            await t
                logging.warning("DISCONNECT: exchange closed the connection")
            except websockets.exceptions.ConnectionClosed as exc:
                logging.warning("DISCONNECT: %s", exc)
            except OSError as exc:
                logging.warning("DISCONNECT: cannot reach exchange (%s)", exc)
            except Exception:
                logging.exception("DISCONNECT: unexpected error")
            finally:
                if self._ws:
                    with contextlib.suppress(Exception):
                        await self._ws.close()
            if time.monotonic() - up_since > STABLE_S:
                delay = BACKOFF_MIN_S                           # that connection was healthy → restart the backoff
            wait = delay * random.uniform(0.5, 1.0)             # jitter: don't reconnect in lockstep with every bot
            logging.warning("RECONNECT in %.1fs", wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, BACKOFF_MAX_S)

    # ── Incoming: the SDK dispatcher + ack/reject logging + books ────────

    async def listen(self) -> None:
        """Typed dispatch of every frame.

        Each raw frame goes straight through the discriminated union on `type`
        (WIRE.validate_json): one call picks the model and validates every
        field — no json.loads, no dict lookups. A bad or unknown frame is
        rejected at this boundary and never reaches the trading logic.
        """
        async for raw in self._ws:
            try:
                msg = WIRE.validate_json(raw)
            except ValidationError as exc:
                err = exc.errors()[0]
                if err["type"] == "union_tag_invalid":       # a type we don't model (e.g. teacher-only)
                    tag = err.get("ctx", {}).get("tag", "?")
                    if tag not in self._unknown_tags:
                        self._unknown_tags.add(tag)
                        logging.info("Ignoring unmodelled message type %r", tag)
                else:
                    logging.warning("Malformed frame dropped: %s at %s — %s",
                                    err["type"], err.get("loc"), err.get("msg"))
                continue
            if isinstance(msg, BookSnapshot):
                self.market.update_book(msg)
                if self._first_book:
                    self._first_book = False
                    logging.info("RESYNC book from first snapshot (%s bid %s ask %s)",
                                 msg.symbol, self.market.best_bid(msg.symbol),
                                 self.market.best_ask(msg.symbol))
            elif isinstance(msg, TradeExecution):
                self.market.update_trade(msg)
                self._on_trade(msg)
            elif isinstance(msg, PortfolioUpdate):
                if msg.team_id != _config.TEAM_ID:
                    continue
                self.portfolio.apply_server_update(msg)
                self._on_portfolio(msg)
            elif isinstance(msg, SessionEvent):
                await self._on_session_event(msg)
            elif isinstance(msg, OrderAck):
                if msg.team_id != _config.TEAM_ID:
                    continue
                if msg.order_id in self._acked_orders:
                    continue
                self._acked_orders.add(msg.order_id)
                self._terminal_orders[msg.order_id] = msg.symbol
                # Same socket requests are processed serially. IOC has finished
                # matching before this cancel is handled. ORDER_NOT_FOUND for
                # this exact ID proves all its fill messages preceded the reply.
                await self._send(CancelOrder(team_id=_config.TEAM_ID,
                    symbol=msg.symbol, order_id=msg.order_id))
                self._owner._unanswered = [
                    (tick, symbols) for tick, symbols in self._owner._unanswered
                    if msg.symbol not in symbols
                ]
                if msg.symbol in self._owner._resume_sent:
                    self._owner._resume_sent.discard(msg.symbol)
                    self._owner._resume_pending.discard(msg.symbol)
                    logging.info("RISK RESUME CONFIRMED %s via post-resume order ACK", msg.symbol)
                logging.info("ACK %s %s %d %s @ %.2f", msg.order_id[:8],
                             msg.side, msg.quantity, msg.symbol, msg.price)
            elif isinstance(msg, ErrorMsg):
                if self._confirm_terminal(msg):
                    continue
                self._has_server_snapshot = False
                if self._ws is not None:
                    await self._ws.close()
                self.rejects += 1
                logging.warning("REJECT #%d [%s] %s", self.rejects, msg.code, msg.message)
                # An IOC reject has no order_id on the wire. Permit a later
                # resumption retry rather than claiming the rejected probe
                # established that trading reopened.
                self._owner._resume_sent.clear()
                if self._owner._resume_pending:
                    self._owner._resume_retry_after = self._owner.now() + 5.0
            elif isinstance(msg, Leaderboard):
                self._on_leaderboard(msg)

    def _confirm_terminal(self, msg):
        if msg.code != "ORDER_NOT_FOUND":
            return False
        for oid, sym in list(self._terminal_orders.items()):
            if repr(oid) in msg.message:
                self._terminal_orders.pop(oid)
                self._owner._inflight.pop(sym, None)
                logging.info("IOC COMPLETE %s %s", sym, oid[:8])
                return True
        return False

    def _on_trade(self, msg: TradeExecution) -> None:
        """A trade printed. If it is ours: books first (authoritative), then patch
        the SDK portfolio at once.

        The exchange's PortfolioUpdate lags by a few ticks; without the patch the
        next sell would be sized on a stale position and could turn into a short.
        """
        me = _config.TEAM_ID
        if msg.trade_id in self._seen_trades:
            return
        if me not in (msg.buyer_id, msg.seller_id):
            return                               # someone else's trade
        self._seen_trades.add(msg.trade_id)
        side = "buy" if msg.buyer_id == me else "sell"
        # The stock _on_trade only logs (with fee / 2, which is wrong: fee is the
        # full taker fee) and calls on_fill — so call on_fill directly instead
        self._owner.on_fill(side, msg.symbol, msg.quantity, msg.price)
        fee = msg.fee if msg.taker_id == me else (-msg.maker_rebate if msg.maker_id == me else 0.0)
        self.ledger.apply_fill(side, msg.symbol, msg.quantity, msg.price, fee)
        d = msg.quantity if side == "buy" else -msg.quantity
        self.portfolio.positions[msg.symbol] = self.portfolio.positions.get(msg.symbol, 0) + d
        self.portfolio.cash -= d * msg.price + fee
        refs = self._owner._refs
        logging.info("FILL %s %d %s @ %.2f (fee %.2f) → %s · %s", side, msg.quantity, msg.symbol,
                     msg.price, fee, self.ledger.position_line(msg.symbol, refs),
                     self.ledger.summary(refs))

    def _book_ipo_allocations(self) -> None:
        """Use the announced offer price, even while other symbols have orders in flight."""
        for sym, offer in list(self._ipo_prices.items()):
            if sym not in self._ipo_pending:
                continue
            qty = (self.portfolio.positions.get(sym, 0)
                   - self.ledger.positions.get(sym, 0))
            if qty <= 0:
                continue
            self.ledger.apply_allocation(sym, qty, offer)
            self._ipo_pending.discard(sym)
            self._ipo_prices.pop(sym, None)
            self._entry_from_board.discard(sym)
            logging.info("IPO ALLOCATED %d %s @ %.2f", qty, sym, offer)

    def _on_portfolio(self, msg: PortfolioUpdate) -> None:
        """Recover state, book confirmed IPO allocations and starting grants."""
        if msg.team_id != _config.TEAM_ID:
            return
        self._snapshot_at = time.monotonic()
        state = self._update_state(msg)
        self._has_server_snapshot = True
        self.portfolio.apply_server_update(msg)
        if self._resync:
            self._sync("PortfolioUpdate", state)
            return
        self._book_ipo_allocations()
        moved = {
            sym: msg.positions.get(sym, 0) - self.ledger.positions.get(sym, 0)
            for sym in set(msg.positions) | set(self.ledger.positions)
        }
        moved = {sym: qty for sym, qty in moved.items() if qty}
        if (moved and not self._owner._inflight and not self._session_open
                and not (set(moved) & self._ipo_pending)
                and all(qty > 0 for qty in moved.values())):
            for sym, qty in sorted(moved.items()):
                self.ledger.apply_grant(sym, qty, self._owner._refs.get(sym, 0.0))
                self._entry_from_board.add(sym)
            logging.info("GRANT starting shares: %s", moved)
        if self._closing:
            logging.info("Close reconciliation awaits a leaderboard with entry prices")

    def _on_leaderboard(self, msg: Leaderboard) -> None:
        """Keep our own leaderboard row: cash, positions, avg_cost, realized P&L, fees.

        The exchange sends no PortfolioUpdate when an existing seat reconnects
        (only on fills, dividends, IPO allocations…), but it broadcasts the
        leaderboard every tick, and our row is exactly what the dashboard shows.
        Also logs one TICK line per broadcast (replaces the stock rich print,
        whose rank was always "#?": the rows carry no rank, the list order is it).
        """
        if msg.tick < self._board_tick:
            return
        self._board_tick = msg.tick
        self._owner._venue_tick = msg.tick
        for board in (msg.traders, msg.brokers):     # each list is sorted by net worth
            rank = next((i for i, e in enumerate(board, 1)
                         if e.get("team_id") == _config.TEAM_ID), None)
            if rank is not None:
                row = board[rank - 1]
                break
        else:
            return
        self._snapshot_at = time.monotonic()
        self._last_row = row
        self._has_server_snapshot = True
        self.portfolio.apply_server_update(PortfolioUpdate(
            team_id=_config.TEAM_ID,
            cash=float(row["cash"]),
            positions=dict(row["positions"]),
            realized_pnl=float(row.get("realized_pnl", 0.0)),
            unrealized_pnl=float(row.get("unrealized_pnl", 0.0)),
            total_fees_paid=float(row.get("total_fees_paid", 0.0)),
            total_rebates_earned=float(row.get("total_rebates_earned", 0.0)),
            net_worth=float(row.get("net_worth", self.portfolio._net_worth)),
            **{
                name: row[name]
                for name in (
                    "total_carry_paid", "liquidated", "gross_exposure",
                    "leverage", "margin_ratio", "borrowed", "max_leverage"
                )
                if name in row
            },
        ))
        logging.info("TICK %d · rank %d/%d · net_worth %s · realized %s · unrealized %s",
                     msg.tick, rank, len(board), f"{row.get('net_worth', 0.0):,.2f}",
                     f"{row.get('realized_pnl', 0.0):+,.2f}", f"{row.get('unrealized_pnl', 0.0):+,.2f}")
        log_state(_config.TEAM_ID, self.portfolio.positions, self._owner._refs,
                  self.portfolio.cash, self.portfolio._net_worth, msg.traders + msg.brokers)
        state = self._row_state(row)
        if self._resync:
            self._sync("leaderboard", state)
            return
        self._book_ipo_allocations()
        # Grants / IPO allocations: the exchange's own book cost replaces our mark
        for sym in list(self._entry_from_board):
            if (
                not self.ledger.positions.get(sym)
                and not state["positions"].get(sym)
            ):
                self._entry_from_board.discard(sym)

            elif (
                state["avg_cost"].get(sym) is not None
                and self.ledger.positions.get(sym)
                == state["positions"].get(sym)
            ):
                self.ledger.avg_entry[sym] = state["avg_cost"][sym]
                self._entry_from_board.discard(sym)
        # The venue does not publish each interest/carry cash flow. Retain
        # bounded snapshot residuals, explicitly labelled as inferred adjustments.
        gap = state["cash"] - self.ledger.cash
        limit = max(ACCRUAL_MAX_ABS, ACCRUAL_MAX_FRAC * abs(self.ledger.cash))
        if gap and not self._owner._inflight and abs(gap) <= limit:
            self._inferred_cash_adjustments += gap
            logging.info(
                "INFERRED_CASH_ADJUSTMENT delta=%+.4f cumulative_this_run=%+.4f "
                "source=snapshot_residual (not independently verified interest)",
                gap, self._inferred_cash_adjustments,
            )
            self.ledger.accrue(gap)
        if self._closing:
            self._reconcile_close("leaderboard", state)

    @staticmethod
    def _row_state(row: dict) -> dict:
        """The exchange's books from a leaderboard row (the dashboard's numbers)."""
        return {"cash": float(row.get("cash", 0.0)),
                "positions": dict(row.get("positions") or {}),
                "realized_pnl": float(row.get("realized_pnl", 0.0)),
                "avg_cost": dict(row.get("avg_cost") or {}),
                "fees": float(row.get("total_fees_paid", 0.0))
                        - float(row.get("total_rebates_earned", 0.0))}

    @staticmethod
    def _update_state(msg: PortfolioUpdate) -> dict:
        """The exchange's books from a PortfolioUpdate (no entry prices in it)."""
        return {"cash": msg.cash, "positions": dict(msg.positions),
                "realized_pnl": msg.realized_pnl,
                "fees": msg.total_fees_paid - msg.total_rebates_earned}

    def _sync(self, source: str, state: dict) -> None:
        """Adopt the exchange's books after a (re)connect and log what differed."""
        diffs = self.ledger.sync_from(marks=self._owner._refs, **state)

        entries = state.get("avg_cost") or {}
        self._entry_from_board.update(
            set(self.ledger.positions) - set(entries)
        )
        self._entry_from_board.difference_update(entries)

        self._resync = False
        if diffs:
            logging.info("RESYNC from %s — adopted %d difference(s):\n%s", source, len(diffs),
                         "\n".join(f"    {d}" for d in diffs))
        else:
            logging.info("RESYNC from %s — books already agree", source)
        self._log_books()

    def _reconcile_close(self, source: str, state: dict) -> None:
        """Check accounting and P&L at the leaderboard's valuation marks."""
        row = self._last_row or {}
        logging.info(
            "CASH_AUDIT inferred_adjustments_this_run=%+.4f "
            "recorded_nontrade_flows=%+.4f residual_after_adjustments=%+.4f",
            self._inferred_cash_adjustments, self.ledger.accruals,
            state["cash"] - self.ledger.cash,
        )
        diffs = self.ledger.reconcile(**state)

        marks = {}
        missing = set()
        by_symbol = row.get("unrealized_by_sym") or {}

        for sym, qty in state["positions"].items():
            if not qty:
                continue

            if (
                sym not in state.get("avg_cost", {})
                or sym not in by_symbol
            ):
                missing.add(sym)
                continue

            # Recover the venue's mark from its per-symbol valuation:
            # unrealized = (mark - average_cost) * quantity.
            marks[sym] = (
                state["avg_cost"][sym]
                + float(by_symbol[sym]) / qty
            )

        missing.update(set(self.ledger.positions) - set(marks))
        expected = row.get("unrealized_pnl")

        if expected is None:
            missing.add("unrealized_pnl")

        if missing:
            logging.warning(
                "RECONCILE at close — INCOMPLETE valuation data: %s; "
                "accounting differences: %s",
                sorted(missing), diffs,
            )
            self.ledger.save(self._state_path)
            return

        self._closing = False
        actual = self.ledger.unrealized_pnl(marks)

        if abs(actual - float(expected)) > 1.0:
            diffs.append(
                f"unrealized: ours {actual:+.2f} "
                f"vs exchange {expected:+.2f}"
            )

        if diffs:
            logging.warning(
                "RECONCILE at close vs %s — MISMATCH: %s",
                source, diffs,
            )
        else:
            logging.info(
                "RECONCILE at close vs %s — OK: accounting and "
                "unrealized P&L match at venue valuation marks "
                "(ours %+.2f, exchange %+.2f)",
                source, actual, expected,
            )

        self.ledger.save(self._state_path)

    def _check(self) -> str:
        """Our books vs the latest leaderboard row, for the BOOKS header."""
        if self._resync:
            return "waiting for the first exchange snapshot"
        if self._last_row is None:
            return "no leaderboard row yet"
        nw = f"net_worth {self._last_row.get('net_worth', 0.0):,.2f}"
        if self._owner._inflight:
            return f"{nw} · check skipped (order in flight)"
        diffs = self.ledger.reconcile(**self._row_state(self._last_row))
        return f"{nw} · " + ("accounting fields match (cash includes disclosed adjustments)" if not diffs else "MISMATCH: " + "; ".join(diffs))

    def _log_books(self, title: str = "BOOKS") -> None:
        """The books as an aligned table, headed by the exchange check."""
        logging.info("%s · %s\n%s", title, self._check(), self.ledger.table(self._owner._refs))

    async def _housekeeping(self) -> None:
        """Every second: periodic books line, state save, optional induced drop."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            if now - last >= STATUS_EVERY_S:
                last = now
                if self._resync:
                    logging.info("BOOKS · %s", self._check())
                else:
                    self.ledger.seed_missing_entries(self._owner._refs)
                    self._log_books()
                    self.ledger.save(self._state_path)
            if DROP_AFTER_S and not self._dropped and now - self._t0 >= DROP_AFTER_S:
                self._dropped = True
                logging.warning("INDUCED DROP after %.0fs — closing the socket", DROP_AFTER_S)
                await self._ws.close()

    # ── Orders and session events ─────────────────────────────────────────

    async def _place_order(self, signal: Signal) -> None:
        """Apply final position guards, then send a typed IOC order."""
        if (
            self.auction
            or not self._owner._week5_active
            or not self._session_open
            or self._resync
            or self._first_book
            or signal.symbol in self._entry_from_board
            or self._owner._market_halted
            or signal.symbol in self._owner._halted_symbols
            or not self._has_server_snapshot
            or time.monotonic() - self._snapshot_at > 10.0
            or getattr(self.portfolio, "liquidated", False)
        ):
            return

        if self._owner._inflight:
            return

        if not math.isfinite(signal.price) or signal.price <= 0:
            return
        pos = self.portfolio.positions.get(signal.symbol, 0)

        if signal.side == "buy":
            # A hard limit checked at the FINAL outbound order boundary, not
            # just when generating a signal. Long-only and cash-funded.
            refs = self._owner._refs
            marks = {sym: (refs.get(sym) or self.market.mid_price(sym) or 0.0)
                     for sym, q in self.portfolio.positions.items() if q}
            # Unknown marks cannot be treated as zero risk.
            if any(px <= 0 for px in marks.values()):
                logging.warning("RISK UNKNOWN MARK: refusing new buy until positions priced")
                return
            gross = sum(abs(int(q)) * marks[sym]
                        for sym, q in self.portfolio.positions.items() if q)
            # Reserve risk for IOC orders already sent on OTHER symbols but
            # not yet filled or ACKed. Otherwise concurrent events can each
            # spend the same gross/cash budget.
            pending_buy = sum(
                max(0, f[1]) * (f[3] if len(f) > 3 else (refs.get(sym) or self.market.mid_price(sym) or 0.0))
                for sym, f in self._owner._inflight.items() if f[0] == "buy"
            )
            if any((refs.get(sym) or self.market.mid_price(sym) or 0) <= 0
                   for sym, f in self._owner._inflight.items() if f[0] == "buy"):
                return
            fee = max(float(self._owner.taker), BUY_FEE_BUFFER)
            available_cash = max(0.0, (self.portfolio.cash - 0.60 * TRADER_STARTING_EQUITY - pending_buy * (1 + fee)) * 0.95)
            gross_room = max(0.0, TRADER_GROSS_CAP - gross - pending_buy)
            equity = self.portfolio.net_worth(self.market)
            if self._has_server_snapshot and (not equity or equity < TRADER_EQUITY_HALT):
                return
            qty = min(
                signal.quantity,
                max(0, SEAT_POSITION_CAP - pos),
                int(gross_room // max(signal.price, refs.get(signal.symbol, signal.price))) if signal.price > 0 else 0,
                int(available_cash // (signal.price * (1 + fee)))
                if signal.price > 0 else 0,
            )
        else:
            # This strategy sells existing long inventory.
            qty = min(signal.quantity, max(0, pos))

        if qty <= 0:
            return
        # Week 5 limits limit/IOC orders to +/-8% of the reference. Reject
        # our own stale or invalid prices before risking a venue PRICE_BAND.
        ref = self._owner._refs.get(signal.symbol) or self.market.mid_price(signal.symbol)
        if not ref or signal.price <= 0 or not 0.93 * ref <= signal.price <= 1.07 * ref:
            logging.info("RISK PRICE BAND skip %s %s @ %.2f ref=%.2f",
                         signal.symbol, signal.side, signal.price, ref or 0)
            return

        signal = signal.model_copy(update={"quantity": qty})
        self._owner.on_sent(signal)
        self._owner._inflight[signal.symbol].append(signal.price)
        if signal.symbol in self._owner._resume_pending:
            self._owner._resume_sent.add(signal.symbol)

        logging.info(
            "Order sent: %s %d %s @ %.2f (IOC)",
            signal.side, qty, signal.symbol, signal.price,
        )

        await self._send(PlaceOrder(
            team_id=_config.TEAM_ID,
            symbol=signal.symbol,
            side=signal.side,
            order_type="ioc",
            price=signal.price,
            quantity=qty,
        ))

    async def _on_session_event(self, msg) -> None:
        """Auction gate, IPO/dividend bookkeeping, close-of-session reconcile."""
        d = msg.data or {}
        if msg.event == "MARKET_STATE":
            previously = set(self._owner._halted_symbols)
            market_was_halted = self._owner._market_halted
            self._owner._halted_symbols = set(d.get("halted_symbols", []))
            self._owner._market_halted = bool(d.get("market_halted", False))
            self._owner._resume_pending.update(previously - self._owner._halted_symbols)
            if market_was_halted and not self._owner._market_halted:
                self._owner._resume_pending.update(self._owner._refs)
        if msg.event == "SESSION_OPEN":
            # A reconnect does not necessarily replay WEEK_CHANGED.
            week = d.get("week", (d.get("scenario") or {}).get("week"))
            if week is not None:
                self._owner._week5_active = int(week) == 5
                logging.info("RISK SESSION WEEK %02d graded=%s",
                             int(week), self._owner._week5_active)
        if msg.event in AUCTION_EVENTS:
            self.auction = True
        elif msg.event in ("SESSION_OPEN", "SESSION_CLOSED"):
            self.auction = False
        if msg.event == "IPO_OPEN" and d.get("symbol"):
            self._ipo_pending.add(d["symbol"])
        elif msg.event == "IPO_PRICED":
            sym, offer = d.get("symbol"), d.get("offer_price")
            if sym and offer is not None and float(offer) > 0:
                self._ipo_prices[sym] = float(offer)
                self._ipo_pending.add(sym)
                self._book_ipo_allocations()
        elif msg.event == "IPO_LISTED":
            self._ipo_pending.discard(d.get("symbol", ""))
        elif msg.event == "DIVIDEND" and d.get("symbol"):
            self.ledger.apply_dividend(d["symbol"], float(d.get("amount_per_share", 0)))
        elif msg.event == "SESSION_CLOSED":
            # Reconcile on the next exchange snapshot after the close (the books are final by then)
            self._closing = True
            self._log_books("BOOKS at close")
            logging.info("SESSION SUMMARY connects=%d rejects=%d", self._connects, self.rejects)
        await super()._on_session_event(msg)

    async def flatten_all_positions(self) -> None:
        """Disabled. The stock version sends market orders after the close, and an
        order after the close is always rejected (the 10 rejects per seat in the
        week-2 recording were exactly this)."""


if __name__ == "__main__":
    MyTrader().run()
