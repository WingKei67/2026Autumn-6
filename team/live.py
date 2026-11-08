"""
team/live.py — the live connection for team/trader.py (Phase 1 work, unchanged).

Everything here is plumbing, not strategy:
  * reconnect with exponential backoff + jitter, rebuild the book after a drop
  * typed wire: every frame validated against the discriminated union
  * our own books (team/ledger.py), resynced on connect, reconciled at the close
  * orders sent as IOC; nothing during auctions or after the close
  * a log file per run: logs/<seat>_<time>.log

The strategy object (`owner`) must provide:
    on_tick / on_event / on_fill / on_ipo         the arena.Trader hooks
    on_sent(signal) · on_answer(symbol|None)      order lifecycle (ACK = symbol, reject = None)
    on_reconnect() · halt(symbol, seconds, why) · is_halted(symbol)
    liquidated · start_nw · _board_nw · team_board · my_board · _refs · _inflight · _portfolio

Run a bot with run_live(MyTrader()).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import random
import re
import time

import websockets

from arena import Signal                    # must come first: it puts engine/ on sys.path
from arena.trader import _AdaptedTraderBot
import trader.config as _config
from trader.trader import MarketData, _print_startup
from pydantic import TypeAdapter, ValidationError
from shared.messages import (AnyExchangeMessage, BookSnapshot, ErrorMsg, Leaderboard, OrderAck,
                             PlaceOrder, PortfolioUpdate, SessionEvent, TradeExecution)
from team.ledger import Ledger

BACKOFF_MIN_S, BACKOFF_MAX_S = 1.0, 30.0   # reconnect waits 1 s, 2 s, 4 s … up to 30 s, times a random jitter
STABLE_S = 30.0                             # a connection that lived 30 s was healthy: restart the backoff at 1 s
STATUS_EVERY_S = 30.0                       # log the books every 30 s
ACCRUAL_MAX_ABS, ACCRUAL_MAX_FRAC = 5.0, 1e-4   # largest cash gap per leaderboard row taken as interest/carry
DROP_AFTER_S = float(os.environ.get("DROP_AFTER_S", "0"))   # > 0: close the socket once after N s (reconnect evidence)
AUCTION_EVENTS = ("SESSION_PREOPEN", "SESSION_PRECLOSE")    # auctions accept limit orders only; we stay out
DEFAULT_HALT_S = 300.0                      # the venue's halt length, when a reject doesn't say
TEAM_PREFIX = os.environ.get("TEAM_PREFIX", "6_rings_")
STATE_DIR = pathlib.Path(__file__).resolve().parent / "state"
LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"

# The wire: every exchange → bot frame is one model of this discriminated union on `type`
WIRE: TypeAdapter[AnyExchangeMessage] = TypeAdapter(AnyExchangeMessage)


def run_live(owner) -> None:
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
    asyncio.run(_Bot(owner).run())


class _ColourFormatter(logging.Formatter):
    """Terminal-only colours: the level, plus the tag that starts each of our lines."""

    LEVELS = {"DEBUG": "2", "INFO": "32", "WARNING": "33", "ERROR": "31", "CRITICAL": "1;31"}
    TAGS = {
        "TICK": "36", "BOOKS": "1;34", "FILL": "1;35", "Order": "35", "ACK": "2",
        "CONNECT": "1;32", "RESYNC": "32", "RECONCILE": "1;32", "SESSION": "1;36",
        "DISCONNECT": "1;31", "RECONNECT": "33", "INDUCED": "1;33", "REJECT": "1;31",
        "IPO": "1;36", "Session": "33", "HALT": "1;31", "RESUME": "1;32", "RISK": "1;31",
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

    def __init__(self, owner) -> None:
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
                self._owner.on_answer(msg.symbol)
            elif isinstance(msg, ErrorMsg):
                self.rejects += 1
                logging.warning("REJECT #%d [%s] %s", self.rejects, msg.code, msg.message)
                self._owner.on_answer(None)
                self._on_reject(msg)
            elif isinstance(msg, Leaderboard):
                self._on_leaderboard(msg)

    def _on_reject(self, msg: ErrorMsg) -> None:
        """⑦ Learn from the two rejects that mean "stop sending": a halt we did not
        hear about (we reconnected mid-halt — the venue does not replay it) and
        a liquidation."""
        if msg.code == "SYMBOL_HALTED":
            sym = msg.message.split(" ", 1)[0]
            m = re.search(r"Resume in (\d+)s", msg.message)
            self._owner.halt(sym, float(m.group(1)) if m else DEFAULT_HALT_S, "learnt from a reject")
        elif msg.code == "LIQUIDATED":
            self._owner.liquidated = True

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
        # ⑦ The team's position per symbol, summed over every one of our seats
        owner = self._owner
        team: dict[str, int] = {}
        for e in (*msg.traders, *msg.brokers):
            if str(e.get("team_id", "")).startswith(TEAM_PREFIX):
                for s, q in (e.get("positions") or {}).items():
                    team[s] = team.get(s, 0) + int(q)
        owner.team_board = team
        for board in (msg.traders, msg.brokers):     # each list is sorted by net worth
            rank = next((i for i, e in enumerate(board, 1)
                         if e.get("team_id") == _config.TEAM_ID), None)
            if rank is not None:
                row = board[rank - 1]
                break
        else:
            return
        self._last_row = row
        owner.my_board = {s: int(q) for s, q in (row.get("positions") or {}).items()}
        owner._board_nw = float(row.get("net_worth") or 0.0)
        if not owner.start_nw and owner._board_nw > 0:
            owner.start_nw = owner._board_nw      # the equity floor's baseline
        if row.get("liquidated"):
            owner.liquidated = True
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
        if self._owner.liquidated or self._owner.is_halted(signal.symbol):
            return              # ⑦ belt and braces: on_tick already dropped these
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

