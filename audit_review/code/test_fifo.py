from fifo import overlay_fifo

def fill(side,q,px,fee,account='a'):
    return dict(account=account,symbol='AAPL',side=side,quantity=q,price=px,fee=fee,rebate=0.)

def test_partial_closes_charge_both_entry_and_exit_fees():
    r=overlay_fifo([fill('buy',2,100.,.6),fill('sell',1,102.,.306),fill('sell',1,99.,.297)])
    assert r['count']==2 and r['wins']==1
    assert abs(r['gains']-1.394)<1e-10 and abs(r['losses']-1.597)<1e-10

def test_sale_and_buyback_of_grant_is_a_closed_overlay():
    r=overlay_fifo([fill('sell',5,100.,1.5),fill('buy',5,90.,1.35)])
    assert r['count']==1 and r['wins']==1 and abs(r['gains']-47.15)<1e-10

def test_two_seats_cannot_close_each_other():
    r=overlay_fifo([fill('buy',1,10.,0.,'a'),fill('sell',1,12.,0.,'b')])
    assert r['count']==0
