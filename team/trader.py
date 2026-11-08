"""
team/trader.py — 6 Rings trader: two strategies behind one set of hard limits.

    RiskTrader       the shared part: hard limits, halts, order lifecycle, equity floor
    MomentumTrader   the Phase 2 signal (price momentum)
    EventTrader      the event bot that ran Sessions 3–6 (RIDE / FADE on CALENDAR_EVENT)
    MyTrader         whichever STRATEGY selects: "momentum" (default) or "event"

MomentumTrader
    r = mid now / mid LOOKBACK snapshots ago − 1          (only data up to now)
    r > ENTRY → buy; sell after HOLD_S, when r turns negative, or STOP under entry.

EventTrader — every CALENDAR_EVENT carries the move (pct), its ramp and overshoot
    pct > 0  RIDE  buy at once (≤ half the move), sell at 90 % of the overshoot peak
    pct < 0  FADE  sell what we hold before the drop, buy it back (and more) at the
                   overshoot trough, sell the extra on the rebound
    A leg is traded only if its designed edge beats the round-trip taker fee.

Hard limits — every order goes through RiskTrader._check()
    never short · ≤ SEAT_CAP shares per symbol · the team's position limit · nothing
    while halted · price inside the LULD band · cash reserve (orders in flight counted)
    · one order in flight per symbol · below EQUITY_FLOOR × start: sell only
    · liquidated: nothing · an order within RESUME_S of every TRADING_RESUMED

Run:      TEAM_ID=6_rings_trader_2 [STRATEGY=event] python -m team.trader
Backtest: python scripts/phase2_sim.py        (MomentumTrader in SimSession — Phase 2)
          python scripts/phase2_backtest.py   (both strategies on the recorded sessions)
"""

from __future__ import annotations

import collections
import logging
import os
import time
from dataclasses import dataclass

from arena import Signal, Trader            # arena must come first: it puts engine/ on sys.path
import trader.config as _config

# Sizing and order prices
SEAT_CAP = int(os.environ.get("SEAT_POSITION_CAP", "150"))   # shares per symbol, this seat
ORDER_MAX = 60            # shares per order
CASH_RESERVE = 0.10       # keep 10 % of net worth in cash
SLIPPAGE = 0.003          # IOC limit = touch ± 0.3 %
FEE_BUFFER = 0.004        # cost estimate per share: price × (1 + fee + margin)

# Hard limits
TEAM_LIMIT = int(os.environ.get("TEAM_POSITION_LIMIT", "1000"))   # scenario position_limit
TEAM_BUFFER = 50          # leaderboard lag + the broker's resting quotes
LULD_BAND = 0.075         # the venue rejects prices > 8 % from the mark
EQUITY_FLOOR = 0.70       # the venue liquidates at 50 %
HALT_GRACE_S = 30.0       # no RESUMED this long after a halt should have ended → assume resumed
RESUME_S = 3.0            # an order within 3 s of every resume
INFLIGHT_TTL_S = 0.3      # an ACK'd IOC is over 0.3 s later
PENDING_TTL_S = 10.0      # no answer at all → stop waiting

log = logging.getLogger("team.trader")


# ═════════════════════════════════════════════════════════════════════════════
# The shared part
# ═════════════════════════════════════════════════════════════════════════════

