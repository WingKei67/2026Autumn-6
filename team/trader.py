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
    ② Reaction guarantee: at least one order within REACT_AFTER_S seconds of
       every event (course rule)
    ③ No rejects:
         - IOC orders only: any unfilled part is cancelled at once and never
           piles up on the book
         - at most one order in flight per symbol (sent, fill not yet back)
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
                             PlaceOrder, PortfolioUpdate, SessionEvent, TradeExecution)
from team.ledger import Ledger
from week2.week02_alpha_bot import ShockSwitchTrader

# ① A SHOCK carries no ramp shape (ramp_ticks / overshoot): reuse the last CALENDAR_EVENT's
DEFAULT_RAMP_TICKS = 15       # before any CALENDAR_EVENT has been seen (exchange default)
DEFAULT_OVERSHOOT = 1.3
MAX_SHOCK_PCT = 0.20          # a derived move above 20% means our price is stale → don't trade

# ② Reaction guarantee
REACT_AFTER_S = 20.0          # no order for an event after 20 s → send one
REACT_BID_DISCOUNT = 0.02     # that order: a 1-share IOC bid 2% under the best bid (won't fill, just "reacts")

# ③ No rejects
INFLIGHT_TTL_S = 0.3          # the exchange acks, then fills: 0.3 s after sending, the unfilled rest is cancelled
SEAT_POSITION_CAP = int(os.environ.get("SEAT_POSITION_CAP", "150"))   # max shares this seat holds per symbol
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
        self.ramp_ticks = DEFAULT_RAMP_TICKS
        self.overshoot = DEFAULT_OVERSHOOT
        self._unanswered: list[tuple[float, set[str]]] = []   # events not yet answered: (time, symbols)
        self._inflight: dict[str, list] = {}                  # orders in flight: symbol → [side, unfilled qty, sent at]
        self._refs: dict[str, float] = {}                     # symbol → the exchange's fair price ref_price (for SHOCK)
        self._portfolio = None                                # set by _Bot: on_ipo gets no portfolio argument

    # ------------------------------------------------------------------
    # Exchange events
    # ------------------------------------------------------------------

    def on_ipo(self, symbol: str, lo: float, hi: float, shares: int, data: dict):
        """IPO book opened. Return (quantity, max_price), or None to pass.
        Called once when the book opens (and again after a reconnect; a new
        indication replaces the old one).

        - Price: the top of the range, hi → we are sure to be allocated
          (an oversubscribed book is cut back pro rata, so we can only get
          fewer shares, never more)
        - Size: what this seat can still hold under its cap. Allocated shares
          skip the exchange's position check, but every later order (sells
          included) must respect the team limit, so never bid above the cap.
        """
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
        # An event that names symbols → remember it for the ② reaction guarantee
        if event in ("CALENDAR_EVENT", "SHOCK") and data.get("affected"):
            self._unanswered.append((self.now(), set(data["affected"])))
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
        now = self.now()
        # 1. Forget orders in flight for longer than INFLIGHT_TTL_S: the IOC's unfilled rest is long cancelled
        self._inflight = {s: f for s, f in self._inflight.items() if now - f[2] < INFLIGHT_TTL_S}
        # 2. Correct the parent's count: in the derisk (sell-down) phase it uses core_sent as
        #    "shares sent to sell", but an IOC may not fill, so count only what actually sold
        #    plus what is still in flight
        for sym, p in self.plans.items():
            if p.state == "derisk":
                f = self._inflight.get(sym)
                p.core_sent = p.core_sold + (f[1] if f and f[0] == "sell" else 0)
        # 3. Ask the parent's strategy first; if it has nothing, check the ② reaction guarantee
        sig = super().on_tick(market, portfolio) or self._react(market, portfolio)
        # 4. An order for this symbol is still in flight → wait for its fill (never double-send)
        if sig is None or sig.symbol in self._inflight:
            return None
        # 5. An order for this symbol counts as a reaction to every event that named it
        self._unanswered = [(t, s) for t, s in self._unanswered if sig.symbol not in s]
        return sig

    def _size(self, sym: str, px: float, nw: float, pos: int) -> int:
        """The parent's size, capped so the seat never exceeds its share of the team limit."""
        return max(0, min(super()._size(sym, px, nw, pos), SEAT_POSITION_CAP - max(pos, 0)))

    def _react(self, market, portfolio):
        """② A 1-share bid well under the market for an event nothing else answered
        within REACT_AFTER_S seconds."""
        now = self.now()
        for t, syms in self._unanswered:
            if now - t < REACT_AFTER_S:
                continue                    # too early: the parent may still trade it
            for sym in sorted(syms):
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
                del self._inflight[symbol]   # fully filled → this symbol may send its next order

    def on_reconnect(self) -> None:
        """New socket: orders in flight on the old one are gone (an IOC is already done)."""
        self._inflight.clear()

    def run(self) -> None:
        """Connect and trade; log to the terminal (coloured) AND to logs/<seat>_<time>.log (plain)."""
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
        self._state_path = STATE_DIR / f"{_config.TEAM_ID}.json"
        self.ledger = Ledger.load(self._state_path)
        self.rejects = 0
        self._connects = 0
        self._resync = False        # next PortfolioUpdate / leaderboard row: rebuild the books from the exchange
        self._first_book = False    # next BookSnapshot: log that the book was rebuilt
        self._closing = False       # next PortfolioUpdate / leaderboard row after the close: reconcile
        self._last_row: dict | None = None   # our latest leaderboard row = what the dashboard shows
        self._unknown_tags: set[str] = set()  # message types outside the union, logged once each
        self._ipo_pending: set[str] = set()
        self._entry_from_board: set[str] = set()   # symbols whose entry the next leaderboard row sets
        self._t0 = time.monotonic()
        self._dropped = False

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
                self.portfolio.apply_server_update(msg)
                self._on_portfolio(msg)
            elif isinstance(msg, SessionEvent):
                await self._on_session_event(msg)
            elif isinstance(msg, OrderAck):
                logging.info("ACK %s %s %d %s @ %.2f", msg.order_id[:8],
                             msg.side, msg.quantity, msg.symbol, msg.price)
            elif isinstance(msg, ErrorMsg):
                self.rejects += 1
                logging.warning("REJECT #%d [%s] %s", self.rejects, msg.code, msg.message)
            elif isinstance(msg, Leaderboard):
                self._on_leaderboard(msg)

    def _on_trade(self, msg: TradeExecution) -> None:
        """A trade printed. If it is ours: books first (authoritative), then patch
        the SDK portfolio at once.

        The exchange's PortfolioUpdate lags by a few ticks; without the patch the
        next sell would be sized on a stale position and could turn into a short.
        """
        me = _config.TEAM_ID
        if me not in (msg.buyer_id, msg.seller_id):
            return                               # someone else's trade
        side = "buy" if msg.buyer_id == me else "sell"
        # The stock _on_trade only logs (with fee / 2, which is wrong: fee is the
        # full taker fee) and calls on_fill — so call on_fill directly instead
        self._owner.on_fill(side, msg.symbol, msg.quantity, msg.price)
        fee = msg.fee if msg.taker_id == me else (-msg.maker_rebate if msg.maker_id == me else 0.0)
        self.ledger.apply_fill(side, msg.symbol, msg.quantity, msg.price, fee)
        d = msg.quantity if side == "buy" else -msg.quantity
        self.portfolio.positions[msg.symbol] = self.portfolio.positions.get(msg.symbol, 0) + d
        self.portfolio.cash -= d * msg.price
        refs = self._owner._refs
        logging.info("FILL %s %d %s @ %.2f (fee %.2f) → %s · %s", side, msg.quantity, msg.symbol,
                     msg.price, fee, self.ledger.position_line(msg.symbol, refs),
                     self.ledger.summary(refs))

    def _on_portfolio(self, msg: PortfolioUpdate) -> None:
        """Resync after a connect, book non-trade position changes, reconcile at the close.

        The exchange pushes a PortfolioUpdate after each of our fills and on
        every change it makes without a trade print. A position change that no
        fill explains is booked only for a known reason — an IPO allocation, or
        the starting-share grant (sent just before the pre-open / open event) —
        and anything else is left to show up as a MISMATCH, because it is a bug.
        """
        state = self._update_state(msg)
        if self._resync:
            self._sync("PortfolioUpdate", state)
            return
        moved = {s: msg.positions.get(s, 0) - self.ledger.positions.get(s, 0)
                 for s in set(msg.positions) | set(self.ledger.positions)}
        moved = {s: d for s, d in moved.items() if d}
        if moved and not self._owner._inflight:
            if set(moved) <= self._ipo_pending:
                for sym, got in moved.items():
                    offer = round((self.ledger.cash - msg.cash) / got, 2)
                    self.ledger.apply_allocation(sym, got, offer)
                    self._ipo_pending.discard(sym)
                    self._entry_from_board.add(sym)
                    logging.info("IPO ALLOCATED %d %s @ %.2f", got, sym, offer)
            elif not self._session_open and all(d > 0 for d in moved.values()):
                for sym, got in sorted(moved.items()):
                    self.ledger.apply_grant(sym, got, self._owner._refs.get(sym, 0.0))
                    self._entry_from_board.add(sym)
                logging.info("GRANT starting shares (no trade print): %s",
                             ", ".join(f"+{d} {s}" for s, d in sorted(moved.items())))
        if self._closing:
            self._reconcile_close("PortfolioUpdate", state)

    def _on_leaderboard(self, msg: Leaderboard) -> None:
        """Keep our own leaderboard row: cash, positions, avg_cost, realized P&L, fees.

        The exchange sends no PortfolioUpdate when an existing seat reconnects
        (only on fills, dividends, IPO allocations…), but it broadcasts the
        leaderboard every tick, and our row is exactly what the dashboard shows.
        Also logs one TICK line per broadcast (replaces the stock rich print,
        whose rank was always "#?": the rows carry no rank, the list order is it).
        """
        for board in (msg.traders, msg.brokers):     # each list is sorted by net worth
            rank = next((i for i, e in enumerate(board, 1)
                         if e.get("team_id") == _config.TEAM_ID), None)
            if rank is not None:
                row = board[rank - 1]
                break
        else:
            return
        self._last_row = row
        logging.info("TICK %d · rank %d/%d · net_worth %s · realized %s · unrealized %s",
                     msg.tick, rank, len(board), f"{row.get('net_worth', 0.0):,.2f}",
                     f"{row.get('realized_pnl', 0.0):+,.2f}", f"{row.get('unrealized_pnl', 0.0):+,.2f}")
        state = self._row_state(row)
        if self._resync:
            self._sync("leaderboard", state)
            return
        # Grants / IPO allocations: the exchange's own book cost replaces our mark
        for sym in list(self._entry_from_board):
            if state["avg_cost"].get(sym):
                self.ledger.avg_entry[sym] = state["avg_cost"][sym]
                self._entry_from_board.discard(sym)
        # Interest on idle cash and margin carry accrue every tick with no message:
        # take the cash gap as a non-trade flow — but only a plausible one
        gap = state["cash"] - self.ledger.cash
        limit = max(ACCRUAL_MAX_ABS, ACCRUAL_MAX_FRAC * abs(self.ledger.cash))
        if gap and not self._owner._inflight and abs(gap) <= limit:
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
        self._resync = False
        diffs = self.ledger.sync_from(marks=self._owner._refs, **state)
        if diffs:
            logging.info("RESYNC from %s — adopted %d difference(s):\n%s", source, len(diffs),
                         "\n".join(f"    {d}" for d in diffs))
        else:
            logging.info("RESYNC from %s — books already agree", source)
        self._log_books()

    def _reconcile_close(self, source: str, state: dict) -> None:
        """Compare our books with the exchange's after the close. A mismatch is a bug."""
        self._closing = False
        diffs = self.ledger.reconcile(**state)
        if diffs:
            logging.warning("RECONCILE at close vs %s — %d MISMATCH(ES):\n%s", source, len(diffs),
                            "\n".join(f"    {d}" for d in diffs))
        else:
            logging.info("RECONCILE at close vs %s — OK: positions, entries, cash, fees and "
                         "realized P&L match the exchange (non-trade cash flows %+.2f)",
                         source, self.ledger.accruals)
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
        return f"{nw} · " + ("matches exchange" if not diffs else "MISMATCH: " + "; ".join(diffs))

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
        """Send the order as IOC (the stock plumbing sends a resting limit order)."""
        if self.auction:
            return              # auctions accept limit orders only; an IOC would be rejected
        self._owner.on_sent(signal)
        logging.info("Order sent: %s %d %s @ %.2f (IOC)",
                     signal.side, signal.quantity, signal.symbol, signal.price)
        await self._send(PlaceOrder(
            team_id=_config.TEAM_ID, symbol=signal.symbol, side=signal.side,
            order_type="ioc", price=signal.price, quantity=signal.quantity,
        ))

    async def _on_session_event(self, msg) -> None:
        """Auction gate, IPO/dividend bookkeeping, close-of-session reconcile."""
        d = msg.data or {}
        if msg.event in AUCTION_EVENTS:
            self.auction = True
        elif msg.event in ("SESSION_OPEN", "SESSION_CLOSED"):
            self.auction = False
        if msg.event == "IPO_OPEN" and d.get("symbol"):
            self._ipo_pending.add(d["symbol"])
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
