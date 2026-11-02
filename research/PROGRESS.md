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

## P0 root-causes (milestone 2)
- **ORDER_NOT_FOUND**: NOT a defect. Trader: deliberate IOC terminal-confirmation handshake (`team/trader.py` ~570-612): after each OrderAck it cancels the IOC and treats ORDER_NOT_FOUND as proof the IOC finished. One per order. Broker: cancel-twice protocol (`cancel_quotes`). Costs 2 messages/order; matters for Week 7+ message quotas.
- **Trader accounting failures (weeks 9/10 only)**: REAL defect in the control. Econ-print plan covers ALL symbols incl. ARENA10, a cash-settled future; the share-only ledger/risk then diverges from exchange cash by ~$7-8k. Fix kept OUT of `team/*` (test_alpha pins live source hashes; control preserved): research variants refuse `NON_SHARE_SYMBOLS` in `research/strategies.py`. Deployed MyTrader would hit this if ever activated in wk9/10 -> must carry the guard before any deployment.
- **Broker 'positions mismatch'**: harness artifact ({sym:0} vs missing key). Fixed.
- **Futures identity**: harness assertion mirrors `Portfolio.net_worth` (server.py:284-307), which is exactly what `snapshot_equity` scores (server.py:3719, ref_prices). Verified against official source.
- **Baseline broker == cash**: `_graded_week5 = (week == 5)` in `team/broker.py` (lines 111,117) makes `_ready()` false in weeks 6-10. Deployed broker never quotes after week 5. Regression test added (characterization).
- **Baseline trader**: same gating via `_week5_active`; 0 orders in wk 6-10 (the +0.317 raw score is endowment drift).
- **imbalance/reversion never fire**: by construction. imbalance edge <= mid/ask-1+0.003 < 2*taker+0.002 threshold (unreachable); reversion needs a 1.2% dislocation. They are null because of rule design, not evidence. Replaced by a direct predictiveness study (`research/signal_study.py`, 12 seeds, observation only):
  - OBI and microprice: corr with 5/20-tick forward mid return ~0 (|corr|<0.011, se ~0.007-0.018) -> REJECTED (Family C).
  - Short-term momentum exists (|5-tick return| in 20-40bp -> +17bp fwd; 40-100bp -> +39bp; >100bp -> +67bp signed fwd 5 ticks) but this is the scripted event ramp; round-trip cost is ~30bp taker fees + ~19bp spread => only the >100bp bucket nets positive and it is rare (~20 obs/run). Event strategy already captures it via announcements.
- **Harness validity bug (big)**: bots close the socket on any uncorrelated exchange error (e.g. POST_ONLY_CROSS) and the harness never reconnected, so passive-broker results were truncated (transport_closed in 18/35 passive, 35/35 skew runs). Harness now emulates the production reconnect: venue sweeps resting orders, 3s wait, re-handshake replay (books, leaderboard, fees, SESSION_OPEN late_join, MARKET_STATE). All earlier passive-broker numbers (E1) are INVALID.
- Earlier harness runs mixed code versions inside one batch when the harness file was edited mid-batch: do not edit research/ during a batch (source_hash is taken at batch start only).
- New broker variants: touch_broker / improve_broker / touch_skew_broker (post-only at/inside observed touch from public book snapshots via a read-only Tee on the wire).

## PRIORITY OVERRIDE (user): Trader 1 first. Broker work paused.
Broker sweep was stopped after 3 of 19 configs (results preserved in registry/backup; partial, NOT analysed, NOT validated). Earlier broker E1 numbers are invalid (see P0). Resume only after Trader 1 concludes.
Commits: 061ec3c (checkpoint 1), 216312f (P0 fixes + checkpoint 2). Backups: ~/Desktop/FINM_33500/six-rings-research-backup/.

### Trader 1 findings
- Event structure (week json + calendar.py): direction is NOT announced in advance, but the fired CALENDAR_EVENT carries signed pct/ramp_ticks/overshoot, so direction is public at fire time. In a traced run the message arrived ~12 ticks after the scheduled tick, when the reference had begun to move; price then ramps to overshoot*pct (3-8% moves) and settles back.
- Baseline (activation-fixed) `season_trader` leaves capital idle: cap 150 shares/symbol and one 60-share IOC at a time; e.g. INTC at $22 bought 130 shares (~$2.9k) on a +5% move. Real limiter is displayed depth (top of book 1-30 shares) -> entries are depth-limited, not only cap-limited.
- New `event2_trader` / `event2_long_trader` (research/strategies.py `EventTrader`): notional-budget sizing, long+short on the signed move, depth-aware IOC chunks, exit at fraction of overshoot/time/stop; futures excluded; SSR/shorts-gate respected.

### E2 — dev seeds 11-50 (40 paired seeds, wk 6-10 rotating, 1500 ticks, mixed opponents), same code hash, 0 failed / 0 DQ
| variant | dScore vs cash | 95% CI | P(>0) | dPnL | worst paired dScore |
|---|---|---|---|---|---|
| baseline_trader (deployed, inactive wk6-10) | +0.034 | [+0.009,+0.062] | 0.30 | +296 | -0.08 |
| season_trader (activation fixed, futures guard) | +0.105 | [+0.073,+0.137] | 0.88 | +1310 | -0.21 |
| short_trader | +0.114 | [+0.087,+0.142] | 0.93 | +1322 | -0.06 |
| event2_long_trader | +0.067 | [+0.049,+0.087] | 0.85 | +993 | -0.00 |
| event2_trader (long+short, default params) | +0.191 | [+0.149,+0.235] | 0.93 | +2382 | -0.10 |
Caveats: dev pool (not OOS); event2 design was informed by tracing ONE dev seed (11); unadjusted CIs; shorts account for most of the gain (long-only +0.067).
Protocol (frozen before search): random search (24 configs) on dev seeds 11-30 -> max-t bootstrap (research/maxboot.py) -> top configs confirmed on dev seeds 31-70 -> independent validation seeds 101-160 -> sealed 1001-1100 once.