class RiskTrader(Trader):
    """Hard limits, halts and the order lifecycle. A strategy implements decide()."""

    def __init__(self) -> None:
        self.now = time.time                  # the backtesters replace this with simulated time
        self.owe: dict[str, float] = {}       # symbol → some order is due by this time
        self.halted: dict[str, float] = {}    # symbol → stop waiting for its RESUMED at
        self.market_halt_until = 0.0
        self.liquidated = False
        self.mids: dict[str, float] = {}      # last two-sided mid per symbol
        # filled in by the live plumbing (team/live.py)
        self.start_nw = float(os.environ.get("STARTING_CASH", "0"))
        self._board_nw = 0.0
        self.team_board: dict[str, int] = {}
        self.my_board: dict[str, int] = {}
        self._refs: dict[str, float] = {}
        self._portfolio = None
        self._inflight: dict[str, list] = {}  # symbol → [side, qty, price, t, acked]
        self._pending: collections.deque[str] = collections.deque()

    def decide(self, market, portfolio, now: float) -> Signal | None:
        """The strategy: at most one order (already passed through _check)."""
        return None

    # ── every tick ──────────────────────────────────────────────────────

    def on_tick(self, market, portfolio):
        now = self.now()
        self._inflight = {s: f for s, f in self._inflight.items()
                          if now - f[3] < (INFLIGHT_TTL_S if f[4] else PENDING_TTL_S)}
        if self.liquidated:
            return None
        for s in market.symbols():           # a one-sided book's mid is a model value, not a price
            if market.best_bid(s) and market.best_ask(s) and (mid := market.mid_price(s)):
                self.mids[s] = mid
        if self.below_floor():                                       # sell everything
            for s, pos in portfolio.positions.items():
                bid = market.best_bid(s)
                if pos > 0 and bid and (sig := self._order(s, "sell", pos, bid, portfolio)):
                    return sig
            return None
        return self.decide(market, portfolio, now) or self._reaction(market, portfolio, now)

    def _reaction(self, market, portfolio, now):
        """A 1-share IOC far from the market for every resume (and event) nothing else answered."""
        for s, due in list(self.owe.items()):
            bid = market.best_bid(s)
            if now < due or not bid or self.is_halted(s):
                continue
            sig = self._check(Signal(symbol=s, side="buy", quantity=1, price=round(bid * 0.98, 2)),
                              portfolio)
            if sig:
                return sig
        return None

    def _order(self, s: str, side: str, qty: int, touch: float, portfolio) -> Signal | None:
        px = touch * (1 + SLIPPAGE) if side == "buy" else touch * (1 - SLIPPAGE)
        return self._check(Signal(symbol=s, side=side, quantity=qty, price=round(px, 2)), portfolio)

    # ── venue events: halts and liquidation ─────────────────────────────

    def on_event(self, event: str, message: str, data: dict) -> None:
        sym, now = data.get("symbol"), self.now()
        if event == "TRADING_HALT" and sym:
            self.halt(sym, float(data.get("duration") or 300.0), data.get("reason", ""))
        elif event == "TRADING_RESUMED" and sym:
            self.halted.pop(sym, None)
            self.owe[sym] = now + RESUME_S
            log.info("RESUME %s — an order goes out within %.0f s", sym, RESUME_S)
        elif event == "MARKET_HALT":
            self.market_halt_until = now + float(data.get("duration") or 300.0) + HALT_GRACE_S
        elif event == "MARKET_RESUMED":
            self.market_halt_until = 0.0
            for s in self.mids:
                self.owe[s] = now + RESUME_S
        elif event == "LIQUIDATION" and data.get("team_id") == _config.TEAM_ID:
            self.liquidated = True

    def halt(self, symbol: str, duration: float, reason: str = "") -> None:
        self.halted[symbol] = self.now() + duration + HALT_GRACE_S
        log.warning("HALT %s for %.0f s %s — nothing is sent", symbol, duration, reason)

    def is_halted(self, symbol: str) -> bool:
        now = self.now()
        if now < self.market_halt_until:
            return True
        until = self.halted.get(symbol)
        if until is not None and now >= until:              # missed the RESUMED
            del self.halted[symbol]
            self.owe[symbol] = now
            return False
        return until is not None

    # ── hard limits: the last word on every order ───────────────────────

    def _check(self, sig: Signal, portfolio) -> Signal | None:
        s = sig.symbol
        if self.liquidated or self.is_halted(s) or s in self._inflight:
            return None
        pos = portfolio.positions.get(s, 0)
        qty = min(int(sig.quantity), ORDER_MAX)
        if sig.side == "sell":
            qty = min(qty, pos)                                          # never short
        else:
            others = self.team_board.get(s, 0) - self.my_board.get(s, 0)
            qty = min(qty, SEAT_CAP - pos, TEAM_LIMIT - TEAM_BUFFER - others - pos,
                      self._affordable(sig.price, portfolio))
        if qty <= 0:
            return None
        price, mark = sig.price, self._refs.get(s)        # the venue's own mark (ref_price)
        if mark:
            price = min(max(price, mark * (1 - LULD_BAND)), mark * (1 + LULD_BAND))
        return Signal(symbol=s, side=sig.side, quantity=qty, price=round(price, 2))

    def _affordable(self, price: float, portfolio) -> int:
        spent = sum(f[1] * f[2] for f in self._inflight.values() if f[0] == "buy")
        cash = portfolio.cash - spent - CASH_RESERVE * (self._board_nw or portfolio.cash)
        return max(0, int(cash / (price * (1 + FEE_BUFFER))))

    def _net_worth(self, portfolio) -> float:
        return portfolio.cash + sum(q * self.mids.get(s, 0.0) for s, q in portfolio.positions.items())

    def below_floor(self) -> bool:
        return bool(self.start_nw and self._board_nw and self._board_nw < EQUITY_FLOOR * self.start_nw)

    # ── order lifecycle (called by team/live.py and the backtesters) ────

    def on_sent(self, sig: Signal) -> None:
        self._inflight[sig.symbol] = [sig.side, sig.quantity, sig.price, self.now(), False]
        self._pending.append(sig.symbol)
        self.owe.pop(sig.symbol, None)                   # any order answers a reaction

    def on_answer(self, symbol: str | None) -> None:
        """ACK (names the symbol) or reject (names nothing: the oldest unanswered order)."""
        if symbol is None:
            if self._pending and (f := self._inflight.get(self._pending.popleft())) and not f[4]:
                f[3] = -1e18                             # rejected → over now
            return
        if symbol in self._pending:
            self._pending.remove(symbol)
        if (f := self._inflight.get(symbol)) and not f[4]:
            f[3], f[4] = self.now(), True

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        if (f := self._inflight.get(symbol)) and f[0] == side:
            f[1] -= quantity
            if f[1] <= 0:
                del self._inflight[symbol]

    def on_reconnect(self) -> None:
        self._inflight.clear()
        self._pending.clear()

    def run(self) -> None:
        from team.live import run_live
        run_live(self)


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 1 — momentum (the Phase 2 signal)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """One momentum trade in one symbol."""

    t: float              # when we decided to buy
    entry: float          # the ask at that moment
    want: int = 0         # shares to buy (set at the first order)
    qty: int = 0          # shares held from this trade
    exiting: bool = False


