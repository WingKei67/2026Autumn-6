#!/usr/bin/env python
"""
scripts/phase2_sim.py — Phase 2 backtest of team/trader.py's MomentumTrader,
in the course simulator:

    SimSession().run(n_ticks=5000, strategies=[("me", as_signal_fn(MomentumTrader()))], seed=s)

1. TRAIN  seeds 0–9: a grid of LOOKBACK × ENTRY. Rule, fixed before looking:
          among settings that make ≥ 10 round trips per seed, take the one with
          the highest mean P&L BEFORE fees (the strongest raw edge).
2. TEST   seeds 10–29 with that setting: P&L after fees, dispersion, hit rate, drawdown.
3. LOOK-AHEAD  the same test seeds with the feature read 1, 2, 5 ticks late.

Fees: SimSession splits a trade's fee between both sides, so fee_rate=0.006 makes
our side pay 0.30 % — the hosted venue's taker fee.

Usage:  python scripts/phase2_sim.py
Output: data/phase2/sim.md, data/phase2/sim_curves.json, evidence/phase2_sim.txt
"""

from __future__ import annotations

import json
import logging
import pathlib
import statistics as st
import sys
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "engine"), str(ROOT)]

import plugins.securities.defaults as securities   # noqa: E402
from arena.trader import as_signal_fn               # noqa: E402
from sim.session import SimSession                  # noqa: E402

import team.trader as tt                             # noqa: E402

N_TICKS = 5000
SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA"]
FEE_RATE = 0.006          # → we pay 0.30 % per fill (half of the trade's fee)
TRAIN_SEEDS = range(0, 10)
TEST_SEEDS = range(10, 30)
GRID_LOOKBACK = (10, 30, 60, 120)
GRID_ENTRY = (0.0025, 0.005, 0.01)
MIN_TRIPS = 10
DELAYS = (0, 1, 2, 5)
OUT = ROOT / "data" / "phase2"


def round_trips(fills: list[tuple[str, str, int, float]], fee: float) -> list[float]:
    """Net P&L of each buy → sell round trip (FIFO per symbol), fees on both legs.
    fills = [(side, symbol, qty, price), …] in time order."""
    lots: dict[str, deque] = defaultdict(deque)
    out = []
    for side, sym, qty, px in fills:
        if side == "buy":
            lots[sym].append([px, qty])
            continue
        pnl = 0.0
        while qty and lots[sym]:
            lot = lots[sym][0]
            take = min(qty, lot[1])
            pnl += take * (px - lot[0]) - fee * take * (px + lot[0])
            lot[1] -= take
            qty -= take
            if not lot[1]:
                lots[sym].popleft()
        out.append(pnl)
    return out


def run(seed: int, lookback: int, entry: float, delay: int = 0) -> dict:
    """One 5,000-tick SimSession with our bot; returns P&L, fees, drawdown, round trips."""
    logging.disable(logging.CRITICAL)
    securities.FUNDAMENTAL_SEED = f"phase2-{seed}"   # SimSession's seed= alone does not move prices
    bot = tt.MomentumTrader()
    bot.LOOKBACK, bot.ENTRY, bot.SIGNAL_DELAY = lookback, entry, delay
    as_fn = as_signal_fn(bot)
    held: dict[str, int] = {}

    def signal_fn(symbol, prices, history, book, portfolio):
        bot.now = lambda n=len(history): float(n)            # one tick = one second
        pos = (portfolio.get("positions") or {}).get(symbol, 0)
        change, held[symbol] = pos - held.get(symbol, 0), pos
        if change:                                            # the sim bridge never calls on_fill
            bot.on_fill("buy" if change > 0 else "sell", symbol, abs(change), prices.get(symbol, 0.0))
        return as_fn(symbol, prices, history, book, portfolio)

    res = SimSession().run(n_ticks=N_TICKS, strategies=[("me", signal_fn)], symbols=SYMBOLS,
                           fee_rate=FEE_RATE, seed=seed, verbose=False)
    me = next(t for t in res.traders if t.team_id == "me")
    fees = next(r for r in res.leaderboard if r["team_id"] == "me")["total_fees_paid"]
    curve = [v - me.pnl_curve[0] for v in me.pnl_curve]
    peak = dd = 0.0
    for v in curve:
        peak, dd = max(peak, v), max(dd, peak - v)
    fills = [("buy" if f.buyer_id == "me" else "sell", f.symbol, f.quantity, f.price) for f in me.fills]
    return {"seed": seed, "lookback": lookback, "entry": entry, "delay": delay,
            "pnl": curve[-1], "gross": curve[-1] + fees, "fees": fees, "max_dd": dd,
            "trips": round_trips(fills, FEE_RATE / 2), "curve": curve[::25] + curve[-1:]}


