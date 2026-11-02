"""All P&L comes from actual exchange portfolios; cost diagnostics are not subtracted twice."""
import math
from collections import defaultdict, deque
from statistics import mean, stdev


def summarize(server,accounts,receivers,gateways,curve,initial,starting_positions,starting_marks):
    fills=[f for r in receivers for f in r.fills]
    commissions=sum(f['fee'] for f in fills); rebates=sum(f['rebate'] for f in fills)
    turnover=sum(f['price']*f['quantity'] for f in fills)
    slippage=sum((1 if f['side']=='buy' else -1)*(f['price']-f['decision_mid'])*f['quantity']
                 for f in fills if f['decision_mid'])
    peak=initial; drawdown=0.
    for x in curve:peak=max(peak,x);drawdown=max(drawdown,(peak-x)/peak)
    returns=[b/a-1 for a,b in zip([initial]+curve[:-1],curve) if a>0]
    vol=stdev(returns) if len(returns)>1 else 0
    sharpe=mean(returns)/vol*math.sqrt(len(returns)) if vol else 0.
    # FIFO closed-fill P&L, including both fills' fees/rebates. Not an order win rate.
    lots=defaultdict(deque); closed=[]
    for sym,qty in starting_positions.items():
        if qty:lots[sym].append([qty,starting_marks[sym],0.])
    for f in sorted(fills,key=lambda f:f['tick']):
        signed=f['quantity']*(1 if f['side']=='buy' else -1)
        cost=(f['fee']-f['rebate'])/f['quantity']; sym=f['symbol']; close_pnl=0.; did_close=False
        while signed and lots[sym] and signed*lots[sym][0][0]<0:
            old,price,oldcost=lots[sym][0]; qty=min(abs(signed),abs(old))
            close_pnl+=qty*((f['price']-price)*(1 if old>0 else -1)-oldcost-cost)
            lots[sym][0][0]-=qty*(1 if old>0 else -1)
            signed-=qty*(1 if signed>0 else -1);did_close=True
            if not lots[sym][0][0]:lots[sym].popleft()
        if signed:lots[sym].append([signed,f['price'],cost])
        if did_close:closed.append(close_pnl)
    errors=defaultdict(int)
    for r in receivers:
        for code,n in r.errors.items():errors[code]+=n
    return {'pnl':curve[-1]-initial if curve else 0.,'initial_equity':initial,
        'final_equity':curve[-1] if curve else initial,'commissions':commissions,'maker_rebates':rebates,
        'net_fees':commissions-rebates,'slippage_vs_decision_mid':slippage,
        'session_sharpe':sharpe,'max_drawdown':drawdown,'turnover':turnover/initial,
        'traded_notional':turnover,'fills':len(fills),'maker_fills':sum(f['maker'] for f in fills),
        'closed_fill_win_rate':sum(x>0 for x in closed)/len(closed) if closed else None,
        'closed_fill_count':len(closed),'liquidations':sum(server.portfolios[a].liquidated for a in accounts),
        'position_violations':sum(g.position_violations for g in gateways),
        'max_abs_position':max((g.peak_position for g in gateways),default=0),
        'max_gross':max((g.peak_gross for g in gateways),default=0),
        'hedge_failures':sum(g.hedge_failures for g in gateways),'rejects':dict(errors),
        'carry_paid':sum(server.portfolios[a].total_carry_paid for a in accounts),
        'same_tick_fills':sum(f['tick']<=f['decision_tick'] for f in fills if f['decision_tick'] is not None)}
