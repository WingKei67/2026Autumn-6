"""Regression cases for cash, allocation, recovery gating and venue-tick reactions."""

import asyncio
import logging
from pathlib import Path

import pytest
import trader.config as cfg
from shared.messages import BookSnapshot, Leaderboard, SessionEvent, TradeExecution
from team.ledger import Ledger
from team.trader import MyTrader, _Bot
from week2.week02_alpha_bot import Plan


def make_bot(tmp_path: Path) -> _Bot:
    bot = _Bot(MyTrader())
    bot.ledger = Ledger(cash=10_000)
    bot.portfolio.cash = 10_000
    bot.portfolio.positions = {}
    bot._state_path = tmp_path / "state.json"
    return bot


def add_book(bot: _Bot, symbol: str) -> None:
    bot.market.update_book(BookSnapshot(
        symbol=symbol, bids=[[99.9, 100]], asks=[[100.1, 100]],
        mid_price=100, spread=0.2,
    ))


def test_fast_cash_patch_includes_fee(tmp_path):
    bot = make_bot(tmp_path)
    bot._on_trade(TradeExecution(
        trade_id="t", symbol="AAPL", price=100, quantity=4,
        buyer_id=cfg.TEAM_ID, seller_id="other", aggressor="buy",
        fee=1.2, taker_id=cfg.TEAM_ID,
    ))
    assert bot.portfolio.cash == pytest.approx(9_598.8)
    assert bot.ledger.cash == pytest.approx(bot.portfolio.cash)


def test_ipo_pricing_books_while_unrelated_order_inflight(tmp_path):
    bot = make_bot(tmp_path)
    bot._owner._inflight = {"AAPL": ["buy", 1, bot._owner.now()]}
    bot._ipo_pending = {"ORCA"}
    bot.portfolio.positions = {"ORCA": 111}
    bot.portfolio.cash = 6_892
    asyncio.run(bot._on_session_event(SessionEvent(
        event="IPO_PRICED", message="priced",
        data={"symbol": "ORCA", "offer_price": 28},
    )))
    assert bot.ledger.positions == {"ORCA": 111}
    assert bot.ledger.avg_entry == {"ORCA": 28}
    assert bot.ledger.cash == 6_892
    assert "ORCA" not in bot._ipo_pending
    bot._book_ipo_allocations()
    assert bot.ledger.cash == 6_892  # Allocation must not be applied twice.


def test_one_missing_entry_does_not_block_other_symbol(tmp_path):
    bot = make_bot(tmp_path)
    owner = bot._owner
    bot._entry_from_board = {"NVDA"}
    for symbol in ["AAPL", "NVDA"]:
        add_book(bot, symbol)
        owner.plans[symbol] = Plan(
            symbol, "ride", 100, 0.05, 1.3, owner.now(), 15,
        )
    signal = owner.on_tick(bot.market, bot.portfolio)
    assert signal is not None and signal.symbol == "AAPL"


def test_reaction_deadline_uses_venue_ticks(tmp_path):
    bot = make_bot(tmp_path)
    owner = bot._owner
    owner.now = lambda: 1_000_000.0
    owner._unanswered = [(100, {"AAPL"})]
    add_book(bot, "AAPL")
    owner._venue_tick = 119
    assert owner._react(bot.market, bot.portfolio) is None
    owner._venue_tick = 120
    assert owner._react(bot.market, bot.portfolio) is not None


def test_inferred_cash_is_explicit_and_totalled(tmp_path, caplog):
    bot = make_bot(tmp_path)
    row = {
        "team_id": cfg.TEAM_ID, "cash": 10_002, "positions": {},
        "avg_cost": {}, "net_worth": 10_002, "realized_pnl": 0,
        "unrealized_pnl": 0, "unrealized_by_sym": {},
    }
    bot._closing = True
    with caplog.at_level(logging.INFO):
        bot._on_leaderboard(Leaderboard(
            traders=[row], brokers=[], exchange_fees=0, tick=1,
        ))
    assert bot._inferred_cash_adjustments == 2
    assert "source=snapshot_residual" in caplog.text
    assert "CASH_AUDIT" in caplog.text
    assert "inferred_adjustments_this_run=+2.0000" in caplog.text