def many(seeds, lookback, entry, delay=0) -> list[dict]:
    with ProcessPoolExecutor() as pool:
        n = len(seeds)
        return list(pool.map(run, seeds, [lookback] * n, [entry] * n, [delay] * n))


def row(label: str, rs: list[dict]) -> str:
    pnl = [r["pnl"] for r in rs]
    trips = [t for r in rs for t in r["trips"]]
    sd = st.pstdev(pnl)
    hit = f"{sum(t > 0 for t in trips) / len(trips):.0%}" if trips else "—"
    return (f"| {label} | {len(rs)} | {st.mean(pnl):+,.0f} | {sd:,.0f} | {sd / len(rs) ** 0.5:,.0f} "
            f"| {st.mean(r['gross'] for r in rs):+,.0f} | {hit} | {len(trips) / len(rs):.0f} "
            f"| {st.mean(r['fees'] for r in rs):,.0f} | {max(r['max_dd'] for r in rs):,.0f} |")


HEAD = ["| set | seeds | mean P&L | sd across seeds | s.e. | mean P&L before fees | hit rate (round trips) "
        "| round trips / seed | mean fees | worst max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]


def main() -> int:
    lines = [f"# Momentum in SimSession — {N_TICKS} ticks, {', '.join(SYMBOLS)}, $100k, "
             f"0.30 % fee per fill (generated by scripts/phase2_sim.py)", "",
             f"## 1. Training grid (seeds {TRAIN_SEEDS[0]}–{TRAIN_SEEDS[-1]})", "", *HEAD]
    grid = {}
    for look in GRID_LOOKBACK:
        for entry in GRID_ENTRY:
            grid[(look, entry)] = rs = many(TRAIN_SEEDS, look, entry)
            lines.append(row(f"lookback {look}, entry {entry:.2%}", rs))
    eligible = {k: v for k, v in grid.items()
                if sum(len(r["trips"]) for r in v) / len(v) >= MIN_TRIPS} or grid
    look, entry = max(eligible, key=lambda k: st.mean(r["gross"] for r in eligible[k]))
    lines += ["", f"Chosen (highest P&L before fees with ≥ {MIN_TRIPS} round trips/seed): "
                  f"**lookback {look}, entry {entry:.2%}**", "",
              f"## 2. Test (seeds {TEST_SEEDS[0]}–{TEST_SEEDS[-1]}, never used for choosing)", "", *HEAD]
    test = many(TEST_SEEDS, look, entry)
    lines.append(row("momentum", test))
    lines += ["", "## 3. Look-ahead check (test seeds, feature read k ticks late)", "", *HEAD]
    delayed = {0: test}
    for d in DELAYS[1:]:
        delayed[d] = many(TEST_SEEDS, look, entry, d)
    for d in DELAYS:
        lines.append(row(f"delay {d}", delayed[d]))

    text = "\n".join(lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sim.md").write_text(text)
    (OUT / "sim_curves.json").write_text(json.dumps(
        {"chosen": [look, entry], "test": [r["curve"] for r in test]}))
    (ROOT / "evidence" / "phase2_sim.txt").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
