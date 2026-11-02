#!/usr/bin/env python3
"""Summarize executed experiments; missing studies are never described as passes."""
import argparse
import csv
import json
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np


def confidence(values,seed=34551):
    a=np.asarray(values,dtype=float)
    if not len(a):return [None,None]
    rng=np.random.default_rng(seed)
    boot=a[rng.integers(0,len(a),size=(5000,len(a)))].mean(axis=1)
    return [float(np.quantile(boot,.025)),float(np.quantile(boot,.975))]


def aggregate(rows):
    benchmark={(r['week'],r['stress'],r['seed']):r['pnl'] for r in rows if r['candidate']=='no_trade'}
    grouped={}
    for r in rows:grouped.setdefault((r['week'],r['stress'],r['candidate']),[]).append(r)
    summary=[]
    for (week,stress,name),group in grouped.items():
        excess=[r['pnl']-benchmark[(week,stress,r['seed'])] for r in group if (week,stress,r['seed']) in benchmark]
        if len(excess)!=len(group):raise ValueError(f'missing paired no_trade benchmark for {name}')
        vals=lambda key:np.asarray([r[key] for r in group],dtype=float)
        win=[r['closed_fill_win_rate'] for r in group if r['closed_fill_win_rate'] is not None]
        rejection={}
        for r in group:
            for code,n in r['rejects'].items():rejection[code]=rejection.get(code,0)+n
        summary.append({'week':week,'stress':stress,'candidate':name,'strategy':group[0]['strategy'],
            'n_seeds':len(group),'mean_pnl':float(vals('pnl').mean()),'median_pnl':float(np.median(vals('pnl'))),
            'pnl_ci95':confidence(vals('pnl')),'mean_excess_no_trade':float(np.mean(excess)),
            'excess_ci95':confidence(excess),'profitable_seed_fraction':float(np.mean(vals('pnl')>0)),
            'beats_no_trade_fraction':float(np.mean(np.array(excess)>0)),
            'mean_net_fees':float(np.mean([r.get('total_net_fees',r['net_fees']) for r in group])),'mean_cancellation_fees':float(np.mean([r.get('cancellation_fees_paid',0.) for r in group])),'mean_commissions':float(vals('commissions').mean()),
            'mean_rebates':float(vals('maker_rebates').mean()),'mean_slippage':float(vals('slippage_vs_decision_mid').mean()),
            'mean_session_sharpe':float(vals('session_sharpe').mean()),
            'worst_drawdown':float(vals('max_drawdown').max()),'mean_turnover':float(vals('turnover').mean()),
            'mean_closed_fill_win_rate':float(np.mean(win)) if win else None,
            'mean_fills':float(vals('fills').mean()),'trading_seed_fraction':float(np.mean(vals('fills')>0)),
            'liquidations':int(vals('liquidations').sum()),
            'position_violations':int(vals('position_violations').sum()+vals('team_position_violations').sum()),
            'same_tick_fills':int(vals('same_tick_fills').sum()),
            'max_position':float(vals('max_abs_position').max()),'max_team_position':float(vals('max_team_position').max()),
            'mean_hedge_failures':float(vals('hedge_failures').mean()),'mean_unwound_overlay_remaining':float(vals('open_overlay_shares').mean()),
            'availability':group[0]['availability'],'rejects':rejection})
    return sorted(summary,key=lambda r:(r['week'],r['stress'],-r['mean_excess_no_trade']))


def load(path):return json.loads(path.read_text()) if path.exists() else []


