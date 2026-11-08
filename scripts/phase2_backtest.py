#!/usr/bin/env python
"""
scripts/phase2_backtest.py — team/trader.py on the recorded class sessions (weeks 3–6).

A second, out-of-sample check of the Phase 2 signal: MomentumTrader's parameters
were chosen in SimSession (scripts/phase2_sim.py), so none of these tapes was used
for fitting. EventTrader runs on the same tapes for comparison.

Each run is paired with a DoNothing bot on the same tape (it keeps the 20 granted
shares of every symbol): excess = bot P&L − hold P&L, after the 0.30 % taker fee.
The momentum look-ahead check reads the feature 1, 2, 5 snapshots late.

Usage:  python scripts/phase2_backtest.py
Output: data/phase2/recorded.md, data/phase2/recorded_curves.json, evidence/phase2_backtest.txt
"""

from __future__ import annotations

import json
import logging
import pathlib
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "engine"), str(ROOT)]

from scripts.phase2_sim import round_trips                                 # noqa: E402
from week2.week02_event_backtest import DoNothing, EventBacktester, Result  # noqa: E402

OUT = ROOT / "data" / "phase2"
CASH = 150_000.0          # trader_2's seat
SEAT_CAP = 150
SEED_SHARES = 20          # the venue grants 20 shares of every symbol at the open
LATENCY_S = 0.25          # event message → bot
FEE = 0.003               # the hosted venue's taker fee (the harness default)
TAPES = {
    "week03": "sessions/session_week03_20261016_204000_market.jsonl",
    "week04": "sessions/session_week04_20261023_204025_market.jsonl",
    "week05": "sessions/session_week05_20261029_221018_market.jsonl",
    "week06": "sessions/session_week06_20261106_214023_market.jsonl",
}


class Phase2Backtester(EventBacktester):
    """The course's EventBacktester plus two things that happen live:
    - no fill while the venue has the symbol halted (live: rejected)
    - the bot hears that its order went out and was ACK'd (on_sent / on_answer),
      so its one-order-in-flight-per-symbol rule works as it does live."""

    def run(self, path: pathlib.Path, name: str = "") -> Result:
        self.halts = []                                  # (ts, symbol, halted?)
        for r in read_tape(path):
            ev = r["msg"].get("event")
            if ev in ("TRADING_HALT", "TRADING_RESUMED"):
                self.halts.append((float(r["ts"]), r["msg"]["data"]["symbol"], ev == "TRADING_HALT"))
        self.blocked = 0
        return super().run(path, name)

    def is_halted(self, symbol: str) -> bool:
        state = False
        for ts, sym, halted in self.halts:
            if ts > self.ts:
                break
            if sym == symbol:
                state = halted
        return state

    def _execute(self, sig):
        if self.is_halted(sig.symbol):
            self.blocked += 1
            return None
        if hasattr(self.bot, "on_sent"):
            self.bot.on_sent(sig)
            self.bot.on_answer(sig.symbol)
        return super()._execute(sig)


def read_tape(path: pathlib.Path):
    with open(path) as fh:
        for line in fh:
            if '"book_snapshot"' in line or '"session_event"' in line:
                yield json.loads(line)


def new_bot(strategy: str, delay: int = 0):
    logging.disable(logging.CRITICAL)
    if strategy == "hold":
        return DoNothing()
    import team.trader as tt
    bot = tt.STRATEGIES[strategy]()
    if delay:
        bot.SIGNAL_DELAY = delay
    return bot


def backtest(strategy: str, path: pathlib.Path, delay: int = 0) -> tuple[Result, int]:
    bt = Phase2Backtester(new_bot(strategy, delay), cash=CASH, fill="sweep", tick=0.1, cap=SEAT_CAP,
                          seed_shares=SEED_SHARES, latency=LATENCY_S, fee=FEE)
    return bt.run(path, strategy), bt.blocked


def run_one(job: dict) -> dict:
    """job = {"tape", "strategy", "delay"} → excess vs hold, fees, drawdown, round trips."""
    path = ROOT / TAPES[job["tape"]]
    bot, blocked = backtest(job["strategy"], path, job.get("delay", 0))
    hold, _ = backtest("hold", path)
    excess = [(t, (v - bot.start_nw) - (h - hold.start_nw)) for (t, v), (_, h) in zip(bot.marks, hold.marks)]
    peak = dd = 0.0
    for _, x in excess:
        peak, dd = max(peak, x), max(dd, peak - x)
    fills = [(f.side, f.symbol, f.quantity, f.price) for f in bot.fills]
    t0 = excess[0][0]
    return {**job, "excess": bot.pnl - hold.pnl, "fees": bot.fees, "fills": len(bot.fills),
            "max_dd": dd, "blocked": blocked, "trips": round_trips(fills, FEE),
            "curve": [(round(t - t0, 1), round(x, 2)) for t, x in excess[::50] + excess[-1:]]}


def main() -> int:
    jobs = [{"tape": t, "strategy": s} for s in ("momentum", "event") for t in TAPES]
    jobs += [{"tape": t, "strategy": "momentum", "delay": d} for d in (1, 2, 5) for t in TAPES]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(run_one, jobs))

    lines = ["# team/trader.py on the recorded sessions (generated by scripts/phase2_backtest.py)", "",
             f"${CASH:,.0f} · seat cap {SEAT_CAP} · {SEED_SHARES} granted shares/symbol · "
             f"taker fee {FEE:.2%} on every fill · latency {LATENCY_S} s · fills walk the recorded depth", "",
             "excess = bot P&L − P&L of holding the granted shares (DoNothing), same tape", "",
             "| tape | strategy | excess | excess before fees | fees | hit rate (round trips) "
             "| worst excess DD | fills | blocked by halt |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        if r.get("delay"):
            continue
        trips = r["trips"]          # buy → sell pairs; the event strategy also sells first, so n/a there
        hit = (f"{sum(t > 0 for t in trips) / len(trips):.0%} ({len(trips)})" if trips else "— (0)") \
            if r["strategy"] == "momentum" else "n/a"
        lines.append(f"| {r['tape']} | {r['strategy']} | {r['excess']:+,.0f} | {r['excess'] + r['fees']:+,.0f} "
                     f"| {r['fees']:,.0f} | {hit} | {r['max_dd']:,.0f} | {r['fills']} | {r['blocked']} |")
    lines += ["", "## Momentum look-ahead check (feature read k snapshots late, mean of weeks 3–6)", "",
              "| delay | mean excess | mean excess before fees | fills |", "|---|---:|---:|---:|"]
    for d in (0, 1, 2, 5):
        rs = [r for r in rows if r["strategy"] == "momentum" and r.get("delay", 0) == d]
        lines.append(f"| {d} | {st.mean(r['excess'] for r in rs):+,.0f} "
                     f"| {st.mean(r['excess'] + r['fees'] for r in rs):+,.0f} | {st.mean(r['fills'] for r in rs):.0f} |")
    text = "\n".join(lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "recorded.md").write_text(text)
    (OUT / "recorded_curves.json").write_text(json.dumps(
        [{k: r.get(k) for k in ("tape", "strategy", "delay", "curve")} for r in rows]))
    (ROOT / "evidence" / "phase2_backtest.txt").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
