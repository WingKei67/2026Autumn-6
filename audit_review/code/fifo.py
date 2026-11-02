"""Per-account overlay FIFO: start at zero change from endowed inventory.

A sale of starting stock opens a virtual short OVERLAY, not a legal stock
short. A later buyback closes that overlay. Avoids assigning arbitrary initial
reference prices to grants when measuring trading round trips.
"""
from collections import defaultdict,deque

def overlay_fifo(fills):
    lots=defaultdict(deque);closed=[]
    for f in fills:
        key=(f['account'],f['symbol']);q=f['quantity'];signed=q*(1 if f['side']=='buy' else -1)
        cost=(f['fee']-f['rebate'])/q;pnl=0.;matched=False
        while signed and lots[key] and signed*lots[key][0][0]<0:
            old=lots[key][0];direction=1 if old[0]>0 else -1;n=min(abs(signed),abs(old[0]))
            pnl+=n*((f['price']-old[1])*direction-old[2]-cost)
            old[0]-=n*direction;signed+=n*direction;matched=True
            if not old[0]:lots[key].popleft()
        if signed:lots[key].append([signed,f['price'],cost])
        if matched:closed.append(pnl)
    return dict(gains=sum(x for x in closed if x>0),losses=-sum(x for x in closed if x<0),count=len(closed),wins=sum(x>0 for x in closed))
