"""
The Phase 2 backtest scripts: the properties the BACKTEST.md numbers rely on.

- the recorded-tape backtester never fills an order in a symbol the venue has halted
- round trips are FIFO per symbol and pay the fee on both legs
- a SimSession run is reproducible per seed, and different seeds give different prices
"""

from __future__ import annotations

import json
import pathlib

import pytest

import scripts.phase2_backtest as pb
import scripts.phase2_sim as ps
from shared.messages import Signal


class _Buy:
    """A bot that buys 1 share of AAPL on every tick."""

    def on_tick(self, market, portfolio):
        return Signal(symbol="AAPL", side="buy", quantity=1, price=1e6)

    def on_event(self, *a):
        pass

    def on_fill(self, *a):
        pass


def _tape(tmp_path: pathlib.Path) -> pathlib.Path:
    snap = {"type": "book_snapshot", "symbol": "AAPL", "bids": [[99.0, 100.0]], "asks": [[101.0, 100.0]],
            "mid_price": 100.0, "spread": 2.0}
    rows = [{"ts": 0.0, "msg": {"type": "session_event", "event": "SESSION_OPEN", "data": {}}}]
    rows += [{"ts": t / 2, "msg": snap} for t in range(1, 21)]                       # 0.5 … 10 s
    rows.insert(8, {"ts": 3.6, "msg": {"type": "session_event", "event": "TRADING_HALT", "data": {"symbol": "AAPL"}}})
    rows.insert(16, {"ts": 7.6, "msg": {"type": "session_event", "event": "TRADING_RESUMED", "data": {"symbol": "AAPL"}}})
    rows.append({"ts": 11.0, "msg": {"type": "session_event", "event": "SESSION_CLOSED", "data": {}}})
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_no_fill_while_halted(tmp_path):
    bt = pb.Phase2Backtester(_Buy(), cash=1e6, tick=0.1, cap=1000)
    res = bt.run(_tape(tmp_path))
    assert bt.blocked > 0
    assert not [f for f in res.fills if 3.6 <= f.ts < 7.6]
    assert [f for f in res.fills if f.ts < 3.6] and [f for f in res.fills if f.ts >= 7.6]


def test_round_trips_fifo_with_fees_on_both_legs():
    fills = [("buy", "X", 10, 100.0), ("buy", "X", 10, 102.0), ("sell", "X", 15, 104.0),
             ("buy", "Y", 5, 50.0), ("sell", "Y", 5, 49.0)]
    trips = ps.round_trips(fills, fee=0.001)
    # X: 10 @ 100 and 5 @ 102 out at 104;  Y: 5 @ 50 out at 49
    x = 10 * 4 + 5 * 2 - 0.001 * (10 * (104 + 100) + 5 * (104 + 102))
    y = -5 - 0.001 * 5 * (49 + 50)
    assert trips == [pytest.approx(x), pytest.approx(y)]


def test_simsession_seeds_are_reproducible_and_differ():
    a, b = ps.run(3, 30, 0.005), ps.run(3, 30, 0.005)
    assert a["curve"] == b["curve"] and a["pnl"] == b["pnl"]
    import plugins.securities.defaults as sec
    sec.FUNDAMENTAL_SEED = "phase2-3"
    p3 = sec.fundamental_innovation("AAPL", 10, 0.3)
    sec.FUNDAMENTAL_SEED = "phase2-4"
    assert sec.fundamental_innovation("AAPL", 10, 0.3) != p3      # the seed really moves prices