def charts(out,summary,rows):
    import os
    os.environ.setdefault('MPLCONFIGDIR',str(out/'mpl_cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    for ax,week in zip(axes,(5,9)):
        items=[r for r in summary if r['week']==week and r['stress']=='base']
        names=[r['candidate']+(' (disabled)' if r['availability']!='enabled' else ' (inactive)' if r['candidate']=='original' and week==9 else '') for r in items];means=[r['mean_excess_no_trade'] for r in items]
        ax.barh(names,means,color=['#187f65' if x>0 else '#b64e48' for x in means])
        low=[m-r['excess_ci95'][0] for m,r in zip(means,items)]
        high=[r['excess_ci95'][1]-m for m,r in zip(means,items)]
        ax.errorbar(means,range(len(items)),xerr=[low,high],fmt='none',ecolor='#222',capsize=3)
        ax.axvline(0,color='black',lw=.8);ax.set_title(f'Week {week}: OOS paired excess ({items[0]["n_seeds"]} seeds)')
        ax.set_xlabel('After-fee P&L versus no-trade ($), bootstrap 95% CI');ax.invert_yaxis()
    fig.tight_layout();fig.savefig(out/'oos_profitability.png',dpi=180);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    for ax,week in zip(axes,(5,9)):
        items=[r for r in summary if r['week']==week and r['strategy'] in ('event_momentum','post_halt','market_making','basket','combined')]
        names=sorted({r['candidate'] for r in items})
        for stress in sorted({r['stress'] for r in items}):
            data={r['candidate']:r['mean_excess_no_trade'] for r in items if r['stress']==stress}
            ax.plot(names,[data.get(name,np.nan) for name in names],marker='o',label=stress)
        ax.axhline(0,color='black',lw=.8);ax.tick_params(axis='x',rotation=25)
        ax.set_title(f'Week {week} stress tests');ax.set_ylabel('Mean after-fee excess P&L ($)');ax.legend()
    fig.tight_layout();fig.savefig(out/'stress_profitability.png',dpi=180);plt.close(fig)
    confirmation=load(out/'portfolio_confirmation_summary.json')
    if confirmation:
        fig,axes=plt.subplots(1,2,figsize=(13,5))
        for ax,week in zip(axes,(5,9)):
            items=[r for r in confirmation if r['week']==week and r['stress']=='base']
            values=[r['mean_excess_no_trade'] for r in items]
            names=[r['candidate'] for r in items]
            ax.barh(names,values,color='#3573a6')
            low=[v-r['excess_ci95'][0] for v,r in zip(values,items)]
            high=[r['excess_ci95'][1]-v for v,r in zip(values,items)]
            ax.errorbar(values,range(len(items)),xerr=[low,high],fmt='none',ecolor='#222',capsize=3)
            ax.axvline(0,color='black',lw=.8);ax.invert_yaxis()
            ax.set_title(f'Week {week}: fresh portfolio confirmation (50 seeds)')
            ax.set_xlabel('After-fee excess vs no-trade ($), bootstrap 95% CI')
        fig.tight_layout();fig.savefig(out/'portfolio_confirmation.png',dpi=180);plt.close(fig)
    sensitivity=load(out/'sensitivity.json')
    fig,ax=plt.subplots(figsize=(7,4.5))
    matrix=np.zeros((4,4))
    for i,c in enumerate((3,5,10,15)):
        for j,h in enumerate((20,40,60,120)):
            scores=[r['mean_excess_no_trade'] for r in sensitivity if r['strategy']=='event_momentum'
                    and r['params']['confirmation']==c and r['params']['holding']==h]
            matrix[i,j]=np.mean(scores) if scores else np.nan
    im=ax.imshow(matrix,cmap='RdYlGn');ax.set_xticks(range(4),[20,40,60,120]);ax.set_yticks(range(4),[3,5,10,15])
    for i in range(4):
        for j in range(4):ax.text(j,i,f'${matrix[i,j]:.0f}',ha='center',va='center')
    ax.set_xlabel('Holding ticks');ax.set_ylabel('Confirmation ticks')
    ax.set_title('Calibration sensitivity only: mean excess\naveraged over trailing multipliers',fontsize=11)
    fig.colorbar(im,ax=ax,label='$ versus no-trade');fig.tight_layout();fig.savefig(out/'calibration_sensitivity.png',dpi=180);plt.close(fig)
    curves=load(out/'example_curves.json')
    if curves:
        fig,ax=plt.subplots(figsize=(10,5))
        for row in curves:
            path=np.asarray(row['equity_curve'])-row['initial_equity']
            ax.plot(path,label=row['candidate'])
        ax.set_title(f'Preregistered illustrative seed {curves[0]["seed"]}, Week 9 (not a selected winner)')
        ax.set_ylabel('MTM after-fee account P&L ($)');ax.set_xlabel('Continuous trading tick');ax.legend(ncol=2)
        fig.tight_layout();fig.savefig(out/'example_equity.png',dpi=180);plt.close(fig)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT/'alpha_results'/'study')
    ap.add_argument('--no-charts',action='store_true');args=ap.parse_args();out=args.input
    protocol=json.loads((out/'protocol.json').read_text())
    inventory=load(out/'data_inventory.json')
    for recording in inventory:
        path=Path(recording['file'])
        if path.exists():
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
            recording['sha256']=digest.hexdigest()
    (out/'data_inventory.json').write_text(json.dumps(inventory,indent=2))
    oos=load(out/'out_of_sample.json')
    if not oos:raise SystemExit('No executed out-of-sample results found.')
    expected=set(protocol['evaluation_seeds'])
    for week in (5,9):
        for name in ('event_momentum','post_halt','market_making','basket','combined','original','buy_hold','no_trade'):
            actual={r['seed'] for r in oos if r['week']==week and r['candidate']==name}
            if actual!=expected:raise SystemExit(f'Incomplete OOS evidence: week {week}, {name}')
    stresses=load(out/'stress.json')
    if stresses:
        for stress in ('fees_60bps','latency_3ticks','volatile'):
            for week in (5,9):
                for name in ('event_momentum','post_halt','market_making','basket','combined','original','buy_hold','no_trade'):
                    actual={r['seed'] for r in stresses if r['week']==week and r['stress']==stress and r['candidate']==name}
                    if actual!=expected:raise SystemExit(f'Incomplete stress evidence: {stress}, week {week}, {name}')
    if any(r['week']==9 and any(name=='market_making' for name,_ in r['components']) and 'total_net_fees' not in r for r in oos+stresses):
        raise SystemExit('Run scripts/alpha_fee_audit.py before reporting maker fees (P&L already includes cancellation charges).')
    summary=aggregate(oos+stresses)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    with (out/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]));writer.writeheader()
        writer.writerows({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in summary)
    lines=['# Offline alpha results','',
        'Rankings use held-out after-fee excess over a paired no-trade account. Absolute account P&L also includes endowed shares and cash interest. Robustness labels apply only to the seeded generator; the historical diagnostics do not validate a live edge.',
        f'Calibration: {len(protocol["calibration_seeds"])} seeds. Evaluation: {len(expected)} disjoint seeds. {protocol["ticks"]} ticks/run; starting cash ${protocol["cash"]:,.0f}; taker fee 30 bps.',
        '', '## Out-of-sample results','',
        '| Week | Strategy | Mean account P&L | Mean excess | Excess 95% CI | Beats no-trade | Mean fills | Worst DD |',
        '|---|---|---:|---:|---|---:|---:|---:|']
    for r in summary:
        if r['stress']!='base':continue
        lo,hi=r['excess_ci95']
        label=r['candidate']+(' (future disabled)' if r['availability']!='enabled' else ' (Week 5 gate)' if r['candidate']=='original' and r['week']==9 else '')
        lines.append(f'| {r["week"]} | {label} | ${r["mean_pnl"]:.2f} | ${r["mean_excess_no_trade"]:.2f} | [{lo:.2f}, {hi:.2f}] | {r["beats_no_trade_fraction"]:.0%} | {r["mean_fills"]:.1f} | {r["worst_drawdown"]:.2%} |')
    lines+=['','## Base-run costs and risk statistics','', '| Week | Strategy | Total net fees | Cancellation charges | Decision-mid slippage | Session Sharpe | Turnover | Closing-fill win rate |', '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        if r['stress']!='base':continue
        win='n/a' if r['mean_closed_fill_win_rate'] is None else f'{r["mean_closed_fill_win_rate"]:.1%}'
        lines.append(f'| {r["week"]} | {r["candidate"]} | ${r["mean_net_fees"]:.2f} | ${r["mean_cancellation_fees"]:.2f} | ${r["mean_slippage"]:.2f} | {r["mean_session_sharpe"]:.2f} | {r["mean_turnover"]:.2f} | {win} |')
    lines+=['','## Stress tests','', '| Week | Strategy | Regime | Mean excess | 95% CI | Liquidations | Position violations |', '|---|---|---|---:|---|---:|---:|']
    for r in summary:
        if r['stress']=='base':continue
        lo,hi=r['excess_ci95'];lines.append(f'| {r["week"]} | {r["candidate"]} | {r["stress"]} | ${r["mean_excess_no_trade"]:.2f} | [{lo:.2f}, {hi:.2f}] | {r["liquidations"]} | {r["position_violations"]} |')
    absum=aggregate(load(out/'ablations.json')) if (out/'ablations.json').exists() else []
    (out/'ablation_summary.json').write_text(json.dumps(absum,indent=2))
    lines+=['','## Ablations (held-out seeds; no retuning)','', '| Week | Variant | Mean excess | 95% CI | Mean fills |', '|---|---|---:|---|---:|']
    for r in absum:
        if r['candidate']=='no_trade':continue
        lo,hi=r['excess_ci95'];lines.append(f'| {r["week"]} | {r["candidate"]} | ${r["mean_excess_no_trade"]:.2f} | [{lo:.2f}, {hi:.2f}] | {r["mean_fills"]:.1f} |')
    lines+=['','## Profitability and robustness ranking','']
    rankings=[]
    for week in (5,9):
        active=[r for r in summary if r['week']==week and r['stress']=='base' and r['strategy'] in ('event_momentum','post_halt','market_making','basket') and r['availability']=='enabled']
        for r in active:
            stress_rows=[x for x in summary if x['week']==week and x['candidate']==r['candidate'] and x['stress']!='base']
            safe=not (r['liquidations'] or r['position_violations'] or r['same_tick_fills'])
            robust=(safe and r['mean_fills']>0 and r['excess_ci95'][0]>0 and len(stress_rows)==3 and all(x['excess_ci95'][0]>0 and not (x['liquidations'] or x['position_violations']) for x in stress_rows))
            rankings.append({**r,'robust_positive':robust,'safe':safe})
        ordered=sorted([r for r in rankings if r['week']==week],key=lambda r:(not r['safe'],not r['robust_positive'],-r['mean_excess_no_trade']))
        lines.append(f'Week {week} (safety first; positive CI in base and all three stresses outranks an unstable mean):')
        for rank,r in enumerate(ordered,1):
            status=('positive CI in base and all stresses' if r['robust_positive'] else 'inactive / no demonstrated edge' if not r['mean_fills'] else 'positive mean but not robust' if r['mean_excess_no_trade']>0 else 'lost versus no-trade')
            lines.append(f'{rank}. {r["candidate"]}: ${r["mean_excess_no_trade"]:.2f} mean excess; {status}.')
        if not any(r['robust_positive'] for r in ordered):lines.append('No strategy demonstrates robust positive trading alpha under these tests.')
        lines.append('')
    (out/'rankings.json').write_text(json.dumps(rankings,indent=2))
    # Condition analysis uses held-out strata only, without retuning.
    conditions=[]
    no_trade={(r['week'],r['stress'],r['seed']):r['pnl'] for r in oos if r['candidate']=='no_trade'}
    for week in (5,9):
        for name in ('event_momentum','post_halt','market_making','basket','combined'):
            for spread in sorted({r['spread_bps'] for r in oos}):
                group=[r for r in oos if r['week']==week and r['candidate']==name and r['spread_bps']==spread]
                if group:conditions.append({'week':week,'strategy':name,'spread_bps':spread,'n':len(group),'mean_excess':float(np.mean([r['pnl']-no_trade[(week,'base',r['seed'])] for r in group])),'mean_fills':float(np.mean([r['fills'] for r in group]))})
    (out/'conditions.json').write_text(json.dumps(conditions,indent=2))
    confirmation=out/'portfolio_confirmation'
    confirm_rows=load(confirmation/'results.json')+load(confirmation/'stress.json')
    if confirm_rows:
        definition=json.loads((confirmation/'definition.json').read_text())
        wanted=set(definition['seeds'])
        for week in (5,9):
            for stress in ('base','fees_60bps','latency_3ticks','volatile'):
                for name in ('combined_profit','original','no_trade'):
                    actual={r['seed'] for r in confirm_rows if r['week']==week and r['stress']==stress and r['candidate']==name}
                    if actual!=wanted:raise SystemExit(f'Incomplete independent portfolio confirmation: W{week}, {stress}, {name}')
        confirmation_summary=aggregate(confirm_rows)
        (out/'portfolio_confirmation_summary.json').write_text(json.dumps(confirmation_summary,indent=2))
        lines+=['','## Independent profit-ranked portfolio confirmation','',
            'Momentum plus market making was chosen by calibration mean excess, following the primary study. It is evaluated on 50 new seeds (2000–2049), not reused primary-study seeds. All sleeves share one total capital allocation and team constraints.',
            '', '| Week | Strategy | Regime | Mean account P&L | Mean excess | Excess 95% CI |', '|---|---|---|---:|---:|---|']
        for r in confirmation_summary:
            lo,hi=r['excess_ci95']
            lines.append(f'| {r["week"]} | {r["candidate"]} | {r["stress"]} | ${r["mean_pnl"]:.2f} | ${r["mean_excess_no_trade"]:.2f} | [{lo:.2f}, {hi:.2f}] |')
    history=load(out/'historical_funded.json') or load(out/'historical.json')
    if history and any(r.get('historical_fixture')!='funded_providers_v3' for r in history):
        raise SystemExit('Historical background inventory is unaudited; run scripts/alpha_history.py.')
    if history:
        lines+=['','## Secondary historical taker replay','', 'This is not an exact queue reconstruction. Maker performance is intentionally not estimated. These earlier-week tapes contain no halt/resume pair and do not enable ARENA10 futures. Week 4 is chronologically later than Weeks 2–3. These tapes were inspected during engineering and are supplementary diagnostics, not a pristine historical holdout. No historical tape is used for parameter selection.', '', '| Recording | Strategy | Account P&L | Excess versus no-trade | Fills |', '|---|---|---:|---:|---:|']
        baselines={r['file']:r['pnl'] for r in history if r['strategy']=='no_trade'}
        for r in history:lines.append(f'| {Path(r["file"]).name} | {r["strategy"]} | ${r["pnl"]:.2f} | ${r["pnl"]-baselines[r["file"]]:.2f} | {r["fills"]} |')
    lines+=['','## Interpretation and limits','',
        '* Positive account P&L is not automatically alpha. Use excess over the paired no-trade account, which holds the same initial grants and earns the same configured cash interest.',
        '* Synthetic seeds are independent price/event/flow draws, not 50 independent historical trading days. External liquidity and taker behavior are modeled; actual hosted profitability is unproven.',
        '* The actual order book supplies price-time priority, partial fills, stale resting quotes and cancellations; there is no touch-price fill shortcut. Commands execute no sooner than the next tick, before a new decision.',
        '* Futures are disabled in Week 5. Week 9 is a separate common-universe comparison; the original Week 5 bot intentionally remains inactive outside Week 5.',
        '* All equity strategies retain long-only stock inventory, per-seat caps, a 60% projected cash reserve and min(40% capital, $70k) gross bound at new-risk entry. Subsequent fees, price moves and futures variation can change those balances. The basket can short a capped cash-settled future in Week 9. Hedge legs are non-atomic and delayed; hedge failures and residual overlays are recorded.',
        '* P&L is conservatively marked by the real exchange. Fees/rebates/carry and actual cancellation charges are included once, not subtracted again. The fee audit replays maker runs, checks identical P&L/fill counts, and extracts authoritative total charges; trade commissions remain separately available. Slippage is a separate diagnostic relative to the decision mid. Sharpe is a session statistic, not annualized. Win rate is for FIFO closing fills including costs.',
        '* Baseline no-trade receives no special liquidation at the end. Trading sleeves try to return to their initial inventory during the final 12 ticks; halts or lack of liquidity can prevent this, and remaining overlay is reported.',
        '* Bootstrap intervals resample paired seeds, conditional on the generator. Multiple strategies are compared; the intervals are descriptive and are not a guarantee or a multiple-testing-adjusted discovery claim.',
        '* No hosted connection or live order is used. The proven live bot files and .env are unchanged.',
        '']
    if not (out/'stress.json').exists():lines+=['**Stress studies have not been executed yet.**','']
    if history:
        baselines={r['file']:r['pnl'] for r in history if r['strategy']=='no_trade'}
        momentum=[r for r in history if r['strategy']=='event_momentum']
        lines+=['','## Diagnosis','', 'The synthetic generator preserves the exchange calendar ramps and a simplified liquidity population. Success under those seeds is conditional on that structure, not proof of prediction in a live market.', '', 'Historical momentum diagnostics (same order paths, fees added back only for cost attribution):', '', '| Recording | After-fee excess | Fill commissions | Excess plus those commissions |', '|---|---:|---:|---:|']
        for r in momentum:
            excess=r['pnl']-baselines[r['file']]
            lines.append(f'| {Path(r["file"]).name} | ${excess:.2f} | ${r["commissions"]:.2f} | ${excess+r["net_fees"]:.2f} |')
        lines+=['', 'Adding fees back does not rerun a zero-fee strategy or remove market impact/cash-interest differences. Here the historical deficit is not explained by commissions alone; delayed price confirmation did not produce enough subsequent continuation on these recorded books.', '', 'Post-halt reversion has few eligible trades: the actual five-minute halt is much longer than the default 15-tick news ramp, and the permanent-earnings filter excludes most large displacements. The remaining trades do not establish a positive edge.', '', 'Market-making gains depend substantially on rebates and the modeled taker population. The historical tapes cannot verify its maker queues, and the Week 5 latency interval crosses zero. Fee-insensitive simulated flow is a material assumption.', '', 'Basket residual z-scores do not establish a stationary relationship. After the cost gate, integer futures sizing and non-atomic legs, the few executed trades have negative average excess. There is no profitable basket result to promote.', '', 'The original Week 5 bot remains the strongest tested Week 5 baseline. The new strategies and profit-ranked portfolio are not installed into the live bots. No new strategy has demonstrated historical-validated profitability from these inputs.', '']
    (out/'REPORT.md').write_text('\n'.join(lines))
    if not args.no_charts:charts(out,summary,oos)
    print('\n'.join(lines[:27]));print(f'Full results: {out/"REPORT.md"}')


if __name__=='__main__':main()
