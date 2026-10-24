"""Our books must equal the exchange's books for the same fills."""

import random

import pytest

from exchange.server import Portfolio as ExchangePortfolio
from shared.messages import PortfolioUpdate
from team.ledger import Ledger


def test_adds_average_the_entry():
    L = Ledger(cash=10_000)
    L.apply_fill("buy", "X", 10, 100)
    L.apply_fill("buy", "X", 30, 104)
    assert L.positions["X"] == 40
    assert L.avg_entry["X"] == pytest.approx(103.0)
    assert L.cash == pytest.approx(10_000 - 1_000 - 3_120)


def test_partial_close_realizes_against_average():
    L = Ledger()
    L.apply_fill("buy", "X", 40, 103)
    L.apply_fill("sell", "X", 10, 110)
    assert L.realized_pnl == pytest.approx(70)
    assert L.positions["X"] == 30 and L.avg_entry["X"] == pytest.approx(103)


def test_flip_reopens_at_fill_price():
    L = Ledger()
    L.apply_fill("buy", "X", 10, 100)
    L.apply_fill("sell", "X", 25, 90)
    assert L.realized_pnl == pytest.approx(-100)
    assert L.positions["X"] == -15 and L.avg_entry["X"] == 90


def test_unrealized_marks_to_ref_price():
    L = Ledger()
    L.apply_fill("buy", "X", 10, 100)
    assert L.unrealized_pnl({"X": 105}) == pytest.approx(50)


def test_ipo_allocation_booked_at_offer():
    L = Ledger(cash=10_000)
    L.apply_allocation("ORCA", 100, 26.0)
    assert L.positions["ORCA"] == 100 and L.avg_entry["ORCA"] == 26.0
    assert L.cash == pytest.approx(7_400)


@pytest.mark.parametrize("seed", range(20))
def test_matches_exchange_math_on_random_fills(seed):
    rng = random.Random(seed)
    ours = Ledger(cash=100_000)
    theirs = ExchangePortfolio(team_id="t", role="trader", level=1, cash=100_000)
    for _ in range(200):
        side, qty = rng.choice(["buy", "sell"]), rng.randint(1, 50)
        px = round(rng.uniform(90, 110), 2)
        fee = round(px * qty * 0.0005, 4)
        ours.apply_fill(side, "X", qty, px, fee)
        (theirs.apply_buy if side == "buy" else theirs.apply_sell)("X", px, qty, fee)
    assert ours.positions.get("X", 0) == theirs.positions.get("X", 0)
    assert ours.cash == pytest.approx(theirs.cash)
    assert ours.realized_pnl == pytest.approx(theirs.realized_pnl)
    if ours.positions:
        assert ours.avg_entry["X"] == pytest.approx(theirs.avg_cost["X"])


def test_reconcile_flags_a_mismatch():
    L = Ledger(cash=1_000, positions={"X": 5}, avg_entry={"X": 10.0})
    assert L.reconcile(1_000, {"X": 6}, 0.0) == ["X: ours 5 vs exchange 6"]


def test_reconcile_checks_entries_when_given():
    L = Ledger(cash=1_000, positions={"X": 5}, avg_entry={"X": 10.0})
    assert L.reconcile(1_000, {"X": 5}, 0.0, {"X": 10.004}) == []
    assert L.reconcile(1_000, {"X": 5}, 0.0, {"X": 10.5}) == [
        "X entry: ours 10.0000 vs exchange 10.5000"]


def test_sync_without_entries_keeps_same_side_entry():
    """PortfolioUpdate path: no avg_cost from the exchange."""
    L = Ledger(cash=900, positions={"X": 5}, avg_entry={"X": 10.0})
    diffs = L.sync_from(1_000, {"X": 8, "Y": 3}, 0.0, {"X": 11.0, "Y": 20.0})
    assert diffs                                    # the drop cost us fills: reported
    assert L.positions == {"X": 8, "Y": 3} and L.cash == 1_000
    assert L.avg_entry == {"X": 10.0, "Y": 20.0}    # X kept, Y seeded at the mark


def test_sync_from_leaderboard_row_takes_exchange_entries():
    """Leaderboard path: the exchange's avg_cost wins over anything we had."""
    L = Ledger()                                    # a fresh process: empty books
    L.sync_from(210_000.0, {"NVDA": 40}, -4_109.98, {"NVDA": 160.0}, {"NVDA": 162.43})
    assert L.positions == {"NVDA": 40} and L.avg_entry == {"NVDA": 162.43}
    assert L.realized_pnl == -4_109.98
    assert L.reconcile(210_000.0, {"NVDA": 40}, -4_109.98, {"NVDA": 162.43}) == []


def test_portfolio_update_fields_feed_reconcile():
    upd = PortfolioUpdate(team_id="t", cash=1_000, positions={"X": 5}, realized_pnl=0,
                          unrealized_pnl=0, total_fees_paid=0, net_worth=1_050)
    L = Ledger(cash=1_000, positions={"X": 5}, avg_entry={"X": 10.0})
    assert L.reconcile(upd.cash, upd.positions, upd.realized_pnl) == []


def test_save_and_load_round_trip(tmp_path):
    L = Ledger(cash=5.0, positions={"X": 2}, avg_entry={"X": 3.0}, realized_pnl=1.0)
    L.save(tmp_path / "s.json")
    assert Ledger.load(tmp_path / "s.json") == L
    assert Ledger.load(tmp_path / "missing.json") == Ledger()


def test_grant_new_position_enters_at_mark_existing_keeps_entry():
    """Starting shares: same rule as the exchange's _grant_starting_shares."""
    L = Ledger(positions={"X": 10}, avg_entry={"X": 50.0})
    L.apply_grant("X", 20, 55.0)
    L.apply_grant("Y", 20, 30.0)
    assert L.positions == {"X": 30, "Y": 20}
    assert L.avg_entry == {"X": 50.0, "Y": 30.0}
    assert L.cash == 0.0                            # free shares: no cash moves


def test_accruals_move_cash_and_are_reported():
    L = Ledger(cash=100_000, positions={"X": 10})
    L.accrue(0.40)                                  # interest on idle cash
    L.apply_dividend("X", 0.50)
    assert L.cash == pytest.approx(100_005.40)
    assert L.accruals == pytest.approx(5.40)


def test_reconcile_checks_fees_when_given():
    L = Ledger(cash=1_000)
    L.apply_fill("buy", "X", 10, 10.0, fee=3.00)
    assert L.reconcile(L.cash, {"X": 10}, 0.0, fees=3.00) == []
    assert L.reconcile(L.cash, {"X": 10}, 0.0, fees=1.50) == ["fees: ours 3.00 vs exchange 1.50"]
