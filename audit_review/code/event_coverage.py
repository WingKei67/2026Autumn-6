"""Audit event regime coverage; future resolved directions never go to alphas."""
import asyncio,json,csv
from pathlib import Path
from statistics import mean
from audit_study import runner,ROOT
from alpha_lab.exchange import OfflineExchange

def reconstruct(seed,week):
    with runner.isolated(seed,week,175000.,1) as (_,scenario,_):
        server=OfflineExchange();server.set_scenario(scenario)
        return [dict(seed=seed,week=week,tick=e.tick,kind=e.kind,symbol=e.symbol or '*',pct=e.resolve()) for e in server.calendar.events if e.kind!='dividend']

def main():
    original=runner.summarize
    def capture(server,*args):
        r=original(server,*args);r['resolved_events']=[dict(tick=e.tick,pct=e.resolved_pct) for e in server.calendar.events if e.kind!='dividend'];return r
    runner.summarize=capture
    checked=[]
    for week in [5,9]:
        for seed in [3000,3001]:
            expected=reconstruct(seed,week)
            r=asyncio.run(runner.run_one(seed,[('no_trade',{})],week=week,ticks=1500))
            assert r['resolved_events']==[dict(tick=e['tick'],pct=e['pct']) for e in expected]
            checked.append(dict(week=week,seed=seed,match=True))
    events=[e for week in [5,9] for seed in range(3000,3050) for e in reconstruct(seed,week)]
    out=ROOT/'audit_review/results'
    with (out/'event_manifest.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(events[0]));w.writeheader();w.writerows(events)
    (out/'event_manifest_validation.json').write_text(json.dumps({'validation_actual_replays':checked,'method':'Chronological CalendarEvent.resolve draws under isolated seeded RNG; source inspection finds no other active global-RNG consumers. Validated against actual full no-trade simulations; audit analysis only, not signal input.'},indent=2))
    rows=[json.loads(x) for x in (out/'fresh.jsonl').open()];baseline={(r['week'],r['seed']):r['pnl'] for r in rows if r['candidate']=='no_trade'}
    conditions=[]
    for week in [5,9]:
        for tick in sorted(set(e['tick'] for e in events if e['week']==week)):
            for direction in ['up','down']:
                selected=[e for e in events if e['week']==week and e['tick']==tick and (e['pct']>0)==(direction=='up')]
                seeds={e['seed'] for e in selected}
                for name in ['event_momentum','market_making','post_halt','basket','combined_profit','original']:
                    sample=[r for r in rows if r['week']==week and r['candidate']==name and r['seed'] in seeds]
                    conditions.append(dict(week=week,event_tick=tick,symbol=selected[0]['symbol'],direction=direction,seeds=len(seeds),mean_abs_shock=mean(abs(e['pct']) for e in selected),strategy=name,mean_excess=mean(r['pnl']-baseline[(week,r['seed'])] for r in sample)))
    with (out/'event_direction_conditions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(conditions[0]));w.writeheader();w.writerows(conditions)
    print('Validated 4 actual no-trade replays; reconstructed',len(events),'resolved price events.',flush=True)
if __name__=='__main__':main()
