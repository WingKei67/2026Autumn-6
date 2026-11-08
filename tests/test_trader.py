"""
team/trader.py — the two signals and the hard limits.

Strategies
- MomentumTrader: buys above ENTRY, exits when momentum turns; SIGNAL_DELAY reads the feature late
- EventTrader: RIDE (pct > 0) and FADE (pct < 0), each leg gated by the round-trip fee
- MyTrader is whichever STRATEGY selects
Hard limits (week 5: breakers, stops, liquidation)
- nothing while halted; an order within RESUME_S of TRADING_RESUMED; a missed resume is assumed
- never short, seat cap, team limit, LULD band, cash reserve with orders in flight
- equity floor: no buys, sell down · liquidated: nothing
- one order in flight per symbol until the venue answers
"""

from __future__ import annotations

import pytest

import team.trader as tt
from arena import Signal
from shared.messages import ErrorMsg


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Market:
    """bid/ask per symbol, plus an optional mid history for momentum."""

    def __init__(self, books: dict, history: dict | None = None) -> None:
        self.books, self.history = books, history or {}

    def symbols(self):
        return list(self.books)

    def best_bid(self, s):
        return self.books[s][0]

    def best_ask(self, s):
        return self.books[s][1]

    def mid_price(self, s):
        return sum(self.books[s]) / 2

    def prices(self, s):
        return self.history.get(s, [self.mid_price(s)])


class Portfolio:
    def __init__(self, positions=None, cash=150_000.0) -> None:
        self.positions, self.cash = dict(positions or {}), cash


@pytest.fixture
def bot():
    """The event strategy (the risk tests run on it; the limits are shared)."""
    t = tt.EventTrader()
    t.now = Clock()
    return t


@pytest.fixture
def mo():
    t = tt.MomentumTrader()
    t.now = Clock()
    return t


def fill(bot, sig):
    """The venue ACKs and fills the whole order."""
    bot.on_sent(sig)
    bot.on_answer(sig.symbol)
    bot.on_fill(sig.side, sig.symbol, sig.quantity, sig.price)


NFLX = {"NFLX": (99.9, 100.1)}


def next_order(bot, mkt, pf, ticks: int = 3):
    """The first order within a few ticks (a state machine may spend a tick changing state)."""
    for _ in range(ticks):
        if (sig := bot.on_tick(mkt, pf)) is not None:
            return sig
    return None


# ── Signal 1: events ─────────────────────────────────────────────────────

def test_ride_buys_at_most_half_the_move_and_sells_into_the_peak(bot):
    mkt, pf = Market(NFLX), Portfolio()
    bot.on_tick(mkt, pf)                                          # learn the pre-event mid (100)
    bot.on_event("CALENDAR_EVENT", "", {"pct": 0.05, "ramp_ticks": 15, "overshoot": 1.3,
                                        "affected": ["NFLX"]})
    sig = bot.on_tick(mkt, pf)
    assert sig.side == "buy" and sig.price <= round(100 * (1 + 0.5 * 0.05), 2)
    fill(bot, sig)
    pf.positions["NFLX"] = sig.quantity
    bot.now.t += 8                                                # past the entry window (½ ramp)
    peak = Market({"NFLX": (105.9, 106.1)})                       # ≥ 90 % of the 1.3 × 5 % overshoot
    out = next_order(bot, peak, pf)
    assert out.side == "sell" and out.quantity == sig.quantity