class MomentumTrader(RiskTrader):
    """Buy short-term strength, sell when it fades.
    LOOKBACK and ENTRY were chosen on SimSession seeds 0–9 only (BACKTEST.md)."""

    LOOKBACK = 60          # snapshots      — the training-grid choice (scripts/phase2_sim.py)
    ENTRY = 0.01           # +1 % over the lookback
    HOLD_S = 60.0
    STOP = 0.01            # 1 % under the entry
    COOLDOWN_S = 60.0      # after an exit, wait before buying that symbol again
    RISK_FRACTION = 0.2    # one position uses at most 20 % of net worth
    ENTRY_WINDOW_S = 5.0   # keep trying to fill an entry for this long
    SIGNAL_DELAY = 0       # backtest only: read the feature this many snapshots late

    def __init__(self) -> None:
        super().__init__()
        self.open: dict[str, Position] = {}
        self.cooldown: dict[str, float] = {}

    def momentum(self, market, s: str) -> float | None:
        """r = mid now / mid LOOKBACK snapshots ago − 1, read SIGNAL_DELAY snapshots late."""
        h, d, n = list(market.prices(s)), self.SIGNAL_DELAY, self.LOOKBACK
        if len(h) < n + 1 + d or not h[-1 - d - n]:
            return None
        return h[-1 - d] / h[-1 - d - n] - 1.0

    def decide(self, market, portfolio, now):
        self.open = {s: p for s, p in self.open.items()               # entries that never filled
                     if p.qty > 0 or now <= p.t + self.ENTRY_WINDOW_S}
        for s in market.symbols():                                   # new signals
            ask, r = market.best_ask(s), self.momentum(market, s)
            if ask and r is not None and r > self.ENTRY and s not in self.open \
                    and now >= self.cooldown.get(s, 0.0):
                self.open[s] = Position(now, ask)
        for s, p in list(self.open.items()):                         # exits first
            if p.qty > 0 and (p.exiting or self._exit_due(p, market, s, now)):
                p.exiting = True
                bid = market.best_bid(s)
                if bid and (sig := self._order(s, "sell", p.qty, bid, portfolio)):
                    return sig
        for s, p in self.open.items():                               # then entries
            ask = market.best_ask(s)
            if p.exiting or not ask or ask > p.entry * (1 + SLIPPAGE) or now > p.t + self.ENTRY_WINDOW_S:
                continue
            p.want = p.want or int(self.RISK_FRACTION * self._net_worth(portfolio) / ask)
            if p.qty < p.want and (sig := self._check(Signal(
                    symbol=s, side="buy", quantity=p.want - p.qty,
                    price=round(min(ask * (1 + SLIPPAGE), p.entry * (1 + SLIPPAGE)), 2)), portfolio)):
                return sig
        return None

    def _exit_due(self, p: Position, market, s: str, now: float) -> bool:
        r = self.momentum(market, s)
        return (now >= p.t + self.HOLD_S or self.mids.get(s, p.entry) <= p.entry * (1 - self.STOP)
                or (r is not None and r < 0))

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        if p := self.open.get(symbol):
            p.qty += quantity if side == "buy" else -min(quantity, p.qty)
            if p.exiting and p.qty <= 0:
                del self.open[symbol]
                self.cooldown[symbol] = self.now() + self.COOLDOWN_S
        super().on_fill(side, symbol, quantity, price)


