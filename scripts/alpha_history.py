#!/usr/bin/env python3
"""Funded, bounded background accounts for the secondary historical replay.

Earlier scenarios prohibit shorts. Reconstructing a historical offer therefore
requires a genuinely funded stock inventory, not an unfunded synthetic ask.
This fixture adapter supplies 200 shares/provider and truncates each displayed
level to at most 60 shares. All orders still traverse the real exchange checks.
The frozen primary simulation and its strategies are unchanged.
"""
import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import arena
import alpha_lab.history as history
from alpha_lab.data import recording_paths
from alpha_lab.experiment import save_rows


async def run_repaired(path,name,params,cash=None):
    original_portfolio=history.Portfolio;original_order=history.PlaceOrder
    providers={}
    original_exchange=history.OfflineExchange;original_frames=history.frames;original_summary=history.summarize
    class PhaseAwareExchange(original_exchange):
        async def _broadcast(self,msg,skip_ids=None):
            if getattr(msg,'event',None) in ('SESSION_PREOPEN','SESSION_PRECLOSE','SESSION_CLOSED'):
                self.session_open=False
                self.auction_phase='preopen' if msg.event=='SESSION_PREOPEN' else None
                for book in self.books.values():book.auction_mode=bool(self.auction_phase)
            elif getattr(msg,'event',None)=='SESSION_OPEN':
                self.session_open=True;self.auction_phase=None
                for book in self.books.values():book.auction_mode=False
            await super()._broadcast(msg,skip_ids)
        async def _handle_place_order(self,ws,msg,team_id):
            if team_id.startswith('alpha_') and not self.session_open:
                raise AssertionError('historical subject submitted outside continuous trading')
            return await super()._handle_place_order(ws,msg,team_id)
        async def _apply_carry_and_maintenance(self):
            if self.session_open:await super()._apply_carry_and_maintenance()
    def phase_frames(file):
        tape=original_frames(file)
        if tape and any(e.get('event')=='SESSION_OPEN' for _,frame in tape for e in frame['events']):
            tick,frame=tape[0]
            first={**frame,'events':[{'type':'session_event','event':'SESSION_PREOPEN','message':'await recorded open','data':{}}]+list(frame['events'])}
            return [(tick,first)]+tape[1:]
        return tape
    def observed_summary(server,accounts,receivers,*args,**kwargs):
        result=original_summary(server,accounts,receivers,*args,**kwargs)
        fills=[f for rx in receivers for f in rx.fills]
        result['first_fill_tick']=min((f['tick'] for f in fills),default=None)
        result['last_fill_tick']=max((f['tick'] for f in fills),default=None)
        return result
    def funded_portfolio(*args,**kwargs):
        p=original_portfolio(*args,**kwargs)
        if p.team_id.startswith('liquidity_'):
            sym=p.team_id.removeprefix('liquidity_')
            if sym!='ARENA10':p.positions[sym]=200
            providers[p.team_id]=p
        return p
    def bounded_order(*args,**kwargs):
        provider=providers.get(kwargs.get('team_id'))
        if provider is not None:
            qty=min(60,int(kwargs['quantity']))
            if kwargs['side']=='sell':qty=min(qty,provider.positions.get(kwargs['symbol'],0))
            kwargs['quantity']=qty
        return original_order(*args,**kwargs)
    with patch.object(history,'Portfolio',funded_portfolio),patch.object(history,'PlaceOrder',bounded_order),patch.object(history,'OfflineExchange',PhaseAwareExchange),patch.object(history,'frames',phase_frames),patch.object(history,'summarize',observed_summary):
        result=await history.run_history(path,name,params,cash)
    result.update(background_initial_inventory=200,depth_cap_per_level=60,
        historical_fixture='funded_providers_v3',recorded_session_phases_enforced=True,
        fixture_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    return result


def historical_job(payload):
    path,name,params,cash=payload
    return asyncio.run(run_repaired(path,name,params,cash))


def study_repaired(root,chosen,cash=None,workers=4):
    result=[];jobs=[]
    for path in recording_paths(Path(root)/'sessions'):
        for name in ('event_momentum','post_halt','basket','buy_hold','no_trade'):
            jobs.append((str(path),name,chosen[name]['params'] if name in chosen else {},cash))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(historical_job,payload) for payload in jobs]
        for future in as_completed(futures):
            row=future.result();result.append(row)
            print(f'Funded historical replay {Path(row["file"]).name}: {row["strategy"]}, pnl={row["pnl"]:.2f}, fills={row["fills"]}',flush=True)
    return sorted(result,key=lambda r:(r['file'],r['strategy']))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT/'alpha_results'/'study')
    ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();out=args.input
    protocol=json.loads((out/'protocol.json').read_text())
    chosen=json.loads((out/'frozen_selection.json').read_text())
    results=study_repaired(ROOT,chosen,protocol['cash'],args.workers)
    save_rows(out/'historical_funded',results)
    save_rows(out/'historical',results)


if __name__=='__main__':main()