def test_fade_sells_the_core_then_buys_it_back_at_the_trough(bot):
    mkt, pf = Market(NFLX), Portfolio({"NFLX": 20})
    bot.on_tick(mkt, pf)
    bot.on_event("CALENDAR_EVENT", "", {"pct": -0.05, "ramp_ticks": 15, "overshoot": 1.3,
                                        "affected": ["NFLX"]})
    sig = bot.on_tick(mkt, pf)
    assert sig.side == "sell" and sig.quantity == 20              # derisk: the whole core
    fill(bot, sig)
    pf.positions["NFLX"] = 0
    trough = Market({"NFLX": (93.4, 93.6)})                       # the 1.3 × −5 % overshoot
    bot.now.t += 16                                               # the ramp is over
    back = next_order(bot, trough, pf)                            # derisk → wait → enter → buy
    assert back.side == "buy" and back.quantity >= 20             # the core back (and the extra)
    assert back.price <= 100 * (1 - 0.05)                         # never above the settle level


def test_small_event_is_skipped_by_the_fee_gate(bot):
    bot.on_tick(Market(NFLX), Portfolio())
    bot.on_event("CALENDAR_EVENT", "", {"pct": 0.004, "ramp_ticks": 15, "overshoot": 1.3,
                                        "affected": ["NFLX"]})
    assert "NFLX" not in bot.plans                                # designed edge < 2 × taker fee


def test_small_event_is_ignored_but_answered(bot):
    mkt, pf = Market(NFLX), Portfolio()
    bot.on_tick(mkt, pf)
    bot.on_event("CALENDAR_EVENT", "", {"pct": 0.005, "affected": ["NFLX"]})   # under EVENT_MIN
    assert bot.on_tick(mkt, pf) is None
    bot.now.t += tt.EventTrader.REACT_S
    sig = bot.on_tick(mkt, pf)
    assert sig.quantity == 1 and sig.price < 99.9                 # the course's reaction order


# ── Signal 2: momentum ───────────────────────────────────────────────────

def rising(n: int, step: float) -> list[float]:
    return [100.0 * (1 + step) ** i for i in range(n)]


def test_momentum_buys_a_trend_and_exits_when_it_turns(mo):
    hist = rising(mo.LOOKBACK + 1, 0.001)                         # +6 % over the lookback
    mkt, pf = Market({"NFLX": (hist[-1] - 0.05, hist[-1] + 0.05)}, {"NFLX": hist}), Portfolio()
    sig = mo.on_tick(mkt, pf)
    assert sig.side == "buy"
    fill(mo, sig)
    pf.positions["NFLX"] = sig.quantity
    mkt.history["NFLX"] = hist + [hist[0] * 0.99]                 # momentum is now negative
    out = mo.on_tick(mkt, pf)
    assert out.side == "sell" and out.quantity == sig.quantity


def test_momentum_ignores_a_small_move(mo):
    hist = rising(mo.LOOKBACK + 1, mo.ENTRY / mo.LOOKBACK / 2)    # half the entry threshold
    mkt = Market({"NFLX": (hist[-1] - 0.05, hist[-1] + 0.05)}, {"NFLX": hist})
    assert mo.on_tick(mkt, Portfolio()) is None


def test_momentum_delay_reads_the_feature_late(mo):
    hist = [100.0] * (mo.LOOKBACK + 1) + [103.0]                  # the jump is only in the last tick
    mkt = Market({"NFLX": (102.95, 103.05)}, {"NFLX": hist})
    assert mo.momentum(mkt, "NFLX") == pytest.approx(0.03)
    mo.SIGNAL_DELAY = 1
    assert mo.momentum(mkt, "NFLX") == 0.0                        # one tick late: not seen yet
    assert mo.on_tick(mkt, Portfolio()) is None


def test_strategy_switch():
    assert tt.STRATEGIES == {"momentum": tt.MomentumTrader, "event": tt.EventTrader}
    assert issubclass(tt.MyTrader, tt.RiskTrader)


# ── Hard limits ──────────────────────────────────────────────────────────

def test_halt_blocks_orders_and_resume_owes_one(bot):
    mkt, pf = Market(NFLX), Portfolio({"NFLX": 20})
    bot.on_event("TRADING_HALT", "", {"symbol": "NFLX", "duration": 300})
    assert bot._check(Signal(symbol="NFLX", side="sell", quantity=5, price=99.0), pf) is None
    bot.on_event("TRADING_RESUMED", "", {"symbol": "NFLX"})
    bot.now.t += tt.RESUME_S
    sig = bot.on_tick(mkt, pf)
    assert sig.symbol == "NFLX" and sig.quantity == 1