# ═════════════════════════════════════════════════════════════════════════════
# Strategy 2 — the event bot (RIDE / FADE)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Plan:
    """What the bot does in one symbol after one event."""

    mode: str             # "ride" or "fade"
    pre: float            # mid just before the event
    pct: float            # announced move
    overshoot: float
    t0: float
    ramp_s: float
    state: str            # ride: enter → hold → done;  fade: derisk → wait → enter → hold → restore → done
    target: int = 0       # extra shares this plan buys
    held: int = 0         # of those, currently held
    entry_px: float = 0.0
    core0: int = -1       # shares we held when the news broke
    core_sold: int = 0    # … sold before the drop
    core_back: int = 0    # … bought back
    extra_ok: bool = True


class EventTrader(RiskTrader):
    """The Sessions 3–6 event bot (week-2 ShockSwitchTrader logic), every order behind _check."""

    TAKER = 0.003          # updated by FEE_SCHEDULE
    RISK_FRACTION = 0.25   # one plan uses at most 25 % of net worth …
    MAX_SHARES = 200       # … and at most 200 shares
    ENTRY_SLIPPAGE = 0.5   # RIDE: pay at most pre × (1 + 0.5 × pct)
    RIDE_TAKE = 0.9        # RIDE: exit at 90 % of the overshoot peak
    RIDE_TIME_STOP = 3.0   # × ramp
    FADE_STOP = 0.015      # FADE: stop 1.5 % under the entry
    FADE_TIME_STOP = 6.0   # × ramp
    KILL_SWITCH = 0.03     # no new plans after a 3 % drawdown from the high
    REACT_S = 20.0         # course rule: some order within 20 s of every event
    MAX_SHOCK = 0.20       # a derived SHOCK move above 20 % means our price is stale

    def __init__(self) -> None:
        super().__init__()
        self.plans: dict[str, Plan] = {}
        self.ramp_ticks, self.overshoot = 15, 1.3
        self.peak_nw = 0.0

    # ── news → plans ────────────────────────────────────────────────────

    def on_event(self, event: str, message: str, data: dict) -> None:
        super().on_event(event, message, data)
        if event == "FEE_SCHEDULE" and data.get("taker") is not None:
            self.TAKER = float(data["taker"])
        elif event == "CALENDAR_EVENT":
            self.ramp_ticks = int(data.get("ramp_ticks") or self.ramp_ticks)
            self.overshoot = float(data.get("overshoot") or self.overshoot)
            self.news(float(data.get("pct") or 0.0), data.get("affected") or [])
        elif event == "SHOCK":                                       # carries targets, not pct
            for s, target in (data.get("prices") or {}).items():
                ref = self._refs.get(s) or self.mids.get(s)
                if ref and abs(float(target) / ref - 1) <= self.MAX_SHOCK:
                    self.news(float(target) / ref - 1.0, [s])

    def news(self, pct: float, symbols: list[str]) -> None:
        now, ov, cost = self.now(), self.overshoot, 2 * self.TAKER    # taker fee in AND out
        ride_edge = (self.RIDE_TAKE * ov - self.ENTRY_SLIPPAGE) * pct   # buy ≤ half-way, sell at 90 % of peak
        drop_edge = (ov - 0.5) * abs(pct)                               # sell ≥ half-way down
        rebound_edge = (ov - 1.0) * abs(pct)                            # trough → settle
        for s in symbols:
            self.owe.setdefault(s, now + self.REACT_S)
            pre = self.mids.get(s)
            if not pre or s in self.plans or (ride_edge if pct > 0 else drop_edge) <= cost:
                continue
            mode = "ride" if pct > 0 else "fade"
            self.plans[s] = Plan(mode, pre, pct, ov, now, self.ramp_ticks * 1.0,
                                 "enter" if mode == "ride" else "derisk",
                                 extra_ok=mode == "ride" or rebound_edge > cost)
            log.info("EVENT %s %+.1f%% → %s", s, pct * 100, mode.upper())

    # ── plans → one order ───────────────────────────────────────────────

    def decide(self, market, portfolio, now):
        nw = self._net_worth(portfolio)
        self.peak_nw = max(self.peak_nw, nw)
        killed = nw < self.peak_nw * (1 - self.KILL_SWITCH)
        orders = []
        for s, p in list(self.plans.items()):
            if self.is_halted(s):
                continue                                             # a halted plan is frozen
            sig, urgency = self._step(p, s, market, portfolio, nw, killed, now)
            if p.state == "done":
                del self.plans[s]
            if sig:
                orders.append((urgency, sig))
        # the most urgent plan only: if the limits stop it, nothing goes out this tick
        return self._check(max(orders, key=lambda o: o[0])[1], portfolio) if orders else None

    def _step(self, p: Plan, s: str, market, portfolio, nw: float, killed: bool, now: float):
        bid, ask = market.best_bid(s), market.best_ask(s)
        mid = self.mids.get(s)
        if not mid:
            return None, 0.0
        age, pos = now - p.t0, int(portfolio.positions.get(s, 0))
        sell_in_flight = (f := self._inflight.get(s)) and f[0] == "sell" and f[1]

        if p.mode == "ride":
            if p.state == "enter":
                p.target = p.target or self._size(s, ask or mid, nw, pos)
                limit = p.pre * (1 + self.ENTRY_SLIPPAGE * p.pct)
                if killed or p.held >= p.target or age > p.ramp_s * 0.5 or (ask and ask > limit):
                    p.state = "hold" if p.held else "done"
                    return None, 0.0
                qty = min(p.target - p.held, _depth(market, s, "asks", limit))
                return (self._sig(s, "buy", qty, limit), 3.0) if ask and qty > 0 else (None, 0.0)
            take = p.pre * (1 + self.RIDE_TAKE * p.overshoot * p.pct)
            stop = p.entry_px * (1 - abs(p.pct) / 2)
            if p.held <= 0:
                p.state = "done"
            elif bid and (mid >= take or mid <= stop or age > p.ramp_s * self.RIDE_TIME_STOP):
                return self._sig(s, "sell", min(p.held, pos), bid * 0.995), 5.0
            return None, 0.0

        # FADE
        if p.core0 < 0:
            p.core0 = max(0, pos)
        if p.state == "derisk":                    # sell the core before the ramp takes it down
            left = p.core0 - p.core_sold - (sell_in_flight or 0)
            floor_px = p.pre * (1 + 0.5 * p.pct)   # never below half the announced move
            qty = min(left, _depth(market, s, "bids", floor_px)) if bid else 0
            if qty > 0 and age < p.ramp_s * 0.5:
                return self._sig(s, "sell", qty, floor_px), 4.0
            if left <= 0 or age >= p.ramp_s * 0.5:
                p.state = "wait"
            return None, 0.0
        owed = p.core_sold - p.core_back
        if p.state == "wait":                      # wait for the overshoot trough
            if age >= p.ramp_s or mid <= p.pre * (1 + p.overshoot * p.pct) * 1.002:
                p.state = "enter"
                p.target = 0 if killed or not p.extra_ok else self._size(s, ask or mid, nw, pos)
            return None, 0.0
        if p.state == "enter":                     # buy the core back, and the extra
            limit = p.pre * (1 + p.pct)            # never above the announced settle level
            want = owed + p.target - p.held
            if want <= 0 or age > p.ramp_s * 2:
                p.state = "hold" if p.held else "restore"
                return None, 0.0
            qty = min(want, _depth(market, s, "asks", limit))
            if not ask or ask > limit or qty <= 0:
                return None, 0.0
            return self._sig(s, "buy", qty, limit), 2.0 + (p.pre - ask) / p.pre
        if p.state == "hold":                      # sell the extra on the rebound
            if p.held <= 0:
                p.state = "restore"
            elif bid and (mid >= p.pre or mid <= p.entry_px * (1 - self.FADE_STOP)
                          or age > p.ramp_s * self.FADE_TIME_STOP):
                return self._sig(s, "sell", min(p.held, pos), bid * 0.995), 5.0
            return None, 0.0
        if owed <= 0:                              # restore: put the core back, whatever the price
            p.state = "done"
            return None, 0.0
        qty = min(owed, _depth(market, s, "asks", ask * 1.003)) if ask else 0
        return (self._sig(s, "buy", qty, ask * 1.003), 1.0) if qty > 0 else (None, 0.0)

    def _size(self, s: str, px: float, nw: float, pos: int) -> int:
        """Shares to buy: the risk budget, capped by the seat, the team and MAX_SHARES."""
        others = self.team_board.get(s, 0) - self.my_board.get(s, 0)
        by_risk = int(self.RISK_FRACTION * nw / px) if px > 0 else 0
        return max(0, min(by_risk, self.MAX_SHARES, SEAT_CAP - max(pos, 0),
                          TEAM_LIMIT - TEAM_BUFFER - others - max(pos, 0)))

    @staticmethod
    def _sig(s: str, side: str, qty: int, px: float) -> Signal | None:
        return Signal(symbol=s, side=side, quantity=qty, price=round(px, 2)) if qty > 0 else None

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        """Each plan keeps its own share count, so the core is never sold twice."""
        if p := self.plans.get(symbol):
            if side == "buy":
                back = min(quantity, p.core_sold - p.core_back)      # restore the core first
                p.core_back += back
                if extra := quantity - back:
                    p.entry_px = (p.entry_px * p.held + price * extra) / (p.held + extra)
                    p.held += extra
            elif p.state in ("derisk", "wait"):
                p.core_sold += quantity
            else:
                p.held = max(0, p.held - quantity)
        super().on_fill(side, symbol, quantity, price)

    def on_ipo(self, symbol: str, lo: float, hi: float, shares: int, data: dict):
        """Bid the top of the range for what the seat can hold and pay for."""
        cash = self._portfolio.cash if self._portfolio else 0.0
        qty = min(SEAT_CAP, int(0.95 * cash / hi)) if hi else 0
        return (qty, hi) if qty > 0 else None


def _depth(market, s: str, side: str, limit: float) -> int:
    """Shares displayed on one side of the book no worse than limit (ORDER_MAX if unknown)."""
    book = getattr(market, "_books", {}).get(s)
    if not book:
        return ORDER_MAX
    ok = (lambda p: p <= limit) if side == "asks" else (lambda p: p >= limit)
    return int(sum(q for p, q in book.get(side, []) if ok(p)))


STRATEGIES = {"momentum": MomentumTrader, "event": EventTrader}
MyTrader = STRATEGIES[os.environ.get("STRATEGY", "momentum")]

# The live plumbing, re-exported for the tests that import it from here
from team.live import WIRE, _Bot  # noqa: E402,F401

if __name__ == "__main__":
    MyTrader().run()
