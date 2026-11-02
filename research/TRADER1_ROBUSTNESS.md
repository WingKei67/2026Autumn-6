# Trader 1 robustness

## Sealed test (seeds 1001-1100, run once, 100/100 completed per variant, 0 liquidations, 0 accounting errors)
| variant | dScore vs cash [95% CI] | P(>0) | dPnL vs cash | dScore vs corrected baseline | max DD mean / p95 / max |
|---|---|---|---|---|---|
| baseline_trader (deployed) | +0.022 [+0.007,+0.036] | 0.27 | +311 | -0.092 | 0.55% / 1.28% / 1.63% |
| season_trader (corrected) | +0.113 [+0.093,+0.134] | 0.89 | +1294 | 0 | 0.26% / 0.68% / 1.40% |
| event2_long_trader | +0.063 [+0.053,+0.075] | 0.83 | +1272 | -0.050 [-0.066,-0.034] | 0.58% / 1.28% / 1.97% |
| **event2_trader** | **+0.157 [+0.134,+0.182]** | 0.94 | **+2242** | **+0.044 [+0.022,+0.068], P(>0)=0.74, dPnL +947** | 0.33% / 0.79% / 1.01% |
event2 vs deployed baseline: +0.136 [+0.107,+0.164], P(>0)=0.89, dPnL +1930. Worst paired delta vs corrected baseline: -0.32.
By week (sealed, 20 seeds each, event2 minus season_trader): wk6 +0.026, wk7 +0.064, wk8 +0.003, wk9 -0.002, wk10 +0.128. Edge is concentrated in weeks 6, 7, 10; weeks 8-9 show none.

## Validation (101-160): event2 +0.130 vs cash, +0.034 [+0.012,+0.057] vs corrected baseline (see PROGRESS.md E4). Dev (11-50): +0.191 -> shrinkage on fresh data.

## Stress (validation seeds 101-130; PROGRESS.md E5): event2 stays positive vs cash in every condition (competitive +0.171, fees x2 +0.119, volatile +0.099, latency +1s +0.076, thin liquidity +0.045). **In thin liquidity and under +1s latency its advantage over season_trader disappears or reverses (thin: +0.045 vs +0.075).**

## Known limits
- Simulator generalization only: no real historical tape contains these events (round-2 reports found none usable), so there is no empirical out-of-sample evidence from real data.
- Per-run score is one week; the season score pools weeks.
- 3 latency-stress runs flagged small ledger-vs-exchange cash differences ($50-380), not root-caused.
- Futures guard exists only in research classes; deployed MyTrader would trade ARENA10 if activated for weeks 9-10.
- Team-wide position limit: event2 uses cap_shares=400 per symbol; deployed seats cap at 150 (`SEAT_POSITION_CAP`) and a second trader seat shares the same team limit. Live use needs an explicit allocation decision.
- Not exercised: the real WebSocket path (research/rehearsal.py exists but was not run), hosted rate limits/quotas.