### E3 — random search (24 configs + default, 20 dev seeds 11-30) + max-t bootstrap (research/rsearch.py, research/maxboot.py)
- Versus cash every config is significant (family effect is real on dev).
- Versus the DEFAULT event2_trader, selection-adjusted: best config +0.022 (adj.p 0.28; max-t critical 3.82). NO tuned config beats the default; 15/24 are lower, several clearly worse (low ev_take 0.6, tiny notional, ev_entry 0.3). Larger notional/caps do not help (entry is depth-limited). Conclusion: keep default parameters; the search is a negative result, not a source of improvement.
### FROZEN before validation (2026-11-02): candidates = event2_trader (defaults), event2_long_trader, season_trader (corrected baseline), baseline_trader (deployed), cash_trader (control). No parameter changes after this line. Validation seeds 101-160 (60 paired, weeks rotate 6-10). Sealed 1001-1100 untouched until final freeze.

### E4 — VALIDATION, seeds 101-160 (60 independent paired seeds, frozen candidates, base stress, mixed opponents). 300/300 completed, 0 liquidations, 0 accounting errors.
| variant | dScore vs cash | 95% CI | P(>0) | dPnL | vs season_trader (corrected baseline) | vs deployed baseline_trader |
|---|---|---|---|---|---|---|
| baseline_trader (deployed) | +0.027 | [+0.010,+0.045] | 0.28 | +355 | -0.070 | 0 |
| season_trader | +0.097 | [+0.074,+0.119] | 0.92 | +1204 | 0 | +0.070 [+0.048,+0.092] |
| event2_long_trader | +0.059 | [+0.045,+0.075] | 0.85 | +1526 | -0.037 [-0.057,-0.019] | +0.033 |
| **event2_trader** | **+0.130** | [+0.103,+0.158] | 0.92 | **+2328** | **+0.034 [+0.012,+0.057], P(>0)=0.62, dPnL +1124** | **+0.103 [+0.077,+0.132], P(>0)=0.83, dPnL +1973** |
Reading: dev -> validation shrinkage (event2 +0.191 -> +0.130). Edge over the corrected baseline is real but modest and unreliable per seed (P(>0)=0.62, worst -0.17); edge over the deployed bot is large mostly because the deployed bot is inactive in wk 6-10. Long-only event2 is WORSE than the corrected baseline on score -> the short leg is what adds value.
Per-run score is per single week (25 snapshots); a season score pools weeks, so absolute levels differ; only paired deltas are meaningful.

### E5 — stress (validation seeds 101-130, 30 paired; each condition has its own cash control)
| condition | event2 dScore [95% CI] | dPnL | season_trader dScore | note |
|---|---|---|---|---|
| base/mixed (60 seeds) | +0.130 [+0.103,+0.158] | +2328 | +0.097 | |
| competitive opponents | +0.171 [+0.125,+0.221] | +2951 | +0.095 | |
| thin liquidity (passive_mm+noise only) | +0.045 [+0.028,+0.064] | +850 | +0.075 | **event2 WORSE than season_trader** |
| fees x2 | +0.119 [+0.076,+0.166] | +2115 | +0.070 | |
| latency +1.0s | +0.076 [+0.050,+0.102] | +1152 | +0.064 | 2 event2 + 1 season runs flagged accounting (see below) |
| volatile (fundamental vol x2) | +0.099 [+0.050,+0.147] | +2093 | +0.061 | worst paired dScore -0.26 |
Open item (NOT root-caused): the 3 latency-stress runs flagged `cash: ours vs exchange` differ by $50-380 (0.03-0.2%) at end of run with a SYMBOL_HALTED reject; probably in-flight fills at end under +1s latency, but unproven. Zero liquidations anywhere.
### SEALED RUN started (once): seeds 1001-1100, frozen candidates only (cash, baseline, season, event2, event2_long). No changes after this.

### E6 — SEALED (1001-1100, run once, 100/100 completed, 0 liq/0 accounting errors): event2 +0.157 vs cash; +0.044 [+0.022,+0.068] vs corrected baseline (P(>0)=0.74, +$947); +0.136 vs deployed. By week edge vs season: wk6 +.026, wk7 +.064, wk8 +.003, wk9 -.002, wk10 +.128. SEALED SEEDS NOW CONSUMED for these candidates; any new candidate needs new sealed seeds (e.g. 2001-2100).
Trader 1 deliverables written: research/TRADER1_{BASELINE,STRATEGIES,OPTIMIZATION,ROBUSTNESS,FINAL}.md + TRADER1_EXPERIMENTS.csv.

## Next steps (updated after E6)
0. Open Trader 1: overshoot-fade, IPO validation, thin-liquidity weakness, weeks 8-9 (no edge), latency cash discrepancy, run rehearsal.py, port futures guard to production with documented hash-pin update (needs authorization). Broker work paused.
1. (done: E1 above)
2. Root-cause ORDER_NOT_FOUND; check broker `DISQUALIFIED accounting` rows seen in Codex's train runs.
3. Broker: confirm passive post-only quoting actually rests and fills (maker ratio, rebates) vs IOC baseline.
4. Parameter search on dev seeds only; freeze finalists; validation seeds 101-106 -> expand; sealed 1001-1100 ONCE.
5. Local WebSocket rehearsal (`research/rehearsal.py`) for finalists. Upgrades (Family H), cross-venue (D) still untouched.