def test_missed_resume_is_assumed_after_grace(bot):
    bot.halt("NFLX", 300)
    bot.now.t += 300 + tt.HALT_GRACE_S
    assert not bot.is_halted("NFLX") and "NFLX" in bot.owe


def test_market_halt_blocks_everything(bot):
    bot.on_event("MARKET_HALT", "", {"duration": 60})
    assert bot.is_halted("AMD") and bot.is_halted("NFLX")


def test_symbol_halted_reject_marks_the_symbol(bot):
    tt._Bot(bot)._on_reject(ErrorMsg(code="SYMBOL_HALTED", message="NFLX is halted. Resume in 120s"))
    assert bot.is_halted("NFLX")


def test_never_short_and_seat_cap(bot):
    pf = Portfolio({"NFLX": 10})
    assert bot._check(Signal(symbol="NFLX", side="sell", quantity=50, price=100.0), pf).quantity == 10
    pf.positions["NFLX"] = tt.SEAT_CAP - 3
    assert bot._check(Signal(symbol="NFLX", side="buy", quantity=50, price=100.0), pf).quantity == 3


def test_team_limit_counts_other_seats(bot):
    bot.team_board, bot.my_board = {"NFLX": tt.TEAM_LIMIT - tt.TEAM_BUFFER - 5}, {"NFLX": 0}
    assert bot._check(Signal(symbol="NFLX", side="buy", quantity=50, price=100.0), Portfolio()).quantity == 5


def test_price_clamped_into_luld_band(bot):
    bot._refs["NFLX"] = 100.0                                    # the venue's mark
    sig = bot._check(Signal(symbol="NFLX", side="buy", quantity=1, price=150.0), Portfolio())
    assert sig.price == pytest.approx(100 * (1 + tt.LULD_BAND))


def test_cash_reserve_counts_orders_in_flight(bot):
    pf = Portfolio(cash=10_000.0)
    bot.on_sent(Signal(symbol="AMD", side="buy", quantity=50, price=100.0))   # 5,000 in flight
    sig = bot._check(Signal(symbol="NFLX", side="buy", quantity=60, price=100.0), pf)
    assert sig.quantity == int((10_000 - 5_000 - 1_000) / (100 * (1 + tt.FEE_BUFFER)))


def test_one_order_in_flight_until_the_venue_answers(bot):
    sig = Signal(symbol="NFLX", side="buy", quantity=5, price=100.0)
    bot.on_sent(sig)
    bot.now.t += 5.0                                              # no ACK yet
    bot.on_tick(Market(NFLX), Portfolio())
    assert bot._check(sig, Portfolio()) is None
    bot.on_answer("NFLX")                                         # ACK
    bot.now.t += tt.INFLIGHT_TTL_S + 0.01
    bot.on_tick(Market(NFLX), Portfolio())
    assert bot._check(sig, Portfolio()) is not None


def test_liquidated_sends_nothing(bot):
    bot.on_event("LIQUIDATION", "", {"team_id": tt._config.TEAM_ID})
    assert bot.on_tick(Market(NFLX), Portfolio({"NFLX": 20})) is None


def test_equity_floor_blocks_buys_and_sells_down(bot):
    bot.start_nw, bot._board_nw = 150_000.0, 100_000.0           # 67 % < 70 %
    pf = Portfolio({"NFLX": 20})
    bot.on_tick(Market(NFLX), pf)
    bot.on_event("CALENDAR_EVENT", "", {"pct": 0.05, "affected": ["NFLX"]})
    sig = bot.on_tick(Market(NFLX), pf)
    assert sig.side == "sell" and sig.quantity == 20
