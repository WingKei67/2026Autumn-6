from round2.bootstrap import ROOT
import numpy as np
from types import SimpleNamespace as NS
from round2.features import FeatureStore,FullObservation,FullEvent,FEATURES,labels
from round2.models import I,design
from round2.strategies import EventRegime,AdaptiveAllocator
from team.alpha.core import Quote

def obs(t,price=100.,events=(),halted=frozenset(),pos=6):
    return FullObservation(t,{'AAPL':Quote(price-.05,price+.05,10,10)},{'AAPL':pos},175000.,192000.,175000.,halted,False,True,events)
def model(family,value=.03):
    n=design(np.zeros(len(FEATURES)),family).shape[-1]
    return dict(family=family,horizon=20,mean=[0.]*n,scale=[1.]*n,beta=[value]+[0.]*n,train_rmse=0.,supported_proxy=True)
def test_lag_features_and_publication_time():
    f=FeatureStore()
    for t in range(1,10):f.update(obs(t,100.+t))
    assert f.current['AAPL'][I['ann_remaining']]==0.
    event=FullEvent('CALENDAR_EVENT',10,('AAPL',),'earnings',.1,15,1.3)
    v=f.update(obs(10,110.,(event,)))['AAPL'].copy()
    assert abs(v[I['r3']]-(110/107-1))<1e-12
    assert abs(v[I['ann_remaining']]-(109*1.1/110-1))<1e-12
    f.update(obs(11,100000.))
    assert abs(v[I['ann_remaining']]-(109*1.1/110-1))<1e-12

def test_labels_enter_on_later_tick_and_include_both_fees():
    a=np.zeros((4,6+len(FEATURES)));a[:,0]=np.arange(4);a[:,2]=[90,100,104,105];a[:,3]=[89,99,103,104];a[:,4]=[91,101,105,106]
    _,long,_=labels(a,3)
    assert abs(long[0]-(104*.997/(101*1.003)-1))<1e-12
    a[2,5]=1;assert np.isnan(labels(a,3)[1][0])

def test_halt_blocks_new_orders():
    p=EventRegime(models={'event_regime':model('event_regime')})
    e=FullEvent('CALENDAR_EVENT',10,('AAPL',),'earnings',.1)
    assert p.observe(obs(10,events=(e,),halted=frozenset({'AAPL'})))==[]

def test_requests_are_ioc_and_capped_and_volatility_reduces_size():
    amounts=[]
    for vol in [.001,.02]:
        p=EventRegime(models={'event_regime':model('event_regime')})
        p.features.update=lambda o: {'AAPL':np.array([vol if n=='vol30' else .1 if n=='pct' else 1. if n=='age' else 0. for n in FEATURES])}
        orders=p.observe(obs(10));amounts.append(orders[0].quantity)
        assert all(o.order_type=='ioc' and 0<o.quantity<=60 for o in orders)
        assert p.plans['AAPL']['target']<=150
    assert amounts[1]<=amounts[0]

def test_actual_partial_fills_determine_entry_vwap_and_clock():
    p=EventRegime(models={'event_regime':model('event_regime')})
    p.plans['AAPL']=dict(entry_notional=0.,entry_qty=0,entry=0.,started=None,last_tick=9)
    for tick,px in [(10,101.),(11,103.)]:p.on_fill(NS(symbol='AAPL',buyer_id='a',quantity=2,price=px),'a',tick)
    assert p.plans['AAPL']['entry']==102. and p.plans['AAPL']['started']==10

def test_allocator_does_not_sum_correlated_forecasts():
    p=AdaptiveAllocator(models={'event_regime':model('event_regime',.02),'cross_asset':model('cross_asset',.04)},uncertainty=False)
    v=np.zeros(len(FEATURES));v[I['age']]=3;v[I['pct']]=.1;v[I['peer3']]=.01
    assert abs(p.forecasts(v)[0]-.03)<1e-12

def test_unsupported_family_cannot_manufacture_trade():
    from round2.strategies import OvershootResearch
    m=model('overshoot_reversal');m['supported_proxy']=False
    p=OvershootResearch(models={'overshoot_reversal':m});v=np.zeros(len(FEATURES));v[I['age']]=20;v[I['pct']]=-.1;v[I['ann_remaining']]=.05
    assert p.forecasts(v) is None
