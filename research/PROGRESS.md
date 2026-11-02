# Research progress log (resume point for any agent)

Worktree: `/private/tmp/six-rings-research` (branch `research/competition-optimization`, base `e4d8c54`).
**All research files are UNTRACKED/uncommitted** and the worktree lives under `/private/tmp` (volatile). Nothing is pushed.
The main checkout (`castor-phase1-final`) only has the untracked `RESEARCH_MISSION.md`.

## Handoff audit (Codex -> Claude, 2026-11-02)

Verified by inspection, not trust:
- Codex created the branch/worktree and wrote: `BASELINE_AUDIT.md` (reasonable, line-referenced; says baseline metrics "pending"),
  `harness.py` (real `ExchangeServer` + real `MyTrader`/`MyBroker` adapters over a virtual FIFO wire; only transport/halt timers virtualized),
  `strategies.py` (SeasonTrader/SeasonBroker variants), `suite.py` (CLI + registry), `rehearsal.py` (not yet run by me), `test_strategies.py` (11 pass), `SPLITS.json`.
- Registry at handoff: 268 rows. 80 train completed, 10 failed, 52 dev `started` rows that never finished (process killed). Train failures were an
  `AssertionError` in the harness's own accounting check (stale; harness was patched mid-run). Stale `started` rows are left in place (append-only log); ignore rows whose `source_hash` != latest.
- Codex had run NO sealed-test experiments (good: sealed seeds 1001-1100 are untouched). Note: `dev` seed pool 11-70 was added after seeing train results (declared in suite.py).
- Codex's `train` aggregate numbers were not usable (6 seeds, partial, stale code).

## Key findings so far

1. **Baseline trader score is not trading skill.** With the real adapter, `baseline_trader` places 0 orders in a Week-6 run yet scores 0.317 / +$3.4k,
   because the endowed grant positions mark to market. Every comparison must be PAIRED against `cash_*` (same seed, no orders). Raw scores are meaningless.
2. Baseline remains inactive in weeks 6-10 because `_week5_active` is only set for week 5 (confirmed behaviour in harness). Research variants set it for weeks 5-10.
3. **Harness bug fixed (me):** the per-tick net-worth identity check excluded futures; the exchange values futures as (ref-avg_cost)*qty. Weeks 9/10
   (futures_enabled) runs crashed on it, silently dropping those seeds from comparisons. Fixed in `harness.py`.
4. Observation to investigate: season_trader runs in week 9 emit many `ORDER_NOT_FOUND` errors (cancel of already-terminal IOC orders?). Not yet root-caused.
5. Tools added: `research/paired.py` (paired deltas vs cash control, bootstrap over seeds).

## Status of mission deliverables
- BASELINE_AUDIT.md: done by Codex (needs baseline metrics appended from paired runs).
- EXPERIMENT_REGISTRY.csv: in progress (append-only).
- STRATEGY_COMPARISON / ROBUSTNESS_REPORT / BEST_STRATEGY / RUNBOOK / FINAL_DECISION / figures: NOT STARTED.

## Experiment log

### E1 — dev seeds 11-40 (30 paired seeds, weeks rotate 6-10, 1500 ticks, mixed opponents, base stress). `python -m research.paired --stage dev --role trader|broker`
Deltas are candidate minus `cash_*` control on the same seed. CI = bootstrap over seeds. Dev stage = tuning pool, NOT out-of-sample.

| trader | dScore | 95% CI | P(>0) | dPnL | DQ runs |
|---|---|---|---|---|---|
| baseline_trader (control; inactive wk6-10) | +0.038 | [+0.010,+0.071] | 0.33 | +357 | 0 |
| season_trader (activation fixed wk5-10) | +0.102 | [+0.061,+0.140] | 0.87 | +1232 | 0 |
| short_trader | +0.119 | [+0.088,+0.153] | 0.93 | +1269 | 4 |
| aggressive_trader (2x exposure) | +0.117 | [+0.078,+0.156] | 0.87 | +2078 | 2 |
| combined_trader | +0.105 | [+0.062,+0.145] | 0.87 | +1293 | 0 |
| ipo_trader | +0.143 | [+0.094,+0.189] | 0.97 | +1540 | 0 |
| imbalance_trader / reversion_trader | 0.000 | | | 0 | 0 (never trade: no signal fires, negative/null result) |

| broker | dScore | 95% CI | dPnL | DQ runs |
|---|---|---|---|---|
| baseline_broker | 0.000 | | 0 | 0 (identical to cash: places no quotes in harness; unexplained, see next steps) |
| season_broker | +0.000 | | +5 | 0 |
| passive_broker | -0.004 | [-0.011,+0.003] | -20 | 1 |
| skew_broker | +0.001 | [-0.006,+0.009] | +115 | 2 |
| aggressive_broker | +0.011 | [-0.017,+0.037] | -74 | **16 of 30** |

Interpretation (honest):
- Biggest lever is simply running the trader in weeks 6-10 (baseline is gated off). Gain ~+0.06 over baseline, ~+$900 PnL per run on a $175k seat; small in absolute terms.
- Short/aggressive/IPO variants are not distinguishable from season_trader at n=30 (CIs overlap heavily). No promotion yet. IPO gain depends on the simulator's private-fundamental distribution; unverified vs real data.
- Broker: no variant shows a credible edge; maker rebates are tiny ($20-117/run) and offset by adverse selection. `aggressive_broker` fails accounting in 16/30 runs — disqualified until root-caused.
- DQ rows flagged `DISQUALIFIED accounting/liquidation` in registry (trader: short 4, aggressive 2) need root-cause too; they are NOT excluded from the table above.

## Next steps
1. (done: E1 above)
2. Root-cause ORDER_NOT_FOUND; check broker `DISQUALIFIED accounting` rows seen in Codex's train runs.
3. Broker: confirm passive post-only quoting actually rests and fills (maker ratio, rebates) vs IOC baseline.
4. Parameter search on dev seeds only; freeze finalists; validation seeds 101-106 -> expand; sealed 1001-1100 ONCE.
5. Local WebSocket rehearsal (`research/rehearsal.py`) for finalists. Upgrades (Family H), cross-venue (D) still untouched.
