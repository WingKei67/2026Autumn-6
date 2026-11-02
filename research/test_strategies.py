"""Safety regression cases for research variants; never touches live state."""
import asyncio
import time
import pytest

from arena.broker import _AdaptedBrokerBot
from research.strategies import (
    StrategyConfig, SeasonTrader, SeasonTraderBot, SeasonBroker,
    SeasonBrokerBot, make_candidate,
)
from shared.messages import Signal, PlaceOrder, SessionEvent, BookSnapshot, ErrorMsg
import team.trader as trader_module
import team.broker as broker_module


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_module, "STATE_DIR", tmp_path)


def trader_ready(config=None):
    owner = SeasonTrader(config); bot = SeasonTraderBot(owner)
    owner._week5_active = True
    owner._refs["AAPL"] = 100
    bot._session_open = bot._has_server_snapshot = True
    bot._snapshot_at = time.monotonic()
    bot.portfolio.cash = bot.portfolio._net_worth = 175_000
    bot.market.update_book(BookSnapshot(symbol="AAPL",bids=[[99.9,100]],asks=[[100.1,100]],mid_price=100,spread=.2))
    return owner, bot


def test_session_open_activates_future_weeks_without_open_play():
    owner, bot = trader_ready()
    for week in (0, 4, 5, 6, 7, 8, 9, 10, 11):
        asyncio.run(bot._on_session_event(SessionEvent(event="SESSION_OPEN",message="",data={"week":week,"scenario":{"flags":{"shorts_allowed":True}}})))
        assert owner._week5_active == (5 <= week <= 10)
        assert owner._shorts_allowed


def test_gate_fix_preserves_closed_stale_and_inflight(monkeypatch):
    owner, bot = trader_ready()
    sent=[]
    async def capture(msg): sent.append(msg)
    monkeypatch.setattr(bot,"_send",capture)
    sig=Signal(symbol="AAPL",side="buy",quantity=10,price=100)
    for attr,value in (("_session_open",False),("_has_server_snapshot",False),("_resync",True),("_snapshot_at",time.monotonic()-11)):
        old=getattr(bot,attr);setattr(bot,attr,value)
        asyncio.run(bot._place_order(sig));setattr(bot,attr,old)
    assert not sent
    asyncio.run(bot._place_order(sig))
    asyncio.run(bot._place_order(sig))
    assert len(sent)==1
    assert owner._inflight["AAPL"][1]==10


def test_short_requires_public_permission_and_ssr_clear(monkeypatch):
    owner,bot=trader_ready(StrategyConfig(enable_short=True))
    sent=[]
    async def capture(msg): sent.append(msg)
    monkeypatch.setattr(bot,"_send",capture)
    sig=Signal(symbol="AAPL",side="sell",quantity=10,price=100)
    asyncio.run(bot._place_order(sig));assert not sent
    owner._shorts_allowed=True;owner._ssr.add("AAPL")
    asyncio.run(bot._place_order(sig));assert not sent
    owner._ssr.clear();asyncio.run(bot._place_order(sig))
    assert len(sent)==1 and sent[0].side=="sell"


def test_larger_exposure_remains_capped(monkeypatch):
    owner,bot=trader_ready(StrategyConfig(exposure=2))
    sent=[]
    async def capture(msg): sent.append(msg)
    monkeypatch.setattr(bot,"_send",capture)
    asyncio.run(bot._place_order(Signal(symbol="AAPL",side="buy",quantity=10000,price=100)))
    assert sent[0].quantity == 300


def broker_ready(config):
    bot=SeasonBrokerBot(SeasonBroker(config))
    bot._graded_week5=bot._continuous=bot._has_snapshot=bot._post_only_allowed=True
    bot._snapshot_at=time.monotonic()
    bot.state.cash=350_000;bot.state.exchange_prices["AAPL"]=100
    return bot


def test_passive_is_resting_not_ioc_and_stays_reserved(monkeypatch):
    bot=broker_ready(StrategyConfig(passive=True))
    sent=[]
    async def capture(self,msg):sent.append(msg)
    monkeypatch.setattr(_AdaptedBrokerBot,"_send",capture)
    asyncio.run(bot.quote_symbol("AAPL"))
    assert len(sent)==1 and sent[0].order_type=="post_only"
    assert bot._orders["AAPL","buy"][3] is False
    asyncio.run(bot.quote_symbol("AAPL"))
    assert len(sent)==1  # no cancel/reprice churn within minimum resting time
    assert bot._orders["AAPL","buy"][0]==2


def test_broker_precclose_blocks_new_quotes(monkeypatch):
    bot=broker_ready(StrategyConfig(passive=True))
    sent=[]
    async def capture(self,msg):sent.append(msg)
    monkeypatch.setattr(_AdaptedBrokerBot,"_send",capture)
    asyncio.run(bot._on_session_event(SessionEvent(event="SESSION_PRECLOSE",message="",data={})))
    asyncio.run(bot.quote_symbol("AAPL"))
    assert not sent and not bot._continuous


def test_passive_requires_public_flag(monkeypatch):
    bot=broker_ready(StrategyConfig(passive=True));bot._post_only_allowed=False
    sent=[]
    async def capture(self,msg):sent.append(msg)
    monkeypatch.setattr(_AdaptedBrokerBot,"_send",capture)
    asyncio.run(bot.quote_symbol("AAPL"))
    assert not sent and not bot._orders


def test_fill_duplicate_does_not_double_book():
    from shared.messages import TradeExecution
    bot=broker_ready(StrategyConfig(passive=True))
    msg=TradeExecution(trade_id="x",symbol="AAPL",quantity=2,price=100,
       buyer_id=broker_module.broker_config.TEAM_ID,seller_id="other",aggressor="sell",fee=1,maker_rebate=.1,maker_id=broker_module.broker_config.TEAM_ID,taker_id="other")
    bot._on_trade(msg);bot._on_trade(msg)
    assert bot.state.positions["AAPL"]==2
    assert bot.state.cash==pytest.approx(349800.1)


def test_factory_preserves_baseline_class():
    owner,bot=make_candidate("baseline_trader")
    assert type(owner) is trader_module.MyTrader and type(bot) is trader_module._Bot


def test_short_reconnect_does_not_assume_missed_ssr_absent():
    owner,bot=trader_ready(StrategyConfig(enable_short=True))
    asyncio.run(bot._on_session_event(SessionEvent(event="SESSION_OPEN",message="",data={"week":6,"scenario":{"flags":{"shorts_allowed":True}}})))
    assert owner._shorts_allowed
    owner.on_reconnect()
    asyncio.run(bot._on_session_event(SessionEvent(event="SESSION_OPEN",message="",data={"week":6,"late_join":True,"scenario":{"flags":{"shorts_allowed":True}}})))
    assert not owner._shorts_allowed


def test_negative_event_does_not_label_existing_long_as_short():
    owner,bot=trader_ready(StrategyConfig(enable_short=True))
    owner._shorts_allowed=True;owner._mids["AAPL"]=100
    bot.portfolio.positions["AAPL"]=10
    owner.on_event("CALENDAR_EVENT","",{"affected":["AAPL"],"pct":-.05,"overshoot":1.3,"ramp_ticks":15})
    assert owner.plans["AAPL"].mode=="fade"
