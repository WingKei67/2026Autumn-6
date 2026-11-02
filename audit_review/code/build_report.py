"""Build audit tables/figures from executed runs, never fill missing results."""
from pathlib import Path
import os,json,csv,math,sys
from collections import defaultdict
from statistics import mean
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'audit_review';RESULTS=OUT/'results';ARCHIVE=OUT/'extracted/2026Autumn-6-fresh'
os.environ['MPLCONFIGDIR']=str(OUT/'mpl_cache')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from findings import ISSUES
ISSUES=sorted(ISSUES,key=lambda x: {'Critical':0,'High':1,'Medium':2,'Low':3}[x[0]])
from fifo import overlay_fifo

def load(stage):
 p=RESULTS/(stage+'.jsonl')
 return [json.loads(x) for x in p.open()] if p.exists() else []
def save_csv(path,rows):
 if not rows:return
 keys=list(dict.fromkeys(k for r in rows for k in r))
 with path.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
def ci(v):
 v=np.array(v);rng=np.random.default_rng(456789)
 return np.quantile(v[rng.integers(0,len(v),size=(5000,len(v)))].mean(axis=1),[.025,.975]).tolist()
def table(rows,cols):
 def fmt(v):
  if v is None:return 'N/A'
  if isinstance(v,float):return f'{v:,.2f}'
  return str(v)
 return '| '+' | '.join(cols)+' |\n| '+' | '.join('---' for _ in cols)+' |\n'+'\n'.join('| '+' | '.join(fmt(r.get(k)) for k in cols)+' |' for r in rows)

