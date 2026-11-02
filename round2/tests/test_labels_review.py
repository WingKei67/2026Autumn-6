from round2.bootstrap import ROOT
import numpy as np
from round2.labels_review import labels_with_halts

def prices():
    a=np.zeros((6,6));a[:,0]=np.arange(6);a[:,2:5]=100.
    return a

def test_intermediate_halt_does_not_select_out_a_loss():
    a=prices();a[2,5]=1;a[3,2:5]=80.
    _,long,_,stats=labels_with_halts(a,3)
    assert long[0]<-.2 and stats['delayed_exits']==0

def test_exit_waits_for_resume_and_charges_both_fees():
    a=prices();a[3:5,5]=1;a[5,2:5]=75.
    _,long,_,stats=labels_with_halts(a,3)
    assert abs(long[0]-(75*.997/(100*1.003)-1))<1e-12
    assert stats['delayed_exits']>=1

def test_rejected_arrival_is_zero_not_dropped():
    a=prices();a[1,5]=1
    _,long,_,stats=labels_with_halts(a,3)
    assert long[0]==0 and stats['entry_rejected']==1

def test_terminal_halt_is_explicitly_censored():
    a=prices();a[3:,5]=1
    _,long,_,stats=labels_with_halts(a,3)
    assert np.isnan(long[0]) and stats['terminal_censored']>0
