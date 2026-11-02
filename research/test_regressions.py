"""Regression tests for defects found during the Claude continuation (see PROGRESS.md)."""
import asyncio, pytest
import research.harness  # sets sys.path / env  # noqa: F401
from research.strategies import NON_SHARE_SYMBOLS
from research.harness import run_one


def test_futures_excluded_from_share_only_bots():
    assert 'ARENA10' in NON_SHARE_SYMBOLS


@pytest.mark.parametrize('name,seed,week', [('short_trader', 20, 10), ('aggressive_trader', 14, 9)])
def test_no_accounting_errors_when_futures_enabled(name, seed, week):
    # Before the guard, an econ-print plan sold ARENA10 as if it were a share
    # and the ledger cash diverged from the exchange by ~$7-8k.
    r = asyncio.run(run_one(name, seed, week, 1500))
    assert r['accounting_errors'] == []
    assert not any(o['symbol'] == 'ARENA10' for o in r['orders_detail'])


def test_broker_zero_position_entries_are_not_a_mismatch():
    r = asyncio.run(run_one('aggressive_broker', 22, 7, 1500))
    assert r['accounting_errors'] == []


def test_baseline_broker_is_inert_after_week5():
    """Characterization: `_graded_week5` is week==5 only, so the deployed broker
    never quotes in weeks 6-10 and equals the cash control exactly."""
    r = asyncio.run(run_one('baseline_broker', 11, 6, 400))
    assert r['orders'] == 0 and r['fills'] == 0


def test_official_futures_identity_in_harness_week9():
    asyncio.run(run_one('cash_trader', 14, 9, 400))  # harness assertion would raise
