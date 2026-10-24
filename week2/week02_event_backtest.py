"""
week02_event_backtest.py — replay a recorded block through an arena Trader,
WITH the session events (CALENDAR, CALENDAR_EVENT, SHOCK, …) delivered to
on_event(), and a depth-aware fill model.

scripts/backtest.py is the course's standard backtester; it is the right
tool for a signal that only reads the book. It does not call on_event(),
so an event-driven bot sits idle in it. This harness reuses its
BacktestPortfolio ledger and adds what an event bot needs:

  * events      every session_event in the tape reaches on_event() after
                --latency seconds (default 0.25 s: half of the live loop)
  * clock       the bot's `now` is tape time, so "15 s after the event"
                means what it meant on Friday
  * pacing      on_tick() fires every --tick seconds of tape time (default
                0.5 s = trader/config.py TICK_INTERVAL), not on every book
                update — the live bot gets ~2 decisions a second, not ~20
  * fills       --fill touch : stock backtest.py model, best level only
                --fill sweep : a marketable limit walks the recorded depth
                               up to its limit price (what a live limit
                               order priced through the touch does)
                Either way: taker fee on every fill (--fee, default 0.30 %
                = the hosted venue's TAKER_FEE_RATE), long-only (sells
                capped at holdings, week 2), venue cap --cap shares, and
                unfilled remainders are DROPPED (live, they would rest).
  * inventory   --seed-shares N gives the bot N shares of every symbol at
                the first two-sided mid (the venue grants starting shares
                at the open; week 2 granted 20). Starting net worth = --cash.

What it cannot model (read this before quoting a number): your orders
did not exist on Friday. The liquidity you "take" in the replay was taken
by somebody else that day, nobody reacts to you, and queue position,
resting orders and latency jitter are ignored.

Usage:
    python week02_event_backtest.py sessions/session_week02_<ts>.jsonl
    python week02_event_backtest.py <tape> --bot NaiveEventMomentum --fill touch
    python week02_event_backtest.py <tape> --bot DoNothing
    python week02_event_backtest.py <tape> --module my_bot.py --bot MyTrader
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from dataclasses import dataclass, field

_here = pathlib.Path(__file__).resolve()
for _root in _here.parents:
    if (_root / "scripts" / "backtest.py").exists():
        sys.path.insert(0, str(_root))
        if (_root / "engine").is_dir():
            sys.path.insert(0, str(_root / "engine"))
        break

from scripts.backtest import BacktestPortfolio                       # noqa: E402
from shared.messages import BookSnapshot, Signal, TradeExecution     # noqa: E402
from trader.trader import MarketData                                 # noqa: E402
from arena.trader import Trader                                      # noqa: E402

HOSTED_TAKER_FEE = 0.0030


# ─────────────────────────────────────────────────────────────────────────────
# Baselines (so every result has something to be compared with)
# ─────────────────────────────────────────────────────────────────────────────

class DoNothing(Trader):
    """Keep the granted inventory and the cash. Never trade."""

    def on_tick(self, market, portfolio):
        return None


class BuyAndHold(Trader):
    """Spend ~90 % of cash equal-weight at the open, then hold to the close."""

    def __init__(self) -> None:
        self.todo: dict[str, int] | None = None

    def on_tick(self, market, portfolio):
        syms = [s for s in market.symbols() if market.best_ask(s)]
        if self.todo is None and len(syms) >= 10:
            per = 0.9 * portfolio.cash / len(syms)
            self.todo = {s: int(per / market.best_ask(s)) for s in syms}
        for s, q in (self.todo or {}).items():
            if q > 0 and market.best_ask(s):
                take = min(q, 60)
                self.todo[s] -= take
                return Signal(symbol=s, side="buy", quantity=take,
                              price=round(market.best_ask(s) * 1.003, 2))
        return None


class TickMomentum(Trader):
    """The bot most of us write first: buy strength, dump weakness.

    20-tick mid return above +0.3 % → buy 40; below −0.3 % → sell everything.
    """

    LOOKBACK, THRESH, SIZE = 20, 0.003, 40

    def on_tick(self, market, portfolio):
        for s in market.symbols():
            h = market.prices(s)
            bid, ask = market.best_bid(s), market.best_ask(s)
            if len(h) < self.LOOKBACK or not (bid and ask):
                continue
            r = h[-1] / h[-self.LOOKBACK] - 1
            pos = portfolio.positions.get(s, 0)
            if r > self.THRESH and pos < 200 and portfolio.can_buy(s, self.SIZE, ask):
                return Signal(symbol=s, side="buy", quantity=self.SIZE, price=round(ask * 1.003, 2))
            if r < -self.THRESH and pos > 0:
                return Signal(symbol=s, side="sell", quantity=min(pos, 60), price=round(bid * 0.997, 2))
        return None


BASELINES = {c.__name__: c for c in (DoNothing, BuyAndHold, TickMomentum)}


# ─────────────────────────────────────────────────────────────────────────────
# The engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Fill:
    ts: float
    symbol: str
    side: str
    price: float
    quantity: int
    fee: float


@dataclass
class Result:
    name: str
    start_nw: float
    marks: list[tuple[float, float]] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    events: list[tuple[float, str, str]] = field(default_factory=list)
    signals: int = 0
    unfilled: int = 0
    errors: int = 0
    first_error: str = ""

    @property
    def pnl(self) -> float:
        return (self.marks[-1][1] if self.marks else self.start_nw) - self.start_nw

    @property
    def max_drawdown(self) -> float:
        peak, worst = 0.0, 0.0
        for _, v in self.marks:
            peak = max(peak, v)
            worst = max(worst, peak - v)
        return worst

    @property
    def fees(self) -> float:
        return sum(f.fee for f in self.fills)


class EventBacktester:
    """Replays one tape through one arena Trader. See the module docstring."""

    def __init__(self, bot, *, cash: float = 250_000.0, fill: str = "sweep",
                 fee: float = HOSTED_TAKER_FEE, latency: float = 0.25,
                 tick: float = 0.5, cap: int = 400, seed_shares: int = 0) -> None:
        self.bot, self.fill, self.fee = bot, fill, fee
        self.latency, self.tick, self.cap = latency, tick, cap
        self.seed_shares = seed_shares
        self.market = MarketData()
        self.pf = BacktestPortfolio(cash)
        self.books: dict[str, tuple[list, list]] = {}
        self.used: dict[tuple[str, str], int] = {}   # liquidity we took from the current snapshot
        self.marks: dict[str, float] = {}            # last TWO-SIDED mid per symbol
        self.ts = 0.0
        if hasattr(bot, "now"):
            bot.now = lambda: self.ts

    # -- fills ------------------------------------------------------------

    def _execute(self, sig: Signal) -> Fill | None:
        bids, asks = self.books.get(sig.symbol, ([], []))
        held = self.pf.positions.get(sig.symbol, 0)
        if sig.side == "buy":
            qty_cap, levels = self.cap - max(held, 0), asks
            ok = lambda p: p <= sig.price
        else:
            qty_cap, levels = max(held, 0), bids      # long-only
            ok = lambda p: p >= sig.price
        want = min(int(sig.quantity), qty_cap)
        if want <= 0 or not levels:
            return None
        if self.fill == "touch":
            levels = levels[:1]
        skip = self.used.get((sig.symbol, sig.side), 0)
        got, notional = 0, 0.0
        for px, q in levels:
            if not ok(px) or got >= want:
                break
            q = int(q)
            if skip >= q:
                skip -= q
                continue
            q -= skip
            skip = 0
            take = min(q, want - got)
            got += take
            notional += take * px
        if got <= 0:
            return None
        fee = notional * self.fee
        if sig.side == "buy":
            if self.pf.cash < notional + fee:
                return None
            self.pf.apply_buy(sig.symbol, notional / got, got, fee)
        else:
            self.pf.apply_sell(sig.symbol, notional / got, got, fee)
        self.used[(sig.symbol, sig.side)] = self.used.get((sig.symbol, sig.side), 0) + got
        return Fill(self.ts, sig.symbol, sig.side, notional / got, got, fee)

    # -- main loop --------------------------------------------------------

    def run(self, path: pathlib.Path, name: str = "") -> Result:
        res = Result(name or type(self.bot).__name__, self.pf.starting_cash)
        pending: list[tuple[float, dict]] = []
        is_open, seeded, next_tick = False, False, 0.0
        with open(path) as fh:
            for line in fh:
                rec = json.loads(line)
                msg = rec.get("msg") or {}
                kind = msg.get("type")
                if kind not in ("book_snapshot", "trade_execution", "session_event"):
                    continue
                self.ts = float(rec["ts"])
                if kind == "book_snapshot":
                    snap = BookSnapshot.model_validate(msg)
                    self.market.update_book(snap)
                    self.books[snap.symbol] = (snap.bids, snap.asks)
                    self.used.pop((snap.symbol, "buy"), None)
                    self.used.pop((snap.symbol, "sell"), None)
                    if snap.bids and snap.asks:
                        self.marks[snap.symbol] = (snap.bids[0][0] + snap.asks[0][0]) / 2
                elif kind == "trade_execution":
                    self.market.update_trade(TradeExecution.model_validate(msg))
                    continue
                else:
                    ev = msg.get("event")
                    if ev == "SESSION_OPEN":
                        is_open = True
                    elif ev == "SESSION_CLOSED":
                        break
                    pending.append((self.ts + self.latency, msg))
                    continue

                if not is_open:
                    continue
                if self.seed_shares and not seeded:
                    seeded = self._seed()
                    if seeded:
                        res.start_nw = self._nw()
                if self.ts < next_tick:
                    continue
                next_tick = self.ts + self.tick

                while pending and pending[0][0] <= self.ts:
                    _, m = pending.pop(0)
                    if m.get("event") not in ("SESSION_OPEN", "SESSION_CLOSED"):
                        res.events.append((self.ts, m.get("event"), m.get("message", "")))
                        try:
                            self.bot.on_event(m.get("event"), m.get("message", ""), m.get("data") or {})
                        except Exception as exc:            # never crash the replay
                            res.errors += 1
                            res.first_error = res.first_error or repr(exc)
                try:
                    sig = self.bot.on_tick(self.market, self.pf)
                except Exception as exc:
                    res.errors += 1
                    res.first_error = res.first_error or repr(exc)
                    sig = None
                if sig is not None:
                    res.signals += 1
                    f = self._execute(sig)
                    if f:
                        res.fills.append(f)
                        try:
                            self.bot.on_fill(f.side, f.symbol, f.quantity, f.price)
                        except Exception as exc:
                            res.errors += 1
                            res.first_error = res.first_error or repr(exc)
                    else:
                        res.unfilled += 1
                res.marks.append((self.ts, self._nw()))
        return res

    def _nw(self) -> float:
        """Cash + positions at the last two-sided mid. A one-sided book's
        mid_price field is a model value, not a price you could trade at — the
        venue marks AMD and NFLX hundreds of dollars apart on it at the close."""
        self.pf.refresh(self.market)                 # keeps the live-like fields current
        return self.pf.cash + sum(q * self.marks.get(s, self.pf.avg_cost.get(s, 0.0))
                                  for s, q in self.pf.positions.items() if q)

    def _seed(self) -> bool:
        """Grant seed_shares of every two-sided symbol at its mid (paid from cash)."""
        syms = [s for s in self.market.symbols()
                if self.market.best_bid(s) and self.market.best_ask(s)]
        if len(syms) < 10:
            return False
        for s in syms:
            self.pf.apply_buy(s, self.market.mid_price(s), self.seed_shares, 0.0)
        return True


def load_bot(module: str | None, name: str):
    """A baseline by name, or a class from a .py file / import path."""
    if name in BASELINES:
        return BASELINES[name]()
    if module is None:
        module = str(_here.with_name("week02_alpha_bot.py"))
    if module.endswith(".py"):
        spec = importlib.util.spec_from_file_location("bot_under_test", module)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod            # dataclasses look the module up
        spec.loader.exec_module(mod)          # type: ignore[union-attr]
    else:
        mod = importlib.import_module(module)
    return getattr(mod, name)()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("tape")
    ap.add_argument("--module", default=None, help="bot file or import path (default: week02_alpha_bot.py)")
    ap.add_argument("--bot", default="ShockSwitchTrader", help=f"class name, or one of {list(BASELINES)}")
    ap.add_argument("--cash", type=float, default=250_000.0)
    ap.add_argument("--fill", choices=("touch", "sweep"), default="sweep")
    ap.add_argument("--fee", type=float, default=HOSTED_TAKER_FEE)
    ap.add_argument("--latency", type=float, default=0.25)
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--seed-shares", type=int, default=20)
    ap.add_argument("--fills", action="store_true", help="print every fill")
    a = ap.parse_args(argv)

    bot = load_bot(a.module, a.bot)
    bt = EventBacktester(bot, cash=a.cash, fill=a.fill, fee=a.fee, latency=a.latency,
                         tick=a.tick, cap=a.cap, seed_shares=a.seed_shares)
    r = bt.run(pathlib.Path(a.tape), a.bot)
    print(f"{r.name}  fill={a.fill}  P&L {r.pnl:+,.2f}  on {r.start_nw:,.0f}  "
          f"({r.pnl / r.start_nw:+.3%})  fills={len(r.fills)}  fees={r.fees:,.2f}  "
          f"maxDD={r.max_drawdown:,.2f}  signals={r.signals} unfilled={r.unfilled}  errors={r.errors}")
    if r.first_error:
        print("  first error:", r.first_error)
    if a.fills:
        t0 = r.marks[0][0] if r.marks else 0
        for f in r.fills:
            print(f"  +{f.ts - t0:7.1f}s {f.side:4s} {f.quantity:4d} {f.symbol:6s} @ {f.price:9.2f}  fee {f.fee:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
