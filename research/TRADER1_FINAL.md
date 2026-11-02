# Trader 1 final report

**Recommendation: CONDITIONAL GO for further validation, NOT yet for deployment.** The strongest defensible Trader 1 is `event2_trader` (defaults) - but its edge over the corrected baseline is modest, and the largest verified win is simply activating the trader in Weeks 6-10 with the futures guard.

## Comparison (sealed seeds 1001-1100, 100 paired runs each, official `score_equity`)
| # | configuration | dScore vs cash [95% CI] | dPnL | max DD p95 | liquidations | failure rate |
|---|---|---|---|---|---|---|
| 1 | Current deployed Trader 1 | +0.022 [+0.007,+0.036] | +$311 | 1.28% | 0/100 | 0/100 (inactive wk6-10) |
| 2 | Corrected baseline (activation + futures guard) | +0.113 [+0.093,+0.134] | +$1,294 | 0.68% | 0/100 | 0/100 |
| 3 | Best event-driven (event2, long-only) | +0.063 [+0.053,+0.075] | +$1,272 | 1.28% | 0/100 | 0/100; worse than #2 |
| 4 | Best long/short (event2_trader) | **+0.157 [+0.134,+0.182]** | **+$2,242** | 0.79% | 0/100 | 0/100 |
| 5 | Best aggressive capital allocation | none beat the default after multiple-testing correction (adj.p 0.28) | - | - | - | - |
| 6 | Best combined | not built (signal study found no complementary signal; IPO untested) | - | - | - | - |
event2_trader vs corrected baseline: **+0.044 [+0.022,+0.068]**, P(win a seed)=0.74, dPnL +$947. vs deployed: +0.136 [+0.107,+0.164].

## What is verified
- Real engine + real adapters, accounting identity checked every tick, 0 accounting failures on dev/validation/sealed base conditions.
- Validation (+0.034 vs corrected baseline) and sealed (+0.044) agree; dev overstated it (selection/design on dev).
- Robust vs cash under fees x2, high vol, competitive opponents, latency.

## What is NOT established
- Superiority under thin liquidity or high latency (event2 <= corrected baseline there).
- Benefit in Weeks 8-9 (zero) - gains come from weeks 6, 7, 10.
- Any real-market transfer; any live WebSocket behavior; interaction with the second trader seat's and the broker's share of the team position limit.
- IPO participation, overshoot fade, sizing models, lead-lag.

## Before any deployment (requires user authorization; nothing was deployed)
1. Port the futures guard into production `MyTrader` and update the pinned hashes in `tests/test_alpha.py` with a documented reason.
2. Activate weeks 6-10 (`_week5_active` gate) - this alone captures most of the verified gain (+0.09 vs deployed).
3. Decide per-seat position cap (research used 400 shares; deployed 150).
4. Run `research/rehearsal.py` against an isolated local venue; root-cause the latency-stress cash discrepancy.

Reproduce: `python -m research.suite batch --stage sealed --count 100 --ticks 1500 --names cash_trader,baseline_trader,season_trader,event2_trader,event2_long_trader` (results already in the registry; do NOT re-tune on these seeds), then `python -m research.paired --stage sealed --role trader [--control season_trader]`. Raw artifacts are backed up outside Git in ~/Desktop/FINM_33500/six-rings-research-backup/.
