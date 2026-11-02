from types import SimpleNamespace as NS
from collections import defaultdict
import asyncio
from audit_study import CorrectedGateway,BASE_GATEWAY
from alpha_lab.exchange import Receiver,Reservation
from team.alpha.core import Intent,Observation,Quote

class Portfolio:
    liquidated=False;cash=0.;positions={'ARENA10':-1}
    def net_worth(self,*a):return 1000.
class Server:
    session_open=True;tick=1;ref_prices={'ARENA10':100.,'AAPL':100.}
    scenario=NS(flag=lambda x:True)
    circuit_breaker=NS(is_halted=lambda s:False)
    def __init__(self):
        self.portfolios={'x':Portfolio()};self.books={'ARENA10':NS(_orders={})};self.sent=[]
    def _bidask_marks(self):return {}
    async def _handle_place_order(self,*args):self.sent.append(args[1])

def test_original_reducing_flip_reproduced_and_fixed():
    s=Server();rx=Receiver('x');intent=Intent('ARENA10','buy',21,100.)
    assert BASE_GATEWAY(s,'x',rx,175000.).allowed_quantity(intent)==21
    assert CorrectedGateway(s,'x',rx,175000.).allowed_quantity(intent)==1

def test_wire_quantity_not_resized_at_arrival():
    s=Server();rx=Receiver('x');g=CorrectedGateway(s,'x',rx,175000.)
    e=Reservation('ARENA10','buy',4,100.,'ioc',decision_tick=0,due=1)
    rx.pending[('ARENA10','buy')]=e;g.queue=[(1,1,'place',e)]
    # Arrival safety state says zero. Already-sent wire remains 4; actual engine
    # is responsible for accepting/rejecting it rather than silently dropping.
    g.allowed_quantity=lambda *a,**k:0
    asyncio.run(g.execute_due(1));assert s.sent[0].quantity==4

def test_new_orders_still_blocked_on_observed_halt():
    s=Server();s.circuit_breaker=NS(is_halted=lambda sym:True)
    assert CorrectedGateway(s,'x',Receiver('x'),175000.).allowed_quantity(Intent('ARENA10','buy',1,100.))==0

def test_partial_hedge_rounds_zero_not_forced_one():
    s=Server();rx=Receiver('x');g=CorrectedGateway(s,'x',rx,175000.)
    e=Reservation('ARENA10','sell',1,100.,'ioc',group='g',due=1)
    rx.pending[('ARENA10','sell')]=e;g.queue=[(1,1,'place',e)]
    g.group_orders['g']='AAPL';g.group_first_requested['g']=10;rx.group_fills[('g','AAPL')]=1
    asyncio.run(g.execute_due(1));assert not s.sent;assert g.hedge_failures==1

def test_reused_group_gets_new_attempt_identifier():
    s=Server();g=CorrectedGateway(s,'x',Receiver('x'),175000.);captured=[]
    original=BASE_GATEWAY.schedule
    try:
        BASE_GATEWAY.schedule=lambda self,items,obs:captured.extend(items)
        for tick in [1,2]:g.schedule([Intent('AAPL','buy',1,100.,group='exit')],NS(tick=tick))
    finally:BASE_GATEWAY.schedule=original
    assert captured[0].group!=captured[1].group

def metric_fixture():
    def fill(side,price):
        return dict(tick=1,symbol='AAPL',side=side,quantity=1,price=price,fee=0.,rebate=0.,maker=False,decision_tick=0,decision_mid=10.,group='')
    rx1=Receiver('a');rx2=Receiver('b');rx1.fills=[fill('buy',10.)];rx2.fills=[fill('sell',12.)]
    def p(q):return NS(positions={'AAPL':q},liquidated=False,total_fees_paid=0.,total_rebates_earned=0.,total_carry_paid=0.)
    s=NS(portfolios={'a':p(1),'b':p(-1)})
    g=NS(position_violations=0,peak_position=1,peak_gross=12.,hedge_failures=0)
    return s,[rx1,rx2],[g,g]

def test_fifo_does_not_close_across_accounts():
    from audit_study import summarize,BASE_SUMMARY
    s,rx,gs=metric_fixture()
    args=(s,['a','b'],rx,gs,[100.],100.,{}, {'AAPL':10.})
    assert BASE_SUMMARY(*args)['closed_fill_count']==1
    assert summarize(*args)['fifo_closed_fills']==0

def test_fee_accounting_includes_cancellation_and_rebate():
    from audit_study import summarize
    s,rx,gs=metric_fixture()
    s.portfolios['a'].total_fees_paid=1.5;s.portfolios['a'].total_rebates_earned=.1
    rx[0].fills[0].update(fee=1.,rebate=.1)
    r=summarize(s,['a','b'],rx,gs,[98.],100.,{}, {'AAPL':10.})
    assert abs(r['total_net_fees']-1.4)<1e-12
    assert abs(r['cancellation_fees_paid']-.5)<1e-12
    assert abs(r['gross_pnl_before_execution_fees']-(-.6))<1e-12

def test_missing_fill_provenance_is_counted():
    from audit_study import summarize
    s,rx,gs=metric_fixture();rx[0].fills[0]['decision_tick']=None
    r=summarize(s,['a','b'],rx,gs,[100.],100.,{}, {'AAPL':10.})
    assert r['missing_fill_provenance']==1
    assert r['same_tick_fills']==0  # Shows why zero alone is inadequate.
