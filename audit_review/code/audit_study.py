"""Independent offline reproduction and frozen fresh-seed audit; originals untouched."""
import sys, os, json, asyncio, argparse, hashlib, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict, deque
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/'audit_review/extracted/2026Autumn-6-fresh'
sys.path.insert(0,str(ARCHIVE))
from network_guard import install
install()
import arena
from alpha_lab import runner
from alpha_lab.risk import Gateway
from shared.messages import PlaceOrder,CancelOrder
from team.alpha.core import Intent
from exchange import config as cfg
BASE_GATEWAY=Gateway
BASE_SUMMARY=runner.summarize
BASE_REFRESH=runner.MarketPopulation.refresh

class CorrectedGateway(Gateway):
    """Reservation at decision; fixed wire payload; exchange alone rejects arrival state.

    Contingent basket hedge is a NEW decision when its first leg has filled.
    It receives a fresh reservation check, and can round to zero (never force 1).
    """
    def allowed_quantity(self,intent,exclude=None):
        p=self.server.portfolios[self.account];pos=p.positions.get(intent.symbol,0)
        reduces=(intent.side=='buy' and pos<0) or (intent.side=='sell' and pos>0)
        # Clip a reducing intent to flat: cannot exploit an entry-budget bypass.
        if reduces:intent=replace(intent,quantity=min(intent.quantity,abs(pos)))
        return super().allowed_quantity(intent,exclude)
    def schedule(self,intents,obs):
        # Every new two-leg attempt gets fresh fill counters; retries do not
        # inherit previous exit fills under a reused group identifier.
        rewritten=[];groups={}
        for i in intents:
            if i.group:
                groups.setdefault(i.group,f'{i.group}@{obs.tick}')
                i=replace(i,group=groups[i.group])
            rewritten.append(i)
        return super().schedule(rewritten,obs)
    async def execute_due(self,tick):
        due=sorted(x for x in self.queue if x[0]<=tick)
        self.queue=[x for x in self.queue if x[0]>tick]
        for _,_,kind,e in due:
            key=(e.symbol,e.side)
            if self.rx.pending.get(key) is not e:continue
            if kind=='cancel':
                await self.server._handle_cancel_order(self.account,CancelOrder(team_id=self.account,symbol=e.symbol,order_id=e.oid),self.account)
                if e.oid not in self.server.books[e.symbol]._orders:self.rx.pending.pop(key,None)
                continue
            if e.group and self.group_orders[e.group]!=e.symbol:
                first=self.group_orders[e.group]
                filled=self.rx.group_fills.get((e.group,first),0)
                qty=round(e.quantity*min(1.,filled/max(1,self.group_first_requested[e.group])))
                qty=self.allowed_quantity(Intent(e.symbol,e.side,qty,e.price,e.kind),exclude=e) if qty else 0
                if not qty:
                    self.rx.pending.pop(key,None);self.hedge_failures+=1;continue
                e.quantity=qty
            # Do not consult a later price, halt or cash snapshot to alter an
            # order already in transport. Exchange rejection is observable.
            await self.server._handle_place_order(self.account,PlaceOrder(team_id=self.account,symbol=e.symbol,side=e.side,order_type=e.kind,price=e.price,quantity=e.quantity),self.account)
            if e.kind=='ioc' or not e.oid or e.oid not in self.server.books[e.symbol]._orders:self.rx.pending.pop(key,None)
        self.sample_risk()

