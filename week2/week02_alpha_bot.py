"""
week02_alpha_bot.py — a shock-aware momentum / mean-reversion SWITCH.

FINM 33500, Week 2 recap (tape of Fri 2026-10-09). Read
course/systematic-trading/week02_alpha_recap.md first: it explains the
hypothesis, the numbers and — most importantly — when this would NOT work.

The idea in one paragraph
-------------------------
The venue broadcasts every calendar shock as a CALENDAR_EVENT the moment it
fires, and the message carries the announced move (data["pct"]), how many
venue ticks it ramps over (data["ramp_ticks"]) and how far fair value
overshoots before settling (data["overshoot"]). So the shape of the next
~30 seconds is public. The bot switches regime on that message:

    pct > 0  →  RIDE   buy at once, sell into the overshoot peak (momentum)
    pct < 0  →  FADE   sell what you hold now, then buy the overshoot trough
                       once the ramp is done and sell the rebound (reversion)

Long-only, because week 2 locks short selling. Between events the bot does
nothing — that is deliberate, and it is where your own work starts (TODOs).

Run it
------
    Live, against a LOCAL venue (never point experiments at the hosted one):
        make exchange                    # terminal 1
        make broker                      # terminal 2 (somebody must quote)
        EXCHANGE_HOST=localhost TICK_INTERVAL=0.1 TEAM_ID=<your_trader_id> \
            python course/systematic-trading/examples/week02_alpha_bot.py
        # TICK_INTERVAL=0.1: the AAPL leg on Friday was a sub-second race
        # (the default 0.5 s loop missed it). 10 orders/s is inside the
        # ORDER_QUOTA_PER_TICK=20 budget because we send ONE order per tick.
        # to see it trade, start the exchange on a copy of week02.json whose
        # events are moved early (tick 20 / tick 70):
        #   SCENARIO_PATH=my_events.json SEASON_PERSIST=false make exchange

    Offline, on a recorded block (the way the recap's numbers were made):
        python course/systematic-trading/examples/week02_event_backtest.py \
            sessions/session_week02_<ts>.jsonl --bot ShockSwitchTrader

    In the student template repo copy this file and week02_event_backtest.py
    next to team/trader.py; both find the engine on their own.
"""

from __future__ import annotations

import math
import pathlib
import sys
import time
from dataclasses import dataclass, field

# Find the engine whether this runs from the AlgoArena repo
# (course/systematic-trading/examples/) or from a student template repo.
_here = pathlib.Path(__file__).resolve()
for _root in _here.parents:
    if (_root / "arena" / "trader.py").exists():
        sys.path.insert(0, str(_root))
        break
    if (_root / "engine" / "arena" / "trader.py").exists():
        sys.path.insert(0, str(_root / "engine"))
        break

from arena.trader import Trader          # noqa: E402
from shared.messages import Signal       # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Parameters. Every number here comes from the event message or from a risk
# rule written down BEFORE looking at P&L — not from fitting Friday's tape.
# ─────────────────────────────────────────────────────────────────────────────

VENUE_TICK_SEC = 1.0        # one venue tick ≈ 1.04 s on the hosted box (ramp_ticks are ticks)
VENUE_POSITION_CAP = 400    # week 2 position_limit (teacher/season/week02.json)
MAX_SHARES = 200            # our own cap per symbol: half the venue's
RISK_FRACTION = 0.25        # max notional per symbol, as a fraction of net worth
MAX_CHUNK = 60              # shares per order: one order sweeps at most this much
ENTRY_SLIPPAGE = 0.5        # RIDE: pay at most pre × (1 + 0.5 × pct) — half the move
RIDE_TAKE = 0.9             # RIDE: exit at 90 % of the announced overshoot
FADE_STOP = 0.015           # FADE: stop out 1.5 % below our entry
RIDE_TIME_STOP = 3.0        # × ramp: after the ramp the edge is gone
FADE_TIME_STOP = 6.0        # × ramp: give the rebound ~90 s, then leave
KILL_SWITCH = 0.03          # stop opening plans after a 3 % drawdown from the high
DEFAULT_TAKER = 0.0030      # hosted venue's taker fee; FEE_SCHEDULE overrides it at connect
EDGE_OVER_COST = 1.0        # trade a leg only if its DESIGNED edge > this × round-trip fees