def main():
 fresh=load('fresh');stress=load('stress');repro=load('reproduce');paired=load('paired_corrections')
 for name,rows,expected in [('fresh',fresh,900),('stress',stress,2000),('reproduce',repro,900),('paired_corrections',paired,500)]:
  if len(rows)!=expected:raise RuntimeError(f'{name}: incomplete study ({len(rows)}/{expected}); refusing a final report')
 for row in fresh+stress:
  row['overlay_fifo']=overlay_fifo(row['audit_fills'])
 baseline={(r['week'],r['seed'],r['stress']):r['pnl'] for r in fresh+stress if r['candidate']=='no_trade'}
 groups=defaultdict(list)
 for r in fresh+stress:groups[(r['week'],r['candidate'],r['stress'])].append(r)
 summary=[]
 for (week,name,regime),rows in sorted(groups.items()):
  excess=[r['pnl']-baseline[(week,r['seed'],regime)] for r in rows];lo,hi=ci(excess)
  gains=sum(r['overlay_fifo']['gains'] for r in rows);losses=sum(r['overlay_fifo']['losses'] for r in rows)
  closed=sum(r['overlay_fifo']['count'] for r in rows)
  wins=sum(r['overlay_fifo']['wins'] for r in rows)
  summary.append(dict(availability='inactive_week_gate' if name=='original' and week==9 else rows[0]['availability'],evidence_quality={'event_momentum':'Synthetic OOS; three negative historical overlays','post_halt':'Synthetic OOS; sparse activity; no historical trades','market_making':'Synthetic OOS; no empirical queue validation','basket':'Week 5 disabled; sparse Week 9 synthetic trades','original':'Adapted actual policy; inactive Week 9','buy_hold':'30% capital benchmark; terminal not matched','no_trade':'Endowed inventory reference','combined':'Synthetic OOS; split independent sleeves','combined_profit':'Synthetic OOS; no empirical combined replay'}[name],week=week,strategy=name,regime=regime,seeds=len(set(r['seed'] for r in rows)),net_pnl=mean(r['pnl'] for r in rows),
   excess_no_trade=mean(excess),excess_ci_low=lo,excess_ci_high=hi,gross_pnl=mean(r['gross_pnl_before_execution_fees'] for r in rows),
   session_sharpe=mean(r['session_sharpe'] for r in rows),worst_drawdown_pct=100*max(r['max_drawdown'] for r in rows),
   closed_fill_profit_factor=gains/losses if losses else None,profit_factor=gains/(losses+sum(r['cancellation_fees_paid']+r['carry_paid'] for r in rows)) if losses+sum(r['cancellation_fees_paid']+r['carry_paid'] for r in rows)>0 else None,closed_fill_win_rate_pct=100*wins/closed if closed else None,
   mean_turnover=mean(r['turnover'] for r in rows),mean_net_transaction_costs=mean(r['total_net_fees'] for r in rows),
   mean_commissions=mean(r['commissions'] for r in rows),mean_rebates=mean(r['maker_rebates'] for r in rows),mean_cancel_costs=mean(r['cancellation_fees_paid'] for r in rows),
   mean_slippage=mean(r['slippage_vs_decision_mid'] for r in rows),mean_carry=mean(r['carry_paid'] for r in rows),mean_fills=mean(r['fills'] for r in rows),total_closed_fills=closed,
   liquidations=sum(r['liquidations'] for r in rows),position_violations=sum(r['position_violations'] for r in rows),team_violations=sum(r['team_position_violations'] for r in rows),
   missing_fill_provenance=sum(r['missing_fill_provenance'] for r in rows),same_tick_fills=sum(r['same_tick_fills'] for r in rows),
   hedge_failures=sum(r['hedge_failures'] for r in rows),mean_open_overlay=mean(r['open_overlay_shares'] for r in rows)))
 pairs=[]
 for week in (5,9):
  for left,right in [('market_making','event_momentum'),('combined_profit','event_momentum'),('original','event_momentum')]:
   a={r['seed']:r['pnl'] for r in fresh if r['week']==week and r['candidate']==left}
   b={r['seed']:r['pnl'] for r in fresh if r['week']==week and r['candidate']==right}
   delta=[a[seed]-b[seed] for seed in sorted(a.keys()&b.keys())];lo,hi=ci(delta)
   pairs.append(dict(week=week,left=left,right=right,seeds=len(delta),mean_difference=mean(delta),ci_low=lo,ci_high=hi))
 save_csv(RESULTS/'pairwise_comparisons.csv',pairs)
 save_csv(RESULTS/'strategy_metrics.csv',summary);(RESULTS/'strategy_metrics.json').write_text(json.dumps(summary,indent=2))
 # Supplied primary result reproduction is an exact deterministic comparison.
 supplied=json.loads((ARCHIVE/'alpha_results/study/out_of_sample.json').read_text());lookup={(r['week'],r['seed'],r['candidate']):r for r in supplied}
 diffs=[]
 for r in repro:
  old=lookup.get((r['week'],r['seed'],r['candidate']))
  if old:diffs.append(dict(week=r['week'],seed=r['seed'],strategy=r['candidate'],pnl_delta=r['pnl']-old['pnl'],fills_delta=r['fills']-old['fills']))
 save_csv(RESULTS/'reproduction_comparison.csv',diffs)
 verification={'reproduced_runs':len(repro),'matched_primary_runs':len(diffs),'max_absolute_pnl_difference':max((abs(x['pnl_delta']) for x in diffs),default=None),'fill_count_mismatches':sum(x['fills_delta']!=0 for x in diffs)}
 hist=json.loads((RESULTS/'historical_reproduced.json').read_text()) if (RESULTS/'historical_reproduced.json').exists() else []
 oldhist=json.loads((ARCHIVE/'alpha_results/study/historical.json').read_text());hl={(Path(r['file']).name,r['strategy']):r for r in oldhist}
 hd=[]
 for r in hist:
  b=next((x['pnl'] for x in hist if x['file']==r['file'] and x['strategy']=='no_trade'),None)
  old=hl.get((Path(r['file']).name,r['strategy']))
  hd.append(dict(file=Path(r['file']).name,strategy=r['strategy'],net_pnl=r['pnl'],excess_no_trade=r['pnl']-b if b is not None else None,commissions=r['commissions'],slippage=r['slippage_vs_decision_mid'],fills=r['fills'],pnl_reproduction_delta=r['pnl']-old['pnl'] if old else None))
 save_csv(RESULTS/'historical_comparison.csv',hd)
 verification.update(historical_runs=len(hist),historical_max_pnl_difference=max((abs(x['pnl_reproduction_delta']) for x in hd if x['pnl_reproduction_delta'] is not None),default=None))
 oldpaired={(r['week'],r['seed'],r['candidate']):r for r in repro}
 effects=defaultdict(list)
 for r in paired:
  old=oldpaired[(r['week'],r['seed'],r['candidate'])]
  effects[(r['week'],r['candidate'])].append(dict(pnl=r['pnl']-old['pnl'],fills=r['fills']-old['fills'],fees=r['total_net_fees']-old['total_net_fees']))
 effect_rows=[]
 for (week,name),items in sorted(effects.items()):
  lo,hi=ci([x['pnl'] for x in items]);effect_rows.append(dict(week=week,strategy=name,seeds=len(items),mean_pnl_change=mean(x['pnl'] for x in items),min_pnl_change=min(x['pnl'] for x in items),max_pnl_change=max(x['pnl'] for x in items),ci_low=lo,ci_high=hi,mean_fill_change=mean(x['fills'] for x in items)))
 save_csv(RESULTS/'paired_correction_effects.csv',effect_rows)
 verification['paired_correction_runs']=len(paired)
 (RESULTS/'reproduction_verification.json').write_text(json.dumps(verification,indent=2))
 (OUT/'issues.json').write_text(json.dumps([dict(severity=x[0],file=x[1],function=x[2],line=x[3],finding=x[4],resolution=x[5]) for x in ISSUES],indent=2))
 figs=OUT/'figures';figs.mkdir(exist_ok=True)
 # Every seed's curve saved, illustrative path fixed before seeing outcomes.
 fig,axes=plt.subplots(2,2,figsize=(13,8))
 for week,col in [(5,0),(9,1)]:
  for name in ['original','event_momentum','post_halt','market_making','basket','combined_profit','buy_hold','no_trade']:
   r=next((r for r in fresh if r['week']==week and r['seed']==3000 and r['candidate']==name),None)
   if not r:continue
   eq=np.array([r['initial_equity']]+r['audit_equity_curve']);axes[0,col].plot(eq-eq[0],label=name,lw=1);axes[1,col].plot(100*(eq/np.maximum.accumulate(eq)-1),lw=1)
  axes[0,col].set_title(f'Week {week}, preselected seed 3000');axes[0,col].set_ylabel('MTM P&L ($)');axes[1,col].set_ylabel('Drawdown (%)');axes[1,col].set_xlabel('Tick')
 axes[0,1].legend(fontsize=7);fig.tight_layout();fig.savefig(figs/'equity_drawdown.png',dpi=150);plt.close(fig)
 fig,axes=plt.subplots(1,2,figsize=(13,5))
 names=['original','event_momentum','market_making','post_halt','basket','combined_profit','buy_hold']
 for ax,week in zip(axes,[5,9]):
  data=[[r['pnl']-baseline[(week,r['seed'],'base')] for r in fresh if r['week']==week and r['candidate']==n] for n in names]
  ax.boxplot(data,tick_labels=names,showmeans=True);ax.axhline(0,color='k',lw=.7);ax.tick_params(axis='x',rotation=45);ax.set_title(f'Week {week}: paired excess vs no trade');ax.set_ylabel('$ per seed')
 fig.tight_layout();fig.savefig(figs/'seed_distribution.png',dpi=150);plt.close(fig)
 fig,axes=plt.subplots(1,2,figsize=(13,5))
 for ax,week in zip(axes,[5,9]):
  data=[next(r for r in summary if r['week']==week and r['regime']=='base' and r['strategy']==n) for n in names]
  x=np.arange(len(names));ax.bar(x-.24,[r['gross_pnl'] for r in data],.24,label='Gross before net fees');ax.bar(x,[r['mean_net_transaction_costs'] for r in data],.24,label='Net fees (negative = rebate)');ax.bar(x+.24,[r['net_pnl'] for r in data],.24,label='After-fee MTM');ax.set_xticks(x,names,rotation=45);ax.set_title(f'Week {week} cost decomposition');ax.set_ylabel('$')
 axes[1].legend(fontsize=7);fig.tight_layout();fig.savefig(figs/'cost_decomposition.png',dpi=150);plt.close(fig)
 sens=json.loads((ARCHIVE/'alpha_results/study/sensitivity.json').read_text())
 fig,axes=plt.subplots(2,3,figsize=(15,8))
 def heat(ax,name,xkey,xvals,ykey,yvals,extra,title):
  cells=np.array([[next(r['mean_excess_no_trade'] for r in sens if r['strategy']==name and r['params'][xkey]==x and r['params'][ykey]==y and all(r['params'][k]==v for k,v in extra.items())) for x in xvals] for y in yvals])
  bound=max(1,float(np.abs(cells).max()));im=ax.imshow(cells,cmap='RdYlGn',vmin=-bound,vmax=bound,aspect='auto')
  ax.set_xticks(range(len(xvals)),xvals);ax.set_yticks(range(len(yvals)),yvals);ax.set_xlabel(xkey);ax.set_ylabel(ykey);ax.set_title(title)
  for y in range(len(yvals)):
   for x in range(len(xvals)):ax.text(x,y,f'{cells[y,x]:.0f}',ha='center',va='center',fontsize=8)
  fig.colorbar(im,ax=ax,label='Mean excess $')
 heat(axes[0,0],'event_momentum','holding',[20,40,60,120],'confirmation',[3,5,10,15],{'trailing':2.},'A: trailing=2 (calibration)')
 heat(axes[0,1],'event_momentum','holding',[20,40,60,120],'confirmation',[3,5,10,15],{'trailing':4.},'A: trailing=4 (calibration)')
 for ax,name,label in [(axes[0,2],'post_halt','B: reversal threshold'),(axes[1,1],'basket','D: residual threshold')]:
  for lookback in ([60,120] if name=='basket' else [None]):
   rows=sorted([r for r in sens if r['strategy']==name and (lookback is None or r['params']['lookback']==lookback)],key=lambda r:r['params']['z'])
   ax.plot([r['params']['z'] for r in rows],[r['mean_excess_no_trade'] for r in rows],marker='o',label=f'lookback={lookback}')
  ax.axhline(0,color='k',lw=.7);ax.set_title(label);ax.set_xlabel('z threshold');ax.set_ylabel('Mean excess $')
  if name=='basket':ax.legend(fontsize=8)
 heat(axes[1,0],'market_making','width',[1.,1.5,2.5],'skew',[.5,1.5],{},'C: width / inventory skew')
 axes[1,2].axis('off');axes[1,2].text(.05,.8,'Supplied calibration only\n12 seeds per setting\n50 parameter settings\nCells: paired excess over no-trade\nNo parameter refit on fresh seeds',va='top',fontsize=11)
 fig.tight_layout();fig.savefig(figs/'calibration_sensitivity.png',dpi=150);plt.close(fig)
 if stress:
  fig,axes=plt.subplots(1,2,figsize=(13,5))
  for ax,week in zip(axes,[5,9]):
   for name in ['original','event_momentum','market_making','combined_profit']:
    regimes=['base','fees_60bps','latency_3ticks','fast_quotes','no_rebate'];rs=[next((r for r in summary if r['week']==week and r['strategy']==name and r['regime']==s),None) for s in regimes]
    ax.plot(regimes,[r['excess_no_trade'] if r else np.nan for r in rs],marker='o',label=name)
   ax.axhline(0,color='k',lw=.7);ax.tick_params(axis='x',rotation=30);ax.set_title(f'Week {week} frozen-seed stress');ax.set_ylabel('Mean excess $')
  axes[1].legend();fig.tight_layout();fig.savefig(figs/'stress_sensitivity.png',dpi=150);plt.close(fig)
 # Per-regime conditioned statistics; not independent factorial estimates.
 conditions=[]
 for (week,name,regime),rows in groups.items():
  if regime!='base':continue
  for spread in sorted(set(r['spread_bps'] for r in rows)):
   selected=[r for r in rows if r['spread_bps']==spread];conditions.append(dict(week=week,strategy=name,spread_bps=spread,seeds=len(selected),mean_excess=mean(r['pnl']-baseline[(week,r['seed'],'base')] for r in selected)))
 save_csv(RESULTS/'regime_conditions.csv',conditions)
 vol_conditions=[];rejections=[]
 for (week,name,regime),rows in groups.items():
  counts=defaultdict(int)
  for r in rows:
   for code,n in r['rejects'].items():counts[code]+=n
  for code,n in counts.items():rejections.append(dict(week=week,strategy=name,regime=regime,code=code,count=n))
  if regime=='base':
   for vol in sorted(set(r['volatility_multiplier'] for r in rows)):
    selected=[r for r in rows if r['volatility_multiplier']==vol]
    vol_conditions.append(dict(week=week,strategy=name,volatility_multiplier=vol,seeds=len(selected),mean_excess=mean(r['pnl']-baseline[(week,r['seed'],'base')] for r in selected)))
 save_csv(RESULTS/'volatility_regimes.csv',vol_conditions);save_csv(RESULTS/'order_rejections.csv',rejections)
 supplied_stress=json.loads((ARCHIVE/'alpha_results/study/summary.json').read_text())
 supplied_stress=[dict(week=r['week'],strategy=r['strategy'],regime=r['stress'],seeds=r['n_seeds'],mean_excess=r['mean_excess_no_trade'],ci_low=r['excess_ci95'][0],ci_high=r['excess_ci95'][1],net_fees=r['mean_net_fees']) for r in supplied_stress if r['stress']!='base']
 save_csv(RESULTS/'supplied_stress_summary.csv',supplied_stress)

 event_file=RESULTS/'event_direction_conditions.csv'
 with event_file.open() as f:
  event_conditions=[dict(r,week=int(r['week']),event_tick=int(r['event_tick']),seeds=int(r['seeds']),mean_excess=float(r['mean_excess'])) for r in csv.DictReader(f) if r['strategy'] in ['event_momentum','market_making','combined_profit']]
 base=sorted([r for r in summary if r['regime']=='base'],key=lambda r:(r['week'],-r['excess_no_trade']))
 eligible={n:[r for r in base if r['strategy']==n and (n!='basket' or r['week']==9)] for n in ['event_momentum','post_halt','market_making','basket']}
 best_mean=max(eligible,key=lambda n:mean(r['excess_no_trade'] for r in eligible[n]))
 worst_mean=min(eligible,key=lambda n:mean(r['excess_no_trade'] for r in eligible[n]))
 robust={n:min(r['excess_ci_low'] for r in summary if r['strategy']==n) for n in ['event_momentum','market_making']}
 best_robust=max(robust,key=robust.get)
 verdict=f'By mean incremental net P&L across eligible weeks, the best new single strategy is **{best_mean}** and the weakest is **{worst_mean}**. Among the two active single-strategy finalists, **{best_robust}** has the strongest minimum lower confidence bound across tested base/cost/execution regimes ({robust[best_robust]:.2f} dollars). This conservative descriptive ranking is conditional on the generator and is not an adjusted multi-test confidence statement. The A+C portfolio is the strongest new combined candidate by base mean P&L; the original dominates absolute Week 5 simulated returns, with different sizing and information.'
 (RESULTS/'executive_verdict.json').write_text(json.dumps(dict(best_new_mean=best_mean,worst_new_mean=worst_mean,best_single_robustness=best_robust,minimum_lower_bounds=robust),indent=2))

 lines=['# Independent AlgoArena quantitative audit','',
 '## Executive verdict','',verdict,'',
 'The primary ranking below is the newly executed, frozen-parameter evaluation on seeds 3000–3049, using the corrected research gateway. Positive synthetic performance is evidence about this specified generator, not convincing real-market alpha. Historical event momentum is negative relative to no-trade on all three available recordings. Do not promote these alphas to the proven live bot on this evidence.',
 '', 'The original Week 5 strategy must be judged separately: it trades larger risk and uses public calendar shock-shape information. In Week 9 it is explicitly disabled. The best new strategy and combined portfolio are identified by after-fee excess below; no-trade is the proper endowed-inventory comparator.',
 '', '## Executed evidence and reproducibility','','```json\n'+json.dumps(verification,indent=2)+'\n```','',
 'Archive SHA256: `1fe1d222c67447ace8f14ba5bcc2a9a8e1ce91e3b0303a5d344ba84f23f7cf76`. Extracted code is preserved unchanged under `extracted/2026Autumn-6-fresh`. All new code, output and fixes live under `audit_review/`; `.env` and live bot code were not edited by this audit.',
 '', 'Archive tests: **317 passed, 3 skipped**, 320 collected, 0 failures/errors (see XML). The root repository suite also passed **317 tests with 3 skips**, with extracted copies excluded from default discovery. Audit regressions: **14 passed**. The first sandboxed root run had two localhost-binding permission failures; the permitted rerun passed with the non-loopback guard still active. Network guard blocks non-loopback socket connections; only isolated local mechanics are used. The tests that require live smoke execution remain skipped. Diagnostic regressions intentionally reproduce two unsafe original-exchange outcomes; a passing diagnostic is evidence that the bug exists, not that the original venue was repaired. The corrected fixture is tested separately against the ACK settlement interleaving.',
 '', f'Executed synthetic trials in this audit: reproduction {len(repro)}, fresh corrected {len(fresh)}, corrected stress {len(stress)}, paired correction diagnostics {len(paired)}. Each complete comparison uses 50 identical exogenous seeds per strategy/week; price paths can respond to the candidate’s actual order impact, so realized prices are not artificially forced identical; weeks are different scenarios using the same seed IDs, not 100 independent seeds. Seeds 1000–1049 are reproduction only; new seeds 3000–3049 were fixed before inspecting outcomes. No parameter retuning on them.',
 '', 'The ZIP omits all three historical recordings. Local external copies match the archived SHA256 inventory exactly; 15 funded historical replays are reproduced separately. This is not a self-contained archive and these previously inspected days are not pristine held-out data. There is no Week 5 historical tape. Synthetic experiments never silently stand in for historical backtests.',
 '', 'Supplied files document 624 calibration, 800 primary OOS, 550 ablation, 2400 stress, 500 secondary confirmation and 900 secondary stress runs: 5774 seeded trials, 112 distinct seed IDs (12 calibration + 50 primary + 50 confirmation). Fee-audit replays, illustrative curves and historical replays are not additional independent seeds. Saved outputs show execution, but only the explicitly reproduced subsets are independently verified here.',
 '', '## Fresh out-of-sample ranking','',
 table(base,['week','strategy','seeds','net_pnl','excess_no_trade','excess_ci_low','excess_ci_high','gross_pnl','mean_net_transaction_costs','mean_fills']),
 '', 'All dollars are mean per 1500-tick trial, not projected live-session dollars. Starting cash is a controlled $175,000 TOTAL per experiment, taken from configured trader 1, plus the exchange grant; the live configuration is $175,000/$175,000/$350,000 for trader 1/trader 2/broker. Thus this is not a $700,000 three-seat live-account replay, and the combined experiment splits a common $175,000 budget rather than duplicating live account allocations; all fees come from actual portfolio accounting. The study deliberately charges taker 30 bps (the earlier requested research setting), with maker rebate 10 bps where eligible. The repository’s bare default taker setting is 15 bps and can be overridden by venue/environment configuration; no claim is made that 30 bps is the universal hosted default. Week 9 cancellation fees included. Gross = net MTM P&L + actual net execution fees; carry is still deducted. This is an accounting decomposition, not a zero-fee counterfactual. Slippage vs decision mid is already inside P&L and is not subtracted twice.',
 '', 'Pairwise differences use the same seeds; do not infer superiority merely from overlapping or non-overlapping marginal intervals.','',table(pairs,['week','left','right','seeds','mean_difference','ci_low','ci_high']),'', '## Risk and trading statistics','',
 table(base,['week','strategy','session_sharpe','worst_drawdown_pct','profit_factor','closed_fill_win_rate_pct','mean_turnover','mean_fills','liquidations','position_violations','team_violations']),
 '', 'Sharpe is session-scaled tick-return Sharpe, not annualized or an iid significance test. Profit factor pools positive/negative FIFO closing-fill P&L across independently matched accounts, including entry/exit fill commissions and rebates, and adds period cancellation/carry expenses to the loss denominator. This is an expense-adjusted closed-fill diagnostic, not a round-trip PF; the unallocated closed-fill PF is also exported. Non-fill expenses are not assigned arbitrarily to individual lots. The report uses overlay FIFO initialized at zero change from starting inventory: selling endowed shares opens a virtual short overlay, and buying them back closes it. This does not submit an actual stock short. It avoids assigning an arbitrary initial reference price to granted shares. Raw journals also preserve the earlier endowment-inclusive FIFO diagnostic, which is not used for this report’s profit factor. N/A means no losses or no closed fills, never an infinite profit claim. “Trades” here means fill messages; partial fills count separately. The exchange values the full position at the best bid/ask without a depth sweep, and own quotes can affect those marks. These are venue MTM results, not guaranteed exit cash. Net MTM includes open holdings; profit factor on closed fills cannot explain all MTM gains. Full fields, costs, slippage, closed counts and provenance counters are in `results/strategy_metrics.csv`.',
 '', 'No-trade earns/loses on granted inventory, so a positive net account P&L alone is not alpha. Buy-and-hold invests 30% across four active equities, capped at 150 shares each; it is not a fully invested broad-market benchmark. Combined sleeves share total starting cash and conservative independent caps; it is a coordinated-capital experiment, not an atomic hedge execution service.',
 '', '## Paired effect of correctness fixes','',table(effect_rows,['week','strategy','seeds','mean_pnl_change','min_pnl_change','max_pnl_change','ci_low','ci_high','mean_fill_change']),'','These differences compare corrected and original gateways on the SAME previously viewed seeds 1000–1049. They measure implementation effects, not new OOS alpha or a newly optimized strategy. A/B/C have exactly unchanged P&L on all 50 paired seeds; only basket changes, by about $0.006 per Week 9 run on average. Therefore the measured A/C gains should not be blamed on the gateway arrival check: their main external-validity concerns are population/fees/exposure. Fixes are retained for defensible order/risk semantics even where mean returns do not improve. The separate buffered-settlement fixture is not mixed into these serialized results.','', '## Stress results','',table([r for r in summary if r['regime']!='base'],['week','strategy','regime','seeds','excess_no_trade','excess_ci_low','excess_ci_high','mean_net_transaction_costs','liquidations']),
 '', 'Fees_60bps doubles taker costs while leaving the real maker schedule unchanged. Latency_3ticks freezes requests for three ticks. Fast_quotes refreshes the synthetic provider each tick, exposing dependence on stale counterparty quotes. No_rebate explicitly removes maker rebates and reports this as a counterfactual, not the venue default. All retain actual matching/risk/order quotas. Spread/depth/TTL/informedness remain confounded in the base generator; `regime_conditions.csv` is descriptive, not a causal spread estimate. Fresh stress seeds are the same 50 IDs for paired comparison, not additional independent days.',
 '', '## Event regime coverage','',table(event_conditions,['week','event_tick','symbol','direction','seeds','strategy','mean_excess']),'','Direction groups condition total trial P&L on one event sign; they do not isolate that event’s causal contribution because each trial contains other shocks. `event_manifest.csv` reconstructs deterministic resolved events from the isolated calendar RNG, validated against four full actual no-trade replays. Resolved directions are used only for this post-run audit and are never passed into the alpha strategies. There are 250 price events across the 50 fresh seeds and two scenarios.','', '## Supplied volatility stress (not independently rerun)','',table([r for r in supplied_stress if r['regime']=='volatile'],['week','strategy','seeds','mean_excess','ci_low','ci_high','net_fees']),'','This archived stress doubles the seed-specific volatility multiplier and magnifies scheduled shocks by 1.5. It is additional supplied evidence, not part of the new frozen 50-seed rerun. Fresh base regimes already span volatility multipliers 0.5, 1, 2 and 4; their conditional outcomes are exported in `volatility_regimes.csv`.','', '## Historical evidence','',table(hd,['file','strategy','net_pnl','excess_no_trade','commissions','slippage','fills','pnl_reproduction_delta']),
 '', 'Replay uses real exchange order handling against reconstructed recorded ladders with funded providers, depth capped at 60 per level and 200 initial stock shares/provider. Historical recordings do not reveal queue identities, future order cancellations, latency or the counterfactual impact of our bot. Therefore market making is deliberately not scored on these tapes. Post-halt and basket inactivity is not successful alpha; futures were unavailable and usable halt/reversal opportunities absent. All three event-momentum days underperform no-trade, even after adding commissions back, so simply reducing fees does not establish historical profitability.',
 '', '## Supplied strategy ablations (not independently rerun)','',table([{'week':r['week'],'variant':r['candidate'],'seeds':r['n_seeds'],'excess_no_trade':r['mean_excess_no_trade'],'net_fees':r['mean_net_fees'],'fills':r['mean_fills']} for r in json.loads((ARCHIVE/'alpha_results/study/ablation_summary.json').read_text())],['week','variant','seeds','excess_no_trade','net_fees','fills']),'','Removing the fee gate changes Week 5 excess by only about $1, and removing trailing costs about $5 on the supplied seeds. Those small deltas do not validate an optimal exit rule. Removing reversal confirmation worsens B from about -$2 to -$36 while increasing fills: extra activity is not extra alpha. Microprice and inventory-skew deltas are small relative to seed dispersion; no independent significance claim is made. Removing the adverse filter increases rebates/turnover, so its higher raw P&L cannot be read as better information prediction.','', '## Code audit: severity and exact functions','','20 findings: 8 High, 11 Medium, 1 Low. These include demonstrated original-venue defects, measurement limitations and external-validity concerns; they are not all claims of realized P&L distortion.']
 for severity,file,func,line,finding,resolution in ISSUES:
  lines+= [f'### {severity}: `{file}:{line}` — `{func}`','',finding+' '+resolution,'']
 lines += ['## Signal-to-accounting trace and verified mechanics','',
 'Strategies observe public events and current visible book, decide, then append the current quote to lagged history. Momentum uses post-event observed prices; halt references are captured from strictly lagged pre-halt history; basket regression uses past returns. No direct future price-array access was found. The current book is observed at tick t; non-contingent orders arrive no earlier than t+1, so they do not fill on their signal-generating observation by construction. Equity strategies are long-only, making negative signals reductions of endowed inventory rather than symmetric stock-short alpha. The original strategy’s calendar percentage describes a future ramp, but it is deliberately public in this simulator; this is simulator-specific privileged predictability, not an undisclosed historical future-price lookup.',
 '', 'Gateway reserves pending exposure before scheduling. The corrected non-contingent payload stays fixed during transport. The actual exchange handler validates and matches price-time priority at resting prices, emits ACK then fills, processes partial fills, IOC expiry and post-only rejection. Cancellation uses actual matching-engine state in this serialized adapter; it does not simulate asynchronous live cancellation uncertainty. Portfolio cash, positions, commissions, rebates, cancellation fees, carry and futures variation margin come from exchange objects. Snapshot/ACK delivery is immediate and reconnect/order recovery is not modeled here.',
 '', 'Week 5 team position limit is ±800 shares per symbol and liquidation threshold is 50% of starting capital. Research caps are at most 150 shares/sleeve, smaller for MM and split combinations; futures cap 20. Entry cash reserve is 60%, gross cap min(40% cash, $70,000), entry equity floor 70%. A 1500-tick window also does not cover the entire scheduled graded hour. These are submission-time constraints, not guarantees against unlimited adverse price moves or exchange faults. A separate verified Week 9 MOC reservation defect allows three 500-share held orders to finish at 1500 shares under a 1000-share cap; this remains unfixed and is not exercised by these strategies. The original venue also has a reproduced matching-before-ACK settlement race: two independent 500-share buys can exceed the team limit. The separate buffered-settlement fixture fixes that isolated order-admission interleaving, but was not silently substituted into the frozen serialized performance runs. Do not treat exchange position rejections as a complete concurrency safety net. The fixed-wire correction may deliver requests during a halt that began after submission; the real exchange must reject them. No strategy knowingly generates new orders for an observed halted symbol.',
 '', 'ARENA10 is an arithmetic price-level average of ten symbols (AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META, NFLX, AMD, INTC), not a tradable equity basket in Week 5. Futures are enabled in Week 9, with margin $40/contract and periodic variation settlement. Basket orders have no atomic multileg facility. Opening auctions run; these strategies wait for continuous trading. No real closing auction is simulated at the horizon. The experiments use one isolated venue despite the scenario enabling multi-venue trading; competing venue books/routing are absent. Only four equities plus eligible ARENA10 receive research population liquidity, so universe coverage is narrow and there is no survivorship-free empirical sample.',
 '', 'The archived `max_gross` field is a maximum individual sleeve exposure, not the simultaneous portfolio aggregate. It remains mislabeled in original outputs and must not be used as a team-wide maximum. Position violation counters sampled at boundaries plus zero liquidations are useful observed diagnostics, not formal every-fill proofs. The corrected zero-crossing risk bug has a regression test; full reconnect/duplicate/out-of-order wire and hedge repair guarantees remain outside this serialized backtest.',
 '', '## Parameter sensitivity and ablations','',
 'The sensitivity figure and archived sensitivity CSV contain all 50 parameter settings on only 12 calibration seeds. A is profitable in calibration at confirmation 3/5 but negative at 10/15; holding/trailing changes are much smaller. This fits the simulator’s 15-tick ramp mechanism. C’s width=1 settings are positive while wider quotes are negative, exposing quote/population sensitivity. B’s z-threshold grid gives identical weak results, so its selected threshold is not evidence of a meaningful optimum. All eight D settings lose; higher thresholds mainly reduce activity. A confirmation=3, hold=120, trailing=2; B z=2.5; C width=1, skew=1.5; D z=3.5/lookback=60 were frozen for this audit. The archive’s 550 ablation runs remove fee gate/trailing, reversal/earnings filtering, microprice/adverse/inventory skew and beta/hedging. They are supplied evidence, not independently reexecuted in this audit. Rebate removal and fast refresh here are newly executed mechanism ablations. No claimed profitable improvement is inferred merely from an in-sample parameter maximum. Many comparisons make unadjusted bootstrap confidence intervals exploratory.',
 '', '## Improvement roadmap (ranked; no untested profit promise)','',
 '1. **Repair execution and risk truth first** (implemented separately, moderate complexity): `CorrectedGateway` removes arrival-time resizing, clips reductions, and resets hedge counters. This prevents false safety/profit inference; the fresh evaluation measures resulting performance, not a claim that correctness improves P&L.',
 '2. **Replace synthetic counterparty assumptions with recorded event/queue evidence** (high complexity, highest research value): extend `MarketPopulation`, receiver journals and historical schema to record order IDs, queue position, arrivals/cancels and fill markouts. Fast-refresh and rebate-off tests measure two dependencies; until queue data exist, market-making alpha remains conditional.',
 '3. **Make event exits fill-aware and classify continuation/reversal** (moderate complexity, untested profitability): pass actual fills into `PositionStrategy.manage`, use execution VWAP/time and trailing volatility; estimate continuation only from observations after the event, with embargoed day-level validation. Historical A losses make this more urgent than adding more parameter combinations.',
 '4. **Require executable basket edge and fail-safe hedge repair** (high complexity, no demonstrated alpha): in `Basket.decide` include both touch spreads, rounded leg weights, costs, current depth, beta uncertainty and a stationarity diagnostic; in the gateway pre-reserve both legs and enforce a bounded repair/unwind state machine. Keep it disabled while expected net residual convergence cannot pay both legs.',
 '5. **Allocate by paired net evidence and run nested chronological validation** (moderate/high complexity, untested live profitability): replace calibration best-setting selection with sparse allocations, confidence bounds and drawdown budgets; cross-validate by complete event/day and independently randomize regime dimensions. Include original, endowed no-trade, terminally matched buy-and-hold, neutral MM and no-rebate baselines. Do not optimize on seeds 3000–3049 after this audit.',
 '', '## Visualizations','',
 '![Preselected seed equity and drawdown](figures/equity_drawdown.png)','',
 '![Seed-level incremental net P&L distribution](figures/seed_distribution.png)','',
 '![Gross versus net cost decomposition](figures/cost_decomposition.png)','',
 '![Calibration parameter sensitivity](figures/calibration_sensitivity.png)','',
 '![Fresh held-out execution and cost stresses](figures/stress_sensitivity.png)','',
 '## Reproduction commands','',
 'Run from the repository root, Python 3.10; matplotlib/numpy available in `.venv310` for report generation. No hosted exchange commands are used.',
 '', '```bash\npython3 audit_review/code/verify_sources.py\npython3 audit_review/code/run_archive_tests.py\npython3 audit_review/code/run_root_tests.py\npython3 -m pytest audit_review/code/test_audit.py audit_review/code/test_exchange_concurrency.py audit_review/code/test_fifo.py -q\npython3 audit_review/code/audit_study.py --stage reproduce --workers 8\npython3 audit_review/code/audit_study.py --stage fresh --workers 8\npython3 audit_review/code/audit_study.py --stage stress --workers 8\npython3 audit_review/code/reproduce_history.py\npython3 audit_review/code/paired_corrections.py\npython3 audit_review/code/event_coverage.py\n.venv310/bin/python audit_review/code/build_report.py\n```',
 '', 'Each study command overwrites its own stage output; preserve existing results before changing definitions. Protocol JSON records exact seeds/settings/code hash. `reproduce_history.py` requires the external checksum-matched `sessions/` files. Main raw output is JSONL with every equity path and fill journal; tables, reproduction deltas and XML are under `results/`. Figures: equity/drawdown, seed distributions, cost decomposition, calibration sensitivity, and stress sensitivity. Original source/bot versions are preserved for comparison. `effective_configuration.json` records a sanitized whitelist of effective fees and risk parameters; `source_verification.json` checks all 39 archived source fingerprints. Raw JSONL journals and extracted inputs are intentionally ignored by Git but remain saved locally.',
 '', '## Evidence limits','',
 'There is no guarantee of full marks, positive live profit or universal risk safety. Synthetic independent seeds are repetitions of one designed venue population, not independent historical market days. Positive bootstrap intervals do not remove structural model error or multiple-testing concerns. No strategy is established as convincing after-cost historical alpha by the available evidence. The original bot remains Week-5-gated on Weeks 2–4 too, so there is no meaningful active-original historical comparison on the supplied earlier-week tapes.']
 (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps(verification,indent=2));print('Fresh ranking:')
 for r in base:print(r['week'],r['strategy'],round(r['excess_no_trade'],2),round(r['net_pnl'],2))
if __name__=='__main__':main()