def summarize(s,accounts,receivers,gateways,curve,initial,starting_positions,starting_marks):
    out=BASE_SUMMARY(s,accounts,receivers,gateways,curve,initial,starting_positions,starting_marks)
    fees=sum(s.portfolios[a].total_fees_paid-s.portfolios[a].total_rebates_earned for a in accounts)
    out['total_net_fees']=fees;out['cancellation_fees_paid']=fees-out['net_fees']
    out['gross_pnl_before_execution_fees']=out['pnl']+fees
    # Match each account independently. Grants are per sleeve (same grant),
    # not a pooled position that can close one sleeve against another.
    closed=[];journal=[]
    for account,rx in zip(accounts,receivers):
        # Infer exact starting inventory from final positions minus all fills.
        initial_pos=dict(s.portfolios[account].positions)
        for f in rx.fills:initial_pos[f['symbol']]=initial_pos.get(f['symbol'],0)-f['quantity']*(1 if f['side']=='buy' else -1)
        lots=defaultdict(deque)
        for sym,q in initial_pos.items():
            if q:lots[sym].append([q,starting_marks[sym],0.])
        for f in rx.fills:
            journal.append(dict(f,account=account))
            signed=f['quantity']*(1 if f['side']=='buy' else -1);unit=(f['fee']-f['rebate'])/f['quantity'];pnl=0.;matched=False
            while signed and lots[f['symbol']] and signed*lots[f['symbol']][0][0]<0:
                lot=lots[f['symbol']][0];q=min(abs(signed),abs(lot[0]));direction=1 if lot[0]>0 else -1
                pnl+=q*((f['price']-lot[1])*direction-lot[2]-unit)
                lot[0]-=direction*q;signed+=direction*q;matched=True
                if not lot[0]:lots[f['symbol']].popleft()
            if signed:lots[f['symbol']].append([signed,f['price'],unit])
            if matched:closed.append(pnl)
    gains=sum(x for x in closed if x>0);losses=-sum(x for x in closed if x<0)
    out.update(closed_gains=gains,closed_losses=losses,profit_factor=(gains/losses if losses else None),
        fifo_win_rate=sum(x>0 for x in closed)/len(closed) if closed else None,fifo_closed_fills=len(closed),
        missing_fill_provenance=sum(f['decision_tick'] is None for f in journal),
        audit_equity_curve=curve,audit_fills=journal)
    return out

async def fast_refresh(self,force=False):
    self.ttl=1
    return await BASE_REFRESH(self,force)

PARAMS={'event_momentum':{'confirmation':3,'holding':120,'trailing':2.},'post_halt':{'z':2.5},'market_making':{'width':1.,'skew':1.5},'basket':{'z':3.5,'lookback':60}}
def components(name):
    if name=='combined':return [('event_momentum',PARAMS['event_momentum']),('post_halt',PARAMS['post_halt'])]
    if name=='combined_profit':return [('event_momentum',PARAMS['event_momentum']),('market_making',PARAMS['market_making'])]
    return [(name,PARAMS.get(name,{}))]

def job(payload):
    seed,week,mode,stress,names=payload
    runner.Gateway=BASE_GATEWAY if mode=='reproduce' else CorrectedGateway
    runner.summarize=summarize
    runner.MarketPopulation.refresh=fast_refresh if stress=='fast_quotes' else BASE_REFRESH
    # isolated() saves this value and preserves it inside the experiment.
    cfg.MAKER_REBATE_RATE=0. if stress=='no_rebate' else .001
    result=[]
    for name in names:
        r=asyncio.run(runner.run_one(seed,components(name),ticks=1500,week=week,stress=stress))
        r.update(candidate=name,mode=mode);result.append(r)
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stage',choices=['reproduce','fresh','stress'],required=True);ap.add_argument('--workers',type=int,default=8);ap.add_argument('--seeds',type=int,default=50);args=ap.parse_args()
    out=ROOT/'audit_review/results';out.mkdir(exist_ok=True)
    names=list(PARAMS)+['original','buy_hold','no_trade','combined','combined_profit']
    if args.stage=='stress':names=['event_momentum','market_making','original','no_trade','combined_profit']
    start=1000 if args.stage=='reproduce' else 3000
    stresses=['fast_quotes','no_rebate','fees_60bps','latency_3ticks'] if args.stage=='stress' else ['base']
    mode='reproduce' if args.stage=='reproduce' else 'corrected'
    jobs=[(seed,week,mode,stress,names) for stress in stresses for week in (5,9) for seed in range(start,start+args.seeds)]
    protocol={'stage':args.stage,'seeds':list(range(start,start+args.seeds)),'names':names,'stresses':stresses,'ticks':1500,'mode':mode,'params':PARAMS,'python':sys.version,'archive_sha256':json.loads((ROOT/'audit_review/archive_manifest.json').read_text()).get('sha256'),'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/(args.stage+'_protocol.json')).write_text(json.dumps(protocol,indent=2))
    t=time.monotonic();count=0
    with (out/(args.stage+'.jsonl')).open('w') as f,ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(job,j) for j in jobs]
        for future in as_completed(futures):
            rows=future.result()
            for r in rows:f.write(json.dumps(r,allow_nan=False)+'\n')
            f.flush();count+=1
            if count%10==0 or count==len(jobs):print(args.stage,count,'/',len(jobs),'runs',count*len(names),'seconds',round(time.monotonic()-t),flush=True)
if __name__=='__main__':main()
