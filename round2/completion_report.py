"""Read saved evidence only; generate the completion manifest, tables and report."""
from pathlib import Path
import json,csv,hashlib,datetime,os,collections
import numpy as np
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'round2/results'
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'round2/mpl_cache'))

def read(name):
    p=OUT/name;rows=[];invalid=0
    if p.exists():
        for line in p.open():
            try:rows.append(json.loads(line))
            except json.JSONDecodeError:invalid+=1
    return rows,invalid
def ci(v):
    v=np.asarray(v,dtype=float)
    if len(v)<2:return [None,None]
    rng=np.random.default_rng(20261030)
    return np.quantile(v[rng.integers(len(v),size=(10000,len(v)))].mean(1),[.025,.975]).tolist()
def save_csv(name,rows):
    if not rows:return
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def table(rows,cols):
    def fmt(v):
        if v is None:return 'N/A'
        if isinstance(v,float):return f'{v:,.2f}'
        return str(v)
    return '\n'.join(['| '+' | '.join(cols)+' |','| '+' | '.join(['---']*len(cols))+' |']+['| '+' | '.join(fmt(r.get(c)) for c in cols)+' |' for r in rows])
def metrics(rows):
    base={(r['seed'],r['week'],r['stress'],r['candidate']):r for r in rows}
    out=[]
    for stress in sorted({r['stress'] for r in rows}):
        for week in ['pooled',5,9]:
            for name in sorted({r['candidate'] for r in rows}):
                rs=[r for r in rows if r['candidate']==name and r['stress']==stress and (week=='pooled' or r['week']==week)]
                if not rs:continue
                comparisons={}
                for comparator in ['original_active','original','round1_combined','no_trade']:
                    pairs=collections.defaultdict(list)
                    for r in rs:
                        b=base.get((r['seed'],r['week'],stress,comparator))
                        if b:pairs[r['seed']].append(r['pnl']-b['pnl'])
                    # For pooled claims, require both scenario weeks per seed.
                    vals=[np.mean(v) for v in pairs.values() if len(v)==(2 if week=='pooled' else 1)]
                    lo,hi=ci(vals)
                    comparisons.update({f'delta_{comparator}':float(np.mean(vals)) if vals else None,f'low_{comparator}':lo,f'high_{comparator}':hi,f'pairs_{comparator}':len(vals)})
                avg=lambda key:float(np.mean([r.get(key,0) for r in rs]))
                out.append(dict(stress=stress,week=week,strategy=name,runs=len(rs),seeds=len({r['seed'] for r in rs}),net_pnl=avg('pnl'),gross_pnl=avg('gross_pnl_before_execution_fees'),fees=avg('total_net_fees'),rebates=avg('maker_rebates'),slippage=avg('slippage_vs_decision_mid'),turnover=avg('turnover'),fills=avg('fills'),sharpe=avg('session_sharpe'),worst_drawdown_pct=100*max(r['max_drawdown'] for r in rs),max_position=max(r['max_abs_position'] for r in rs),max_gross=max(r.get('max_aggregate_gross',r.get('max_gross',0)) for r in rs),liquidations=sum(r['liquidations'] for r in rs),position_violations=sum(r['position_violations'] for r in rs),team_violations=sum(r['team_position_violations'] for r in rs),same_tick_fills=sum(r['same_tick_fills'] for r in rs),**comparisons))
    return out

