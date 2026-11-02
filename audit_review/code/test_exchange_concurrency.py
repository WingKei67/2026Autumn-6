"""Expose matching-before-ACK settlement race using actual exchange handlers."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_study import runner,cfg
from alpha_lab.exchange import OfflineExchange
from exchange.server import Portfolio
from shared.messages import PlaceOrder,OrderAck,ErrorMsg

def run_race(exchange_class):
    async def scenario():
        with runner.isolated(71,5,175000.,1) as (_,scenario,accounts):
            cfg.LEVERAGE_ENABLED=False
            s=exchange_class();s.set_scenario(scenario);s.session_open=True;s.auction_phase=None
            sym='AAPL';px=s.ref_prices[sym]
            for name in ['alpha_0','inactive_0']:
                s.portfolios[name]=Portfolio(team_id=name,role='trader',level=1,cash=175000.)
            for name in ['provider_a','provider_b']:
                s.portfolios[name]=Portfolio(team_id=name,role='broker',level=1,cash=1e6)
                s.portfolios[name].positions[sym]=500
                s.books[sym].place_order(team_id=name,side='sell',price=px,quantity=500,order_type='limit')
            blocked=asyncio.Event();release=asyncio.Event();messages=[]
            async def send(ws,msg):
                messages.append(msg)
                if ws=='alpha_0' and isinstance(msg,OrderAck):
                    blocked.set();await release.wait()

            if exchange_class is OfflineExchange:s._send=send
            else:s._deliver_send=send
            def order(name):return PlaceOrder(team_id=name,symbol=sym,side='buy',quantity=500,price=px,order_type='ioc')
            first=asyncio.create_task(s._handle_place_order('alpha_0',order('alpha_0'),'alpha_0'))
            await asyncio.wait_for(blocked.wait(),2)
            await s._handle_place_order('inactive_0',order('inactive_0'),'inactive_0')
            release.set();await first
            total=sum(s.portfolios[n].positions.get(sym,0) for n in ['alpha_0','inactive_0'])
            return total,[m.code for m in messages if isinstance(m,ErrorMsg)]
    return asyncio.run(scenario())


def test_original_matching_before_ack_exceeds_team_limit():
    total,errors=run_race(OfflineExchange)
    assert total==1000 and not errors


def test_buffered_settlement_prevents_team_race():
    from atomic_exchange import BufferedSettlementExchange
    total,errors=run_race(BufferedSettlementExchange)
    assert total==500 and errors==['POSITION_LIMIT']

def test_held_moc_orders_are_not_reserved_by_original_venue():
    async def scenario():
        with runner.isolated(72,9,175000.,1) as (_,sc,accounts):
            cfg.LEVERAGE_ENABLED=False
            s=OfflineExchange();s.set_scenario(sc);s.session_open=True;s.auction_phase=None
            sym='AAPL';px=s.ref_prices[sym];name='alpha_0'
            s.portfolios[name]=Portfolio(team_id=name,role='trader',level=1,cash=1e6)
            for provider in ['provider_a','provider_b','provider_c']:
                s.portfolios[provider]=Portfolio(team_id=provider,role='broker',level=1,cash=1e6)
                s.portfolios[provider].positions[sym]=500
                s.books[sym].place_order(team_id=provider,side='sell',price=px,quantity=500,order_type='limit')
            for _ in range(3):
                await s._handle_place_order(name,PlaceOrder(team_id=name,symbol=sym,side='buy',quantity=500,price=0.,order_type='moc'),name)
            assert len(s.moc_orders[sym])==3
            await s.close_session(persist=False)
            await s._run_closing_cross()
            assert s.portfolios[name].positions[sym]==1500>sc.position_limit
    asyncio.run(scenario())
