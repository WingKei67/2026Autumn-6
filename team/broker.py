"""Week 5 broker: bounded IOC quotes and cash-funded, long-only inventory.

IOC quotes sacrifice resting maker rebates for an unambiguous completion
barrier on the hosted protocol. Resume probes can cross and execute.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import time

from team.risk import log_state, acquire_seat_lock
from arena import Broker
from arena.broker import _AdaptedBrokerBot
import broker.config as broker_config
from shared.messages import CancelOrder, PlaceOrder, SessionEvent

# All orders for each broker symbol (including resting orders) stay within
# ±20 shares. Two 150-share trader seats + 20 broker = 320 < team cap 800.
BROKER_SYMBOL_CAP = 20
BROKER_QUOTE_CAP = 10     # compatibility export; live IOC budget uses BROKER_SYMBOL_CAP
BROKER_GROSS_CAP = 75_000.0
BROKER_STARTING_EQUITY = 350_000.0
BROKER_EQUITY_HALT = 0.70 * BROKER_STARTING_EQUITY

# Do not accumulate a 60-share, 3-level ladder on each side.
broker_config.QUOTE_SIZE = 2
broker_config.QUOTE_LEVELS = 1


class _RiskBrokerBot(_AdaptedBrokerBot):
    """Guard every outbound quote and explicitly respect trading halts."""

    def __init__(self, owner, exchange_url=None):
        super().__init__(owner, exchange_url)
        self._halted_symbols: set[str] = set()
        self._market_halted = False
        self._last_equity = BROKER_STARTING_EQUITY
        self._disabled = False
        self._has_snapshot = False
        self._graded_week5 = False
        self._continuous = False
        self._resume_pending = set()
        self._probe_submission = None
        self._resume_probes: set[str] = set()
        self._confirmed_absent = set()
        self._orders = {}  # (symbol, side) -> remaining, price, id, IOC, post-resume
        self._seen_trades = set()
        self._snapshot_at = 0.0
        self._taker = .005
        self._board_tick = -1

    async def connect_to_exchange(self) -> None:
        await super().connect_to_exchange()
        # A connection is not proof that a known halt has ended.
        # MARKET_STATE or the resume event reconciles these gates.
        self._has_snapshot = False  # block orders until fresh server portfolio
        self._continuous = False
        self._resume_probes.clear()
        # Actual venue sweeps this seat on disconnect/reconnect before handshake state.
        self._board_tick = -1
        self._orders.clear()
        self.state.resting_orders.clear()
        self.state.last_quote_prices.clear()

    def _on_portfolio(self, msg):
        if msg.team_id != broker_config.TEAM_ID:
            return
        self._snapshot_at = time.monotonic()
        super()._on_portfolio(msg)
        self._last_equity = msg.net_worth
        self._disabled = bool(msg.liquidated)
        self._has_snapshot = True

    def _on_leaderboard(self, msg):
        if msg.tick < self._board_tick:
            return
        self._board_tick = msg.tick
        super()._on_leaderboard(msg)
        # An existing seat joining mid-session gets a leaderboard first,
        # but may not get a PortfolioUpdate until its next fill/grant.
        # Without this resync, the broker's safe default blocks forever.
        for entry in msg.brokers:
            if entry.get("team_id") == broker_config.TEAM_ID:
                self._snapshot_at = time.monotonic()
                self.state.cash = float(entry.get("cash", self.state.cash))
                self.state.positions = dict(entry.get("positions") or {})
                self._last_equity = float(entry.get("net_worth", 0))
                self._disabled = bool(entry.get("liquidated", False))
                self._has_snapshot = True
                log_state(broker_config.TEAM_ID, self.state.positions,
                          self.state.exchange_prices, self.state.cash, self._last_equity,
                          msg.traders + msg.brokers)
                break

    async def _on_session_event(self, msg: SessionEvent):
        d = msg.data or {}
        if msg.event == "FEE_SCHEDULE":
            self._taker = max(.005, float(d.get("taker", .005)))
        if msg.event == "MARKET_STATE":
            previously = set(self._halted_symbols)
            market_was_halted = self._market_halted
            self._halted_symbols = set(d.get("halted_symbols", []))
            self._market_halted = bool(d.get("market_halted", False))
            self._resume_pending.update(previously - self._halted_symbols)
            if market_was_halted and not self._market_halted:
                self._resume_pending.update(self.state.exchange_prices)
        if msg.event == "WEEK_CHANGED":
            self._graded_week5 = int(d.get("week", 0)) == 5
            self._continuous = False
            logging.info("RISK broker scenario switched: week=%s", d.get("week"))
        elif msg.event == "SESSION_OPEN":
            scen = d.get("scenario") or {}
            if d.get("week") is not None or scen.get("week") is not None:
                self._graded_week5 = int(d.get("week", scen.get("week", 0))) == 5
            self._continuous = True
        elif msg.event in ("SESSION_PREOPEN", "SESSION_CLOSED"):
            self._continuous = False
        if msg.event == "TRADING_HALT" and d.get("symbol"):
            sym = d["symbol"]
            self._halted_symbols.add(sym)
            await self.cancel_quotes(sym)
            logging.warning("RISK broker HALT %s: cancellation requested", sym)
        elif msg.event == "TRADING_RESUMED" and d.get("symbol"):
            sym = d["symbol"]
            self._halted_symbols.discard(sym)
            self._resume_pending.add(sym)
            logging.info("RISK broker RESUMED %s: quoting enabled", sym)
        elif msg.event == "MARKET_HALT":
            self._market_halted = True
            await self.cancel_all_quotes()
            logging.warning("RISK broker market-wide HALT: quotes cancelled")
        elif msg.event == "MARKET_RESUMED":
            self._market_halted = False
            self._resume_pending.update(self.state.exchange_prices)
            logging.info("RISK broker market-wide RESUMED: quoting enabled")
        elif msg.event == "LIQUIDATION" and d.get("team_id") == broker_config.TEAM_ID:
            self._disabled = True
        await super()._on_session_event(msg)
        if msg.event == "SESSION_CLOSED":
            # The SDK normally tries to MARKET-liquidate after closing.
            # That is invalid; cancellation still works and protects the book.
            await self.cancel_all_quotes()
        if (msg.event == "TRADING_RESUMED" and d.get("symbol")
                and self._graded_week5 and self._continuous):
            await self._resume_probe(d["symbol"])

    async def _resume_probe(self, symbol: str):
        """Conservative 1-share IOC activity proof after symbol resumes."""
        if (not self._has_snapshot or self._disabled or self._market_halted
                or symbol in self._halted_symbols):
            logging.warning("RISK broker resume probe skipped %s: not ready", symbol)
            return
        ref = self.state.exchange_prices.get(symbol)
        if not ref or ref <= 0 or self.state.cash < ref * 2:
            logging.warning("RISK broker resume probe skipped %s: no reference/cash", symbol)
            return
        side = "sell" if self.state.positions.get(symbol, 0) > 0 else "buy"
        self._resume_probes.add(symbol)
        self._probe_submission = (symbol, side)
        try:
            sent = await self._send(PlaceOrder(
                team_id=broker_config.TEAM_ID, symbol=symbol, side=side,
                order_type="ioc", price=round(ref * (1.005 if side == "buy" else .995), 2), quantity=1))
        finally:
            self._probe_submission = None
        if not sent:
            self._resume_probes.discard(symbol)

    def start_yahoo_feeds(self):
        # Use the actual venue reference; no external data or binary analytics dependency.
        return

    def _on_order_ack(self, msg):
        if msg.team_id != broker_config.TEAM_ID:
            return
        entry = self._orders.get((msg.symbol, msg.side))
        if entry is None or entry[2] is not None:
            return
        entry[2] = msg.order_id
        logging.info("ACK %s %s %d %s", msg.order_id, msg.side, msg.quantity, msg.symbol)
        if msg.symbol in self._resume_probes and entry[4]:
            self._resume_probes.discard(msg.symbol)
            self._resume_pending.discard(msg.symbol)
            logging.info("RISK broker RESUME CONFIRMED %s via IOC order ACK", msg.symbol)

    def _on_trade(self, msg):
        if msg.trade_id in self._seen_trades:
            return
        me = broker_config.TEAM_ID
        if me not in (msg.buyer_id, msg.seller_id):
            return
        self._seen_trades.add(msg.trade_id)
        side = "buy" if msg.buyer_id == me else "sell"
        entry = self._orders.get((msg.symbol, side))
        if entry:
            entry[0] = max(0, entry[0] - msg.quantity)
        fee = msg.fee if msg.taker_id == me else -msg.maker_rebate
        signed = msg.quantity if side == "buy" else -msg.quantity
        self.state.cash -= signed * msg.price + fee
        self.state.positions[msg.symbol] = self.state.positions.get(msg.symbol, 0) + signed
        self.state.fills += 1
        self._owner.on_fill(side, msg.symbol, msg.quantity, msg.price)
        logging.info("FILL %s %d %s @ %.2f", side, msg.quantity, msg.symbol, msg.price)

    def _on_error(self, msg):
        if msg.code == "ORDER_NOT_FOUND":
            if any(repr(oid) in msg.message for oid in self._confirmed_absent):
                return
            for key, entry in list(self._orders.items()):
                if entry[2] is not None and repr(entry[2]) in msg.message:
                    self._confirmed_absent.add(entry[2])
                    del self._orders[key]
                    logging.info("CANCEL CONFIRMED %s %s", *key)
                    return
        logging.warning("Exchange error [%s]: %s", msg.code, msg.message)
        # Uncorrelated errors cannot release reservations. Reconnect performs
        # the venue's authoritative sweep and obtains a new account snapshot.
        self._has_snapshot = False
        if self._ws is not None:
            asyncio.create_task(self._ws.close())

    async def requote_loop(self):
        # One symbol per half-second: at most two IOC orders and their two
        # completion checks per visit, comfortably below 20 messages/second.
        index = 0
        while True:
            await asyncio.sleep(.5)
            symbols = list(self._quote_symbols)
            if not symbols:
                continue
            symbol = symbols[index % len(symbols)]
            index += 1
            await self.quote_symbol(symbol)

    async def quote_symbol(self, symbol):
        if not self._ready(symbol):
            if any(k[0] == symbol for k in self._orders):
                await self.cancel_quotes(symbol)
            return
        if any(k[0] == symbol for k in self._orders):
            await self.cancel_quotes(symbol)
            return  # confirmed absence, never send-and-assume cancellation
        if symbol in self._resume_pending:
            await self._resume_probe(symbol)
            return
        ref = self.state.exchange_prices.get(symbol)
        if not ref or ref <= 0:
            return
        spread = self.state.compute_spread(symbol)
        for side, price in (("buy", round(ref - spread / 2, 2)),
                            ("sell", round(ref + spread / 2, 2))):
            await self._send(PlaceOrder(team_id=broker_config.TEAM_ID,
                symbol=symbol, side=side, order_type="limit", price=price, quantity=2))
        self.state.last_quote_prices[symbol] = ref

    async def cancel_quotes(self, symbol):
        for (sym, side), entry in list(self._orders.items()):
            if sym == symbol and entry[2] is not None:
                msg = CancelOrder(team_id=broker_config.TEAM_ID,
                                  symbol=sym, order_id=entry[2])
                # First removes a live limit; second confirms absence. IOC
                # already disappeared, so either reply confirms completion.
                await super()._send(msg)
                if not entry[3]:
                    await super()._send(msg)

    def _ready(self, symbol):
        return (self._graded_week5 and self._continuous and self._has_snapshot
            and not self._disabled and not self._market_halted
            and symbol not in self._halted_symbols
            and self._snapshot_at > 0 and time.monotonic() - self._snapshot_at <= 10)

    async def _send(self, msg):
        if not isinstance(msg, PlaceOrder):
            return await super()._send(msg)
        if msg.order_type not in ("limit", "ioc"):
            return
        # Hosted protocol cannot prove that another client has finished booking
        # a fill against a cancelled maker quote. IOC execution on our own
        # socket completes before its absence reply; use that stronger barrier.
        # Preserve quote prices, but never leave an unattended resting stack.
        msg = msg.model_copy(update={"order_type": "ioc"})
        sym, side = msg.symbol, msg.side
        if (not self._ready(sym) or msg.order_type not in ("limit", "ioc")
                or (sym, side) in self._orders):
            return
        marks = self.state.exchange_prices
        ref = marks.get(sym, 0)
        if not ref or not 0.93 * ref <= msg.price <= 1.07 * ref:
            return
        if any(q and marks.get(s, 0) <= 0 for s, q in self.state.positions.items()):
            return
        pos = self.state.positions.get(sym, 0)
        buy = sum(e[0] for (s, d), e in self._orders.items() if s == sym and d == "buy")
        sell = sum(e[0] for (s, d), e in self._orders.items() if s == sym and d == "sell")
        # Cash-funded, long-only: short squeezes cannot create unbounded loss.
        qty = min(msg.quantity, max(0, BROKER_SYMBOL_CAP - pos - buy)
                  if side == "buy" else max(0, pos - sell))
        pending = sum(e[0] * max(e[1], marks.get(s, e[1]))
                      for (s, d), e in self._orders.items() if d == "buy")
        gross = sum(abs(q) * marks[s] for s, q in self.state.positions.items() if q)
        if side == "buy":
            if self._last_equity < BROKER_EQUITY_HALT:
                return
            qty = min(qty, max(0, int(((self.state.cash - .60 * BROKER_STARTING_EQUITY) / (1 + self._taker) - pending) // msg.price)),
                max(0, int((BROKER_GROSS_CAP - gross - pending) // max(ref, msg.price))))
        if qty <= 0:
            return
        self._orders[sym, side] = [qty, msg.price, None, msg.order_type == "ioc",
                                  self._probe_submission == (sym, side)]
        logging.info("Order sent: %s %d %s @ %.2f (%s)", side, qty, sym, msg.price, msg.order_type)
        await super()._send(msg.model_copy(update={"quantity": qty}))
        return True

    async def flatten_all(self):
        # Stock SDK sends market orders AFTER SESSION_CLOSED: all reject.
        # Leave inventory marked by the exchange; no after-close orders.
        return


class MyBroker(Broker):
    def run(self) -> None:
        # Broker evidence must survive terminal scrolling during the graded
        # session, just like the trader's separate per-seat log file.
        self._seat_lock = acquire_seat_lock(broker_config.TEAM_ID)
        log_dir = pathlib.Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        logfile = log_dir / f"{broker_config.TEAM_ID}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
        terminal = logging.StreamHandler()
        terminal.setFormatter(fmt)
        filelog = logging.FileHandler(logfile)
        filelog.setFormatter(fmt)
        logging.basicConfig(level=logging.INFO, force=True, handlers=[terminal, filelog])
        logging.info("RISK broker session log → %s", logfile)
        asyncio.run(self._run_venues())

    def spread(self, symbol, price, history):
        if not price:
            return None
        # Widen ahead of and during 5–12% shocks; never quote through noise.
        if len(history) < 5:
            return max(0.3, 0.006 * price)
        returns = [abs((b - a) / a) for a, b in zip(history[-20:-1], history[-19:]) if a > 0]
        vol = (sum(returns) / len(returns)) if returns else 0.0
        return max(0.3, price * min(0.03, max(0.006, 8.0 * vol)))

    def skew(self, symbol, inventory):
        # Long → move quotes down; short → move quotes up, to mean-revert.
        return -0.002 * inventory

    async def _run_venues(self):
        urls = [u.strip() for u in broker_config.EXCHANGE_URLS if u.strip()]
        if len(urls) <= 1:
            await _RiskBrokerBot(self, urls[0] if urls else None).run()
            return
        # Week 5 cap is team-wide across venues: conservatively quote only
        # one gateway, rather than multiplying resting exposure by venue.
        logging.warning("Week 5 safety: quoting only first of %d venues", len(urls))
        await _RiskBrokerBot(self, urls[0]).run()


if __name__ == "__main__":
    MyBroker().run()