@dataclass
class Plan:
    """What the bot intends to do in one symbol after one event."""

    symbol: str
    mode: str                 # "ride", "fade" (or the naive "chase")
    pre: float                # mid just before the event
    pct: float                # announced move (signed)
    overshoot: float
    t0: float                 # when we heard about it (bot clock)
    ramp_s: float             # ramp length in seconds
    state: str = "enter"      # ride: enter → hold → done ; fade: derisk → wait → enter → hold → done
    target_qty: int = 0       # extra shares this plan trades (on top of the core)
    held: int = 0             # of those, currently held (fills update it)
    entry_px: float = 0.0
    core0: int = -1           # inventory we held when the news broke
    core_sent: int = 0        # core shares we have offered for sale
    core_sold: int = 0        # core shares sold before the drop
    core_back: int = 0        # core shares bought back
    extra_ok: bool = True     # FADE: is the designed rebound worth more than the fees?
    log: list = field(default_factory=list)


class ShockSwitchTrader(Trader):
    """Ride announced positive shocks, fade the overshoot of negative ones."""

    FADE = True               # the switch; NaiveEventMomentum sets it False

    def __init__(self) -> None:
        self.plans: dict[str, Plan] = {}
        self.done: list[Plan] = []
        self._mids: dict[str, float] = {}
        self._peak_nw = 0.0
        self.taker = DEFAULT_TAKER
        self.skipped: list[tuple[str, str, float, float]] = []   # (symbol, leg, edge, cost)
        self.now = time.time  # the offline backtester replaces this with tape time

    # ── hooks ────────────────────────────────────────────────────────────

    def on_event(self, event: str, message: str, data: dict) -> None:
        """Turn a fired calendar event into one Plan per affected symbol."""
        if event == "FEE_SCHEDULE" and data.get("taker") is not None:
            self.taker = float(data["taker"])
        if event == "CALENDAR_EVENT":
            pct = float(data.get("pct") or 0.0)
            ramp_s = max(1, int(data.get("ramp_ticks") or 15)) * VENUE_TICK_SEC
            ov = float(data.get("overshoot") or 1.0)
            cost = 2 * self.taker * EDGE_OVER_COST          # pay the taker fee in AND out
            # The designed edge of each leg, from the message alone (no tape):
            ride_edge = (RIDE_TAKE * ov - ENTRY_SLIPPAGE) * pct   # buy ≤ half-way, sell at 90 % of peak
            drop_edge = (ov - 0.5) * abs(pct)                     # sell ≥ half-way down, rebuy at the trough
            rebound_edge = (ov - 1.0) * abs(pct)                  # trough → settle level
            for sym in data.get("affected") or []:
                pre = self._mids.get(sym)
                if not pre:
                    continue                                       # we have never seen this book
                if pct > 0:
                    if ride_edge <= cost:
                        self.skipped.append((sym, "ride", ride_edge, cost))
                        continue
                    mode = "ride"
                else:
                    if drop_edge <= cost:
                        self.skipped.append((sym, "derisk", drop_edge, cost))
                        continue
                    mode = "fade" if self.FADE else "chase"
                p = Plan(sym, mode, pre, pct, ov, self.now(), ramp_s,
                         state="enter" if mode == "ride" else "derisk")
                if mode == "fade" and rebound_edge <= cost:
                    p.extra_ok = False                             # restore the core, nothing more
                    self.skipped.append((sym, "fade extra", rebound_edge, cost))
                self.plans[sym] = p
        # TODO (Level 5): "CALENDAR" arrives ~120 ticks BEFORE an event with
        #   kind/symbol/tick/magnitude_range but NO direction. What should your
        #   inventory in that symbol be when the announcement lands? Decide
        #   here, act in on_tick.
        # TODO (Level 5): "SHOCK" is a teacher-injected move. Its data has
        #   "affected" and target "prices" but no "pct" — derive the move
        #   from your cached mid and reuse the same Plan machinery. Check the
        #   ramp/overshoot in exchange/calendar.py before you assume it is the
        #   same shape.

    def on_fill(self, side: str, symbol: str, quantity: int, price: float) -> None:
        """Keep each plan's own share count, so we never sell the core inventory twice."""
        p = self.plans.get(symbol)
        if not p:
            return
        p.log.append((self.now(), side, quantity, price))
        if side == "buy":
            back = min(quantity, p.core_sold - p.core_back)   # restore the core first
            p.core_back += back
            quantity -= back
            if quantity > 0:
                cost = p.entry_px * p.held + price * quantity
                p.held += quantity
                p.entry_px = cost / p.held
        elif p.state in ("derisk", "wait"):  # selling the core ahead of the drop
            p.core_sold += quantity
        else:
            p.held = max(0, p.held - quantity)

    def on_tick(self, market, portfolio) -> Signal | None:
        """At most ONE order per tick: exits first, then entries, by urgency."""
        for sym in market.symbols():
            mid = market.mid_price(sym)
            if mid and market.best_bid(sym) and market.best_ask(sym):
                self._mids[sym] = mid
        nw = _net_worth(portfolio, market)
        self._peak_nw = max(self._peak_nw, nw)
        halted = nw < self._peak_nw * (1 - KILL_SWITCH)

        candidates: list[tuple[float, Signal, Plan]] = []
        for sym, p in list(self.plans.items()):
            sig, urgency = self._step(p, market, portfolio, nw, halted)
            if p.state == "done":
                self.done.append(self.plans.pop(sym))
            if sig is not None:
                candidates.append((urgency, sig, p))
        if not candidates:
            # TODO (Level 3/5): between events this bot is flat and idle. The
            #   recap shows microprice − mid has a small but real 2–5 s edge on
            #   this venue. Design a quiet-time signal (and test it with the
            #   event backtester) — but count its fees: 0.30 % taker each way
            #   on the hosted venue is more than most of that edge.
            return None
        candidates.sort(key=lambda c: -c[0])
        _, sig, p = candidates[0]
        if p.state == "derisk":
            p.core_sent += sig.quantity     # count only what we actually send
        return sig

    # ── the state machine ────────────────────────────────────────────────

    def _step(self, p: Plan, market, portfolio, nw: float, halted: bool):
        sym = p.symbol
        bid, ask = market.best_bid(sym), market.best_ask(sym)
        # A one-sided book's mid is a model value: use the last two-sided mid.
        # Buys need an ask, sells need a bid — an empty side never blocks an exit.
        mid = market.mid_price(sym) if (bid and ask) else self._mids.get(sym)
        if not (bid or ask) or not mid:
            return None, 0.0
        age = self.now() - p.t0
        pos = int(portfolio.positions.get(sym, 0))

        # ---- RIDE: positive news, momentum --------------------------------
        if p.mode == "ride":
            if p.state == "enter":
                if not p.target_qty:
                    p.target_qty = self._size(sym, ask or mid, nw, pos)
                limit = p.pre * (1 + ENTRY_SLIPPAGE * p.pct)
                want = p.target_qty - p.held
                if halted or want <= 0 or age > p.ramp_s * 0.5 or (ask and ask > limit):
                    p.state = "hold" if p.held else "done"
                    return None, 0.0
                if not ask:
                    return None, 0.0            # ask side swept: wait a tick
                qty = min(want, MAX_CHUNK, _depth(market, sym, "asks", limit))
                if qty <= 0 or not portfolio.can_buy(sym, qty, limit):
                    return None, 0.0
                return Signal(symbol=sym, side="buy", quantity=qty, price=round(limit, 2)), 3.0
            if p.state == "hold":
                take = p.pre * (1 + RIDE_TAKE * p.overshoot * p.pct)
                stop = p.entry_px * (1 - abs(p.pct) / 2) if p.entry_px else 0.0
                if p.held <= 0:
                    p.state = "done"
                    return None, 0.0
                if bid and (mid >= take or mid <= stop or age > p.ramp_s * RIDE_TIME_STOP):
                    return self._exit(p, bid, pos), 5.0
            return None, 0.0

        # ---- FADE (or naive CHASE): negative news ------------------------
        if p.core0 < 0:
            p.core0 = max(0, pos)              # inventory we held when the news broke
        if p.state == "derisk":
            # Sell that inventory now, before the ramp takes the price down
            # (long-only: this is the only way to be "short" the drop in week 2).
            left = p.core0 - p.core_sent
            floor_px = p.pre * (1 + 0.5 * p.pct)      # never sell below half the announced move
            qty = min(left, MAX_CHUNK, _depth(market, sym, "bids", floor_px)) if bid else 0
            if qty > 0 and age < p.ramp_s * 0.5:
                return Signal(symbol=sym, side="sell", quantity=qty, price=round(floor_px, 2)), 4.0
            if left <= 0 or age >= p.ramp_s * 0.5:
                p.state = "wait"
            return None, 0.0

        owed = p.core_sold - p.core_back       # core shares we still have to buy back
        if p.mode == "chase":
            # The naive version, no switch: "bad news → get out, get back in
            # once it looks safe again", i.e. once price is back above `pre`.
            if p.state == "wait" and age > p.ramp_s and mid > p.pre:
                p.state = "restore"
            if p.state == "wait" and age > p.ramp_s * 8:
                p.state = "restore"
            if p.state == "restore":
                return self._restore(p, market, portfolio, owed)
            return None, 0.0

        trough = p.pre * (1 + p.overshoot * p.pct)
        if p.state == "wait":
            # Wait for the overshoot: either the ramp is over or price is there.
            if age >= p.ramp_s or mid <= trough * 1.002:
                p.state = "enter"
                p.target_qty = 0 if (halted or not p.extra_ok) else self._size(sym, ask or mid, nw, pos)
            return None, 0.0
        if p.state == "enter":
            limit = p.pre * (1 + p.pct)          # never pay above the announced settle level
            want = owed + p.target_qty - p.held
            if want <= 0 or age > p.ramp_s * 2:
                p.state = "hold" if p.held else "restore"
                return None, 0.0
            qty = min(want, MAX_CHUNK, _depth(market, sym, "asks", limit))
            if not ask or ask > limit or qty <= 0 or not portfolio.can_buy(sym, qty, limit):
                return None, 0.0
            edge = (p.pre - ask) / p.pre        # cheapest-relative-to-pre first
            return Signal(symbol=sym, side="buy", quantity=qty, price=round(limit, 2)), 2.0 + edge
        if p.state == "hold":
            if p.held <= 0:
                p.state = "restore"
                return None, 0.0
            stop = p.entry_px * (1 - FADE_STOP)
            if bid and (mid >= p.pre or mid <= stop or age > p.ramp_s * FADE_TIME_STOP):
                return self._exit(p, bid, pos), 5.0
            return None, 0.0
        if p.state == "restore":
            return self._restore(p, market, portfolio, owed)
        return None, 0.0

    def _restore(self, p: Plan, market, portfolio, owed: int):
        """Put the core inventory back, whatever the price: we derisked, we did not take a view."""
        ask = market.best_ask(p.symbol)
        if owed <= 0 or not ask:
            p.state = "done" if owed <= 0 else p.state
            return None, 0.0
        px = ask * 1.003
        qty = min(owed, MAX_CHUNK, _depth(market, p.symbol, "asks", px))
        if qty <= 0 or not portfolio.can_buy(p.symbol, qty, px):
            return None, 0.0
        return Signal(symbol=p.symbol, side="buy", quantity=qty, price=round(px, 2)), 1.0

    # ── helpers ──────────────────────────────────────────────────────────

    def _size(self, sym: str, px: float, nw: float, pos: int) -> int:
        """Shares to buy: the smallest of our cap, the venue cap and the risk budget."""
        by_risk = int(RISK_FRACTION * nw / px) if px > 0 else 0
        return max(0, min(MAX_SHARES, VENUE_POSITION_CAP - max(pos, 0), by_risk))
        # TODO (Level 4): size by the announced |pct| and by the depth you saw
        #   BEFORE the event — the recap shows the top of book was 1–30 shares.

    def _exit(self, p: Plan, bid: float, pos: int) -> Signal | None:
        qty = min(p.held, max(pos, 0), MAX_CHUNK)
        if qty <= 0:
            p.state = "done"
            return None
        # Marketable sell, willing to walk 0.5 % below the bid. TODO (Level 5):
        #   on a 1–4-share book this sweeps several levels — try working the
        #   exit passively one tick inside the spread and measure the difference.
        return Signal(symbol=p.symbol, side="sell", quantity=qty, price=round(bid * 0.995, 2))


class NaiveEventMomentum(ShockSwitchTrader):
    """Same bot with the switch removed: bad news → sell, buy back when it 'recovers'."""

    FADE = False


def _depth(market, sym: str, side: str, limit: float) -> int:
    """Shares displayed on one side of the book at prices no worse than limit."""
    book = getattr(market, "_books", {}).get(sym) or {}
    levels = book.get(side) or []
    ok = (lambda p: p <= limit) if side == "asks" else (lambda p: p >= limit)
    return int(sum(q for p, q in levels if ok(p)))


def _net_worth(portfolio, market) -> float:
    try:
        nw = portfolio.net_worth(market)
    except TypeError:
        nw = portfolio.net_worth()
    return float(nw) if nw and not math.isnan(nw) else float(getattr(portfolio, "cash", 0.0))


if __name__ == "__main__":
    ShockSwitchTrader().run()