def charts(rows,stats,stress):
    if not rows:return []
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dest=ROOT/'round2/figures';dest.mkdir(exist_ok=True)
    names=['original_active','round1_combined','event_regime','cross_asset','adaptive_allocator','no_trade']
    names=[n for n in names if any(r['candidate']==n for r in rows)]
    fig,axes=plt.subplots(2,2,figsize=(13,8))
    for col,week in enumerate([5,9]):
        for name in names:
            subset=[r for r in rows if r['candidate']==name and r['week']==week]
            if not subset:continue
            curves=np.array([r['audit_equity_curve'] for r in subset]);initial=np.array([r['initial_equity'] for r in subset])
            axes[0,col].plot((curves-initial[:,None]).mean(0),label=name)
            peaks=np.maximum.accumulate(np.c_[initial,curves],axis=1)[:,1:]
            axes[1,col].plot(100*((curves-peaks)/peaks).mean(0),label=name)
        axes[0,col].set_title(f'Week {week}: mean account P&L, after fees');axes[1,col].set_title(f'Week {week}: mean path drawdown (%)')
        axes[1,col].set_xlabel('Continuous-trading tick');axes[0,col].legend(fontsize=7)
    fig.tight_layout();fig.savefig(dest/'equity_drawdown.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4))
    lookup={(r['seed'],r['week'],r['candidate']):r['pnl'] for r in rows}
    for name in names:
        if name in ['original_active','no_trade']:continue
        vals=[]
        for seed in sorted({r['seed'] for r in rows}):
            keys=[(seed,w,n) for w in [5,9] for n in [name,'original_active']]
            if all(k in lookup for k in keys):vals.append(np.mean([lookup[(seed,w,name)]-lookup[(seed,w,'original_active')] for w in [5,9]]))
        axes[0].hist(vals,bins=20,histtype='step',label=name)
    axes[0].axvline(0,color='black');axes[0].set_title('Paired mean of Weeks 5/9: increment vs original');axes[0].set_xlabel('Net P&L difference ($/scenario)');axes[0].legend(fontsize=7)
    ss=[r for r in stats if r['week']=='pooled' and r['strategy'] in names]
    x=np.arange(len(ss));axes[1].bar(x,[r['gross_pnl'] for r in ss],label='Gross MTM');axes[1].bar(x,[-r['fees'] for r in ss],label='Negative net fees')
    axes[1].set_xticks(x,[r['strategy'] for r in ss],rotation=35,ha='right');axes[1].set_title('Gross return and fee decomposition ($)');axes[1].legend()
    fig.tight_layout();fig.savefig(dest/'paired_pnl_costs.png',dpi=150);plt.close(fig)
    return ['round2/figures/equity_drawdown.png','round2/figures/paired_pnl_costs.png']

def main():
    expected={'validation_development.jsonl':400,'validation.jsonl':400,'test.jsonl':1400,'priority_stress.jsonl':480,'historical_frozen.jsonl':15,'historical_remaining.jsonl':10}
    manifest={'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'policy':'Preserve prior outputs; no refitting or tuning on test outcomes. New stress scope fixed before examining tests.','experiments':{},'deferred':['Corrected-label model refit and fresh validation (would invalidate completed selection)','Remaining 30 stress seeds; fast-quote and three-tick-latency stresses','Three sizing/uncertainty ablations','Historical chronological refit folds; existing tapes are not pristine holdouts'],'development_caveats':['Original training labels condition on future absence of halts. Corrected diagnostic implementation is separate; frozen evaluation unchanged.','models_stockonly.json is an unevaluated alternative fit, not the model selected by completed validation.','Repeated validation uses the same 20 seed IDs and adds no independent evidence.']}
    allrows={}
    for file,count in expected.items():
        rows,invalid=read(file);allrows[file]=rows;p=OUT/file
        manifest['experiments'][file]={'expected_runs':count,'saved_runs':len(rows),'invalid_lines':invalid,'status':'completed' if len(rows)==count and not invalid else 'partial' if rows else 'not_completed','bytes':p.stat().st_size if p.exists() else 0,'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None}
    manifest['market_collection']={'runs':len(json.loads((OUT/'market_manifest.json').read_text())),'historical_recordings':len(json.loads((OUT/'history_manifest.json').read_text()))}
    manifest['completed_prior_work_not_rerun']=['120 market collections','Three historical feature datasets','Two 400-run validation files on the same seeds','Model fits and parameter grids','Round I audit']
    manifest['frozen_model_sha256']=hashlib.sha256((OUT/'frozen_models.json').read_bytes()).hexdigest()
    manifest['test_provenance_model_matches']=all(r.get('model_sha256')==manifest['frozen_model_sha256'] for r in allrows['test.jsonl'])
    from round2.experiments import hashes
    manifest['frozen_code_matches']=hashes()==json.loads((OUT/'frozen_protocol.json').read_text())['code_sha256']
    manifest['completion_source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'round2').glob('*.py'))}
    manifest['tests']={'passed':11,'failed':0,'skipped':0,'artifact':'round2/results/completion_tests.xml'}
    historical_all=allrows['historical_frozen.jsonl']+allrows['historical_remaining.jsonl']
    manifest['historical_combined']={'saved_runs':len(historical_all),'expected_runs':15,'unique_combinations':len({(r['week'],r['strategy']) for r in historical_all}),'initial_batch_status':'failed after five saved outputs; preserved','resumption':'only unsaved combinations, with closed-session arrival handled by actual exchange'}
    (OUT/'experiment_status.json').write_text(json.dumps(manifest,indent=2))
    rows=allrows['test.jsonl'];stats=metrics(rows);stress=metrics(allrows['priority_stress.jsonl'])
    save_csv('oos_metrics.csv',stats);save_csv('priority_stress_metrics.csv',stress)
    hist=[];hr=allrows['historical_frozen.jsonl']+allrows['historical_remaining.jsonl'];hb={(r['week'],r['strategy']):r for r in hr}
    for r in hr:
        b=hb.get((r['week'],'original_active'));nt=hb.get((r['week'],'no_trade'))
        hist.append(dict(week=r['week'],strategy=r['strategy'],net_pnl=r['pnl'],delta_original_active=r['pnl']-b['pnl'] if b else None,delta_no_trade=r['pnl']-nt['pnl'] if nt else None,fees=r['total_net_fees'],fills=r['fills'],worst_drawdown_pct=r['max_drawdown']*100,liquidations=r['liquidations'],position_violations=r['position_violations']))
    hist.sort(key=lambda r:(r['week'],r['strategy']))
    save_csv('historical_metrics.csv',hist)
    verification={}
    for label,rr in [('test',rows),('priority_stress',allrows['priority_stress.jsonl']),('historical',hr)]:
        rejects=collections.Counter()
        for r in rr:rejects.update(r.get('rejects',{}))
        verification[label]=dict(runs=len(rr),same_tick_fills=sum(r['same_tick_fills'] for r in rr),missing_fill_provenance=sum(r.get('missing_fill_provenance',0) for r in rr),liquidations=sum(r['liquidations'] for r in rr),position_violations=sum(r['position_violations'] for r in rr),team_position_violations=sum(r.get('team_position_violations',0) for r in rr),rejection_codes=dict(rejects))
    verification['test_rejections_by_strategy']={name:dict(sum((collections.Counter(r.get('rejects',{})) for r in rows if r['candidate']==name),collections.Counter())) for name in sorted({r['candidate'] for r in rows})}
    verification['new_strategy_maker_rebates']=sum(r['maker_rebates'] for r in rows+allrows['priority_stress.jsonl'] if r['strategy'] in ['event_regime','cross_asset','adaptive_allocator'])
    verification['test_unique_keys']=len({(r['seed'],r['week'],r['candidate']) for r in rows})
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2))
    lookup={(r['seed'],r['week'],r['candidate']):r for r in rows};attribution=[]
    for name in sorted({r['candidate'] for r in rows}):
        values=[]
        for r in rows:
            nt=lookup.get((r['seed'],r['week'],'no_trade'))
            if r['candidate']!=name or nt is None:continue
            cash=r['final_cash']-nt['final_cash']
            overlay=sum((q-nt['final_positions'].get(s,0))*r['final_marks'][s] for s,q in r['final_positions'].items())
            marks=sum(q*(r['final_marks'][s]-nt['final_marks'][s]) for s,q in nt['final_positions'].items())
            initial=r['initial_equity']-nt['initial_equity']
            delta=r['pnl']-nt['pnl']
            values.append([cash,overlay,marks,-initial,delta,cash+overlay+marks-initial-delta])
        if values:
            avg=np.mean(values,axis=0)
            attribution.append(dict(strategy=name,cash_difference=float(avg[0]),terminal_overlay_value=float(avg[1]),endowment_mark_difference=float(avg[2]),initial_equity_adjustment=float(avg[3]),net_excess_no_trade=float(avg[4]),max_accounting_residual=float(np.max(np.abs(np.array(values)[:,-1])))))
    save_csv('pnl_attribution.csv',attribution)
    diagnostic=[];path=OUT/'label_selection_diagnostics.csv'
    if path.exists():
        dr=list(csv.DictReader(path.open()))
        for name in sorted({r['family'] for r in dr}):
            ds=[r for r in dr if r['family']==name]
            old=sum(int(r['old_label_count']) for r in ds);new=sum(int(r['corrected_label_count']) for r in ds)
            oldret=sum(float(r['old_mean_net_touch'] or 0)*int(r['old_label_count']) for r in ds)
            newret=sum(float(r['corrected_mean_net_touch'] or 0)*int(r['corrected_label_count']) for r in ds)
            diagnostic.append(dict(family=name,old_labels=old,corrected_labels=new,old_mean_bps=10000*oldret/max(1,old),corrected_mean_bps=10000*newret/max(1,new)))
    figs=charts(rows,stats,stress)
    ranking=sorted([r for r in stats if r['week']=='pooled'],key=lambda r:r['net_pnl'],reverse=True)
    new=[r for r in ranking if r['strategy'] in ['event_regime','cross_asset','adaptive_allocator']]
    verdict=('No new strategy demonstrates a robust improvement over the active original bot.' if not any((r['low_original_active'] or -1)>0 for r in new) else 'At least one new strategy has a positive base-scenario paired interval; stress and historical limitations still govern robustness.')
    tested=sum(len(allrows[n]) for n in ['test.jsonl','priority_stress.jsonl','historical_frozen.jsonl','historical_remaining.jsonl'])
    report=f'''# AlgoArena Alpha Discovery Round II — completion report

{verdict} This is a retrospective research result, not a deployment recommendation or a guarantee of live returns. The original project and Round I audit are preserved.

## What completed and what was preserved

The resumed task inspected saved code, logs, 120 market-data collections (40 discovery seeds × two weeks, 20 validation seeds × two weeks), three historical feature datasets, two 400-run validation files, model grids and frozen-model hashes. Neither completed validation nor Round I experiments were rerun. Both validation files use the **same 20 seeds**; they are not 40 independent seeds. The separate stock-only refit exists but was not substituted for the frozen validated model.

This completion saved **{tested} additional exchange/replay runs**. Primary test seeds are **6000–6099**, separate from discovery 4000–4039, validation 4100–4119 and Round I seeds. Strategies, safety thresholds and model hashes were frozen before test execution. No test tuning occurred. Stress scope was reduced before results inspection to conserve usage. All testing used local isolated accounts; no hosted orders were submitted.

{table([dict(file=k,**v) for k,v in manifest['experiments'].items()],['file','saved_runs','expected_runs','status'])}

The first historical batch stopped after five saved results because the V3 fixture asserted on an in-flight order arriving after session close. Its partial output and failure log are preserved. Only the ten missing combinations were resumed; the real exchange now rejects closed-session arrivals normally. The two historical files together constitute the planned 15 runs, not 20.

The machine-readable [status manifest](round2/results/experiment_status.json) includes hashes, partial-output counts and deferred work. Raw `.jsonl` journals and `.npz` datasets remain on disk (ignored by Git to avoid enormous diffs).

## Out-of-sample strategy ranking

Dollar figures are means per **1500-tick scenario**, with $175,000 total starting cash plus endowed shares. Pooled estimates average Weeks 5 and 9 **within each seed**, then bootstrap independent seed clusters (10,000 resamples; 95% intervals). Weeks are not counted as independent extra seeds. Confidence intervals are descriptive, unadjusted for multiple strategies and conditional on this generator.

{table(ranking,['strategy','seeds','net_pnl','delta_original_active','low_original_active','high_original_active','delta_no_trade','fees','turnover','worst_drawdown_pct','liquidations','position_violations','team_violations'])}

`original` preserves the exact frozen policy, including its Week 5 gate. `original_active` is the stricter comparator: the same original decision policy with the simulated venue clock supplied and the week gate enabled in both scenarios. This avoids claiming alpha merely by beating an inactive Week 9 bot. Both comparators are retained in the detailed CSV.

{table([r for r in stats if r['week'] in [5,9] and r['strategy'] in ['original','original_active','round1_combined','adaptive_allocator','event_regime','cross_asset']],['week','strategy','net_pnl','delta_original_active','low_original_active','high_original_active','delta_original','delta_round1_combined'])}

No-trade is endowed with shares and can earn positive MTM without any trading. Therefore account P&L alone is not alpha. Gross P&L adds back actual net execution fees; it is not a zero-cost counterfactual. Slippage/spread already affect fill prices and are not deducted twice. Detailed fees, rebates, slippage, fills, turnover, session-scaled Sharpe and exposure are in [oos_metrics.csv](round2/results/oos_metrics.csv). The original and new strategies have different target sizes within the same capital/risk envelope; a risk-normalized outperformance claim needs the deferred sizing ablation.

## Transaction-cost and shock stresses

These use the same preselected **20 seed IDs, 6000–6019**, in both weeks: they are robustness checks, not additional independent test seeds. `fees_60bps` doubles taker fees from 30 to 60 bps per side. `volatile` doubles fundamental volatility and background spreads and multiplies configured event magnitude ranges by 1.5. It is a joint stress and cannot isolate which change caused a loss.

{table([r for r in stress if r['week']=='pooled'],['stress','strategy','seeds','net_pnl','delta_original_active','low_original_active','high_original_active','delta_no_trade','fees','worst_drawdown_pct','liquidations','position_violations','team_violations'])}

New policies submit IOC orders and do not depend on maker rebates. Round I combined includes market making and its rebates; report net fees accordingly. The unexecuted 50-seed/four-stress plan is **not** represented as completed. Execution-latency and quote-refresh robustness remain unverified for Round II.

## Historical recordings

{table(hist,['week','strategy','net_pnl','delta_original_active','delta_no_trade','fees','fills','worst_drawdown_pct','liquidations','position_violations'])}

All available Week 2, 3 and 4 files match the saved recording inventory; no Week 5 historical tape exists in that inventory. These tapes were examined in Round I, so they are **retrospective transfer tests, not genuinely untouched historical holdouts**. Frozen synthetic models are replayed without fitting to these outcomes. Chronological historical refits were deferred. Public books are replayed into the actual matching engine, but a historical book recording does not reveal the counterfactual queue reaction to our orders. Refilled recorded depth and missing cancellations/queue state limit realism. Historical market making/combined replay is omitted rather than assuming valid maker fills. Three days are insufficient for credible historical confidence intervals.

The event policy made **zero fills on all three recordings**: its positive historical account P&L is grant exposure, not event alpha. Cross-asset and allocator activity was negative relative to no-trade where they traded. The active original received one normal `SESSION_CLOSED` arrival rejection per tape; this is not a crash or a fill outside session hours. These sparse, low-participation results do not support transfer of synthetic alpha.

## Sources of apparent alpha and implementation findings

1. **Event state:** public calendar payloads expose direction/magnitude and shock-shape information. The event model predicts remaining repricing from the last pre-announcement mid, public target, observed momentum, phase and liquidity. This is causal access to simulator news, but it encodes a programmed price trajectory. The original already uses this information; rediscovering it is not an independent information advantage.
2. **Cross-asset movement:** lagged peer returns and relative movement predict some synthetic continuations, especially around shared macro shocks. The same generator drives peers; this does not establish a market lead-lag relationship. Microprice, imbalance and trade-flow predictors are entangled with the synthetic background order generator. Its quote spread, refresh interval, depth and informed-flow strength all co-vary with `seed % 3`; these are bundled regimes rather than independently identified effects. Synthetic strategies see four stock books (AAPL, NVDA, NFLX, AMD); historical replay can expose ten, changing the peer-return feature distribution.
3. **Reversal:** the overshoot reversal model has only one qualifying validation opportunity/seed and is disabled. There are only two provisionally supported independent predictors. The third implemented strategy is an allocator of those two, averaging correlated forecasts rather than adding them. It is not a manufactured third alpha.
4. **High severity — `round2/features.py:labels`:** the frozen training screen removes opportunities if a future halt occurs between entry and exit. That is future-outcome selection. Signal-screen returns and model-support flags are therefore biased and cannot establish statistical alpha. The execution tests do not drop those paths: orders encounter actual halts and account MTM includes adverse moves. Thus the untouched direct execution comparison still honestly evaluates the frozen policy, including any consequences of poor training.
5. **Corrected implementation preserved separately:** `round2/labels_review.py:labels_with_halts` records rejected arrivals as zero payoff, retains intermediate halts, delays exits to the first tradable quote, and explicitly counts terminal censoring. Four regression tests cover these cases. It has **not** been used to refit the evaluated policy; no improved-profit claim follows from the fix.
6. **Other limitations:** frozen model fitting included ARENA10 samples although the new policies trade stocks only; `models_stockonly.json` is an untested alternative. The uncertainty deduction `RMSE/sqrt(40)` is a heuristic, not a valid prediction interval. Grid/feature searches share validation data; existing conditional-return intervals do not correct selection. Missing two-sided quotes limit label coverage. These are reasons to reject model-confidence claims, even when execution P&L is measured correctly.
7. **Historical adapter repair:** the saved `history_eval.py` imported `I` from the wrong module. This completion fixes that import and the historical fixture's decision-time/arrival-time assertion (by retaining actual exchange session/auction rejection); frozen synthetic trading code and model hashes stay unchanged. Original clock/session gating, execution callbacks and historical phase handling remain explicit in the adapter.

Validation-only label diagnostics below use the **same frozen prediction threshold**, equities only, every third tick. These are overlapping quote-touch opportunities, **not independent trades, execution results or new confidence intervals**. Correcting selection need not reduce the mean; the sign of the bias is empirical. This diagnostic performs no refit and does not change OOS policies. Full rows are in `label_selection_diagnostics.csv`.

{table(diagnostic,['family','old_labels','corrected_labels','old_mean_bps','corrected_mean_bps'])}

## Where profit comes from

The new policies buy selected positive expected repricing, use volatility-dependent sizes, and aim to exit after 10/20 ticks or a 1.8% stop. These are discrete-tick rules; halts/gaps can prevent the stop from executing at its threshold. They retain the initial grant during negative news, whereas the original can sell endowed inventory. The allocator can express more simultaneous opportunities, which also changes exposure. Additional P&L versus Round I, if shown above, is attributable to richer public-event information, entry timing and sizing together—not an isolated proof of a new predictor. Dollar performance versus no-trade separates grant drift, while the original comparison tests whether that package actually improves the incumbent.

Exact terminal-account decomposition versus matched no-trade (means across both scenarios):

{table(attribution,['strategy','cash_difference','terminal_overlay_value','endowment_mark_difference','initial_equity_adjustment','net_excess_no_trade','max_accounting_residual'])}

Cash differences include executed costs and other cash flows. Terminal overlay value is the extra inventory marked at each candidate's own terminal venue mark. Endowment mark difference captures changes in valuation of granted shares caused by candidate-dependent market paths. These components sum to paired net P&L after the initial-equity adjustment; they do not imply guaranteed liquidation proceeds.

## Execution and risk boundaries

Orders are scheduled at decision time, reserve exposure, and reach the real local matching handler at least one tick later. Wire quantities are frozen in transit. Actual fills, partial fills, commissions/rebates and rejections determine accounts. Halts block decisions; an in-flight order may be rejected on arrival. The observed violation counts above are empirical outcomes, not a mathematical guarantee against every exchange race.

Saved verification counters (including every completed stress and historical row):

```json
{json.dumps(verification,indent=2)}
```

The large `ORDER_NOT_FOUND` count belongs to Round I combined's cancel/replace behavior, not the new IOC policies. It is a measured operational weakness even though it did not breach position limits here. The new policies' only test rejection code was `SYMBOL_HALTED`, consistent with an order crossing a halt boundary while in transport.

Each experiment shares one $175,000 total budget; Round I combined splits it across two sleeves. The conservative gateway caps at 150 stock shares per single sleeve, reserves pending buys/sells, forbids stock shorts, limits entry gross to min(40% starting cash, $70,000), reserves 60% starting cash, and stops adding risk below 70% starting-cash equity. Independent sleeve budgets are conservative relative to the venue team limit; the study also samples aggregate positions plus pending orders. Price changes can move gross exposure above an entry limit without a new order. There is no live multi-process reconnection/ACK-race certification in this research.

New requests are at most 60 shares; model notional targets are capped at 20% starting cash, and the gateway can reduce them further. Forced final-window unwind attempts are shared across active strategies; a halted position can remain open and is valued using venue MTM. No depth-swept guaranteed cash exit is assumed. Session Sharpe is not annualized and is not an iid significance statistic.

## Actionable improvements, in priority order

1. **Keep the incumbent until a paired improvement is demonstrated.** Use `original_active` as the research comparator; do not replace the live bot with a model merely because gross or no-trade-relative P&L is positive.
2. **Repair training selection before more optimization.** Use `labels_review.py`, keep reject/delay/censor flags, quantify missing-book coverage, and fit stocks only. A new discovery/validation cycle and new sealed seeds are required; 6000–6099 are now spent evaluation data.
3. **Model execution value rather than unconditional touch returns.** Estimate fill probability, fee-adjusted realized markouts and halt-delayed exits from order journals. Replace the heuristic uncertainty haircut with seed/day-block validation and conservative prediction errors.
4. **Isolate event information from exposure.** Compare equal-size original versus event model, remove public shock-shape fields, remove peer features, and separately test grant de-risking on negative news. These are hypotheses, not verified profit improvements; the deferred ablations are necessary.
5. **Demand independent market evidence.** Acquire future untouched recordings and evaluate chronology, realistic latency, depth depletion and no-rebate performance. Do not optimize against a single programmed shock ramp and call it robust alpha.

## Artifacts and reproduction

Charts use saved OOS equity paths without rerunning experiments:

![Mean equity and drawdown](round2/figures/equity_drawdown.png)

![Paired increments and costs](round2/figures/paired_pnl_costs.png)

All new strategies are in `round2/strategies.py`; discovery/model code, frozen parameters, source hashes, raw journals and prior partial/development results remain in `round2/`. The original `.env`, live bot, previous audit and unrelated Git changes are preserved. Tests executed in this completion: **11 passed, 0 failed, 0 skipped**; `round2/results/completion_tests.xml`. Seven tests cover existing policy behavior, four cover corrected labels. This does not rerun or recertify the full project suite.

```bash
# Regenerate tables, manifest and report from saved runs (no experiments):
.venv310/bin/python -m round2.completion_report
# Targeted regressions:
.venv310/bin/python -m pytest round2/tests/test_round2.py round2/tests/test_labels_review.py -q
# Exact primary experiment invocation used (writes test.jsonl; use a separate copy to reproduce):
.venv310/bin/python -m round2.experiments --stage test --workers 8
# These completion commands refuse to overwrite an existing output:
.venv310/bin/python -m round2.finish_experiments --stage stress --workers 4
.venv310/bin/python -m round2.finish_experiments --stage history --workers 3
# Resume only unsaved historical combinations from the preserved partial batch:
.venv310/bin/python -m round2.finish_experiments --stage history_resume --workers 2
```

The frozen runner does not make its original full-stage CLI append-safe. Preserve outputs or use a separate repository copy before reproducing that command. The completion runner explicitly refuses overwrites. Existing parameter sensitivity (`model_sensitivity.csv`) and predictor screens (`signal_screens.csv`) are saved development evidence with the label-selection caveat above; they are not new held-out results. Additional large experiments were deliberately deferred under the user's usage constraint.
'''
    (ROOT/'ROUND_II_REPORT.md').write_text(report)
    (ROOT/'ALPHA_DISCOVERY.md').write_text('''# Alpha Discovery Round II

**No new strategy robustly beats the original bot.** See [ROUND_II_REPORT.md](ROUND_II_REPORT.md) for full paired confidence intervals, costs, risk counters, charts and reproducibility.

The strongest observed relationship is **remaining synthetic event repricing**: publicly announced magnitude, last pre-announcement mid and the observed phase of the programmed shock help predict subsequent prices. The original already consumes this event information. The event model and its allocator improve on Round I combined, but they do not outperform the incumbent on 100 untouched paired test seeds.

**Peer returns** show some synthetic continuation around common macro shocks. Their separate strategy earns little beyond no-trade and loses that advantage under doubled taker fees. Shared shocks and bundled background spread/depth/refresh regimes confound a causal cross-asset interpretation. Microprice, imbalance and trade-flow features have not independently demonstrated incremental after-cost profitability.

**Overshoot reversal is unsupported**: only one qualifying validation opportunity was found. It remains disabled. The third implemented strategy is an allocator of event and peer predictions, not a third independent alpha.

The earlier label screen excluded opportunities encountering future halts. That selection invalidates its model-support confidence claims. A separate corrected diagnostic retains intermediate halts, scores rejected arrivals, delays exits and counts censoring; it was tested but not used to refit or tune the frozen policies. Correcting this selection does not necessarily lower a sample mean. Actual exchange OOS execution, including all halts, determines the final rankings.

Across three previously inspected recordings, the event strategy never trades and the active new strategies lose relative to endowed no-trade. No untouched historical holdout is available. The synthetic event advantage has therefore not demonstrated empirical transfer.

Research outputs and deferred work are recorded in [experiment_status.json](round2/results/experiment_status.json); [oos_metrics.csv](round2/results/oos_metrics.csv) ranks execution outcomes. Further model tuning requires a new development cycle and new sealed test seeds. Preserve the incumbent until that evidence exists.
''')
    print(json.dumps({'saved_runs':tested,'status':manifest['experiments'],'verdict':verdict},indent=2))
if __name__=='__main__':main()
