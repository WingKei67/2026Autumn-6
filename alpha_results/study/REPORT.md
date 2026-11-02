# Offline alpha results

Rankings use held-out after-fee excess over a paired no-trade account. Absolute account P&L also includes endowed shares and cash interest. Robustness labels apply only to the seeded generator; the historical diagnostics do not validate a live edge.
Calibration: 12 seeds. Evaluation: 50 disjoint seeds. 1500 ticks/run; starting cash $175,000; taker fee 30 bps.

## Out-of-sample results

| Week | Strategy | Mean account P&L | Mean excess | Excess 95% CI | Beats no-trade | Mean fills | Worst DD |
|---|---|---:|---:|---|---:|---:|---:|
| 5 | original | $2078.32 | $1587.43 | [1300.68, 1886.78] | 98% | 27.4 | 0.96% |
| 5 | event_momentum | $701.26 | $210.38 | [181.74, 239.94] | 100% | 10.8 | 0.56% |
| 5 | combined | $653.89 | $163.01 | [131.29, 195.56] | 94% | 11.6 | 0.60% |
| 5 | market_making | $620.68 | $129.80 | [33.28, 225.85] | 64% | 300.7 | 0.94% |
| 5 | basket (future disabled) | $490.89 | $0.00 | [0.00, 0.00] | 0% | 0.0 | 0.64% |
| 5 | no_trade | $490.89 | $0.00 | [0.00, 0.00] | 0% | 0.0 | 0.64% |
| 5 | post_halt | $488.74 | $-2.14 | [-3.88, -0.77] | 4% | 0.8 | 0.64% |
| 5 | buy_hold | $-48.01 | $-538.89 | [-1095.81, 9.63] | 42% | 10.2 | 2.56% |
| 9 | market_making | $725.64 | $180.31 | [90.38, 269.36] | 78% | 357.6 | 0.68% |
| 9 | event_momentum | $671.77 | $126.44 | [107.34, 146.41] | 98% | 11.4 | 0.46% |
| 9 | combined | $645.72 | $100.39 | [81.08, 120.67] | 98% | 11.7 | 0.49% |
| 9 | no_trade | $545.33 | $0.00 | [0.00, 0.00] | 0% | 0.0 | 0.53% |
| 9 | original (Week 5 gate) | $545.33 | $0.00 | [0.00, 0.00] | 0% | 0.0 | 0.53% |
| 9 | post_halt | $544.45 | $-0.89 | [-2.56, 0.62] | 4% | 0.4 | 0.53% |
| 9 | basket | $540.00 | $-5.34 | [-7.56, -3.25] | 0% | 1.5 | 0.53% |
| 9 | buy_hold | $234.28 | $-311.05 | [-802.40, 161.60] | 40% | 10.2 | 2.76% |

## Base-run costs and risk statistics

| Week | Strategy | Total net fees | Cancellation charges | Decision-mid slippage | Session Sharpe | Turnover | Closing-fill win rate |
|---|---|---:|---:|---:|---:|---:|---:|
| 5 | original | $404.35 | $0.00 | $259.94 | 1.53 | 0.70 | 71.4% |
| 5 | event_momentum | $79.80 | $0.00 | $19.64 | 1.75 | 0.14 | 53.9% |
| 5 | combined | $67.68 | $0.00 | $15.70 | 1.58 | 0.12 | 52.4% |
| 5 | market_making | $-142.01 | $0.00 | $-141.79 | 1.56 | 0.84 | 68.0% |
| 5 | basket | $0.00 | $0.00 | $0.00 | 1.65 | 0.00 | n/a |
| 5 | no_trade | $0.00 | $0.00 | $0.00 | 1.65 | 0.00 | n/a |
| 5 | post_halt | $3.30 | $0.00 | $0.50 | 1.65 | 0.01 | 39.3% |
| 5 | buy_hold | $133.90 | $0.00 | $43.75 | -0.00 | 0.23 | n/a |
| 9 | market_making | $-157.79 | $47.21 | $-193.75 | 2.28 | 1.17 | 69.7% |
| 9 | event_momentum | $66.09 | $0.00 | $16.75 | 1.73 | 0.11 | 57.6% |
| 9 | combined | $56.94 | $0.00 | $13.78 | 1.61 | 0.10 | 57.0% |
| 9 | no_trade | $0.00 | $0.00 | $0.00 | 2.23 | 0.00 | n/a |
| 9 | original | $0.00 | $0.00 | $0.00 | 2.23 | 0.00 | n/a |
| 9 | post_halt | $1.72 | $0.00 | $0.26 | 2.23 | 0.00 | 50.0% |
| 9 | basket | $1.58 | $0.00 | $0.16 | 2.21 | 0.00 | 23.5% |
| 9 | buy_hold | $133.91 | $0.00 | $43.78 | 0.32 | 0.23 | n/a |

## Stress tests

| Week | Strategy | Regime | Mean excess | 95% CI | Liquidations | Position violations |
|---|---|---|---:|---|---:|---:|
| 5 | original | fees_60bps | $1241.71 | [989.82, 1504.76] | 0 | 0 |
| 5 | event_momentum | fees_60bps | $116.70 | [86.18, 146.79] | 0 | 0 |
| 5 | market_making | fees_60bps | $114.47 | [18.72, 210.56] | 0 | 0 |
| 5 | combined | fees_60bps | $86.05 | [56.35, 115.71] | 0 | 0 |
| 5 | basket | fees_60bps | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | no_trade | fees_60bps | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | post_halt | fees_60bps | $-4.90 | [-8.60, -1.90] | 0 | 0 |
| 5 | buy_hold | fees_60bps | $-673.20 | [-1230.09, -124.71] | 0 | 0 |
| 5 | original | latency_3ticks | $808.70 | [628.26, 992.74] | 0 | 0 |
| 5 | market_making | latency_3ticks | $78.22 | [-16.83, 172.02] | 0 | 0 |
| 5 | event_momentum | latency_3ticks | $52.60 | [34.31, 72.14] | 0 | 0 |
| 5 | combined | latency_3ticks | $37.33 | [21.58, 54.18] | 0 | 0 |
| 5 | basket | latency_3ticks | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | no_trade | latency_3ticks | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | post_halt | latency_3ticks | $-1.58 | [-4.06, 1.41] | 0 | 0 |
| 5 | buy_hold | latency_3ticks | $-542.72 | [-1098.12, 3.65] | 0 | 0 |
| 5 | original | volatile | $1719.58 | [1376.51, 2079.02] | 0 | 0 |
| 5 | market_making | volatile | $162.78 | [25.09, 297.98] | 0 | 0 |
| 5 | event_momentum | volatile | $114.31 | [88.88, 141.22] | 0 | 0 |
| 5 | combined | volatile | $90.73 | [66.77, 117.25] | 0 | 0 |
| 5 | basket | volatile | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | no_trade | volatile | $0.00 | [0.00, 0.00] | 0 | 0 |
| 5 | post_halt | volatile | $-0.49 | [-1.29, 0.28] | 0 | 0 |
| 5 | buy_hold | volatile | $-232.94 | [-941.94, 471.81] | 0 | 0 |
| 9 | market_making | fees_60bps | $165.19 | [75.18, 254.71] | 0 | 0 |
| 9 | event_momentum | fees_60bps | $47.26 | [26.80, 68.65] | 0 | 0 |
| 9 | combined | fees_60bps | $33.22 | [15.98, 52.39] | 0 | 0 |
| 9 | no_trade | fees_60bps | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | original | fees_60bps | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | basket | fees_60bps | $-2.47 | [-4.36, -0.91] | 0 | 0 |
| 9 | post_halt | fees_60bps | $-2.61 | [-5.83, -0.05] | 0 | 0 |
| 9 | buy_hold | fees_60bps | $-445.36 | [-936.72, 27.30] | 0 | 0 |
| 9 | market_making | latency_3ticks | $117.41 | [28.53, 208.29] | 0 | 0 |
| 9 | event_momentum | latency_3ticks | $39.21 | [25.78, 53.68] | 0 | 0 |
| 9 | combined | latency_3ticks | $29.49 | [17.24, 42.98] | 0 | 0 |
| 9 | no_trade | latency_3ticks | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | original | latency_3ticks | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | post_halt | latency_3ticks | $-1.58 | [-4.63, 0.62] | 0 | 0 |
| 9 | basket | latency_3ticks | $-4.79 | [-6.80, -2.91] | 0 | 0 |
| 9 | buy_hold | latency_3ticks | $-311.67 | [-801.69, 160.07] | 0 | 0 |
| 9 | market_making | volatile | $156.59 | [28.27, 284.47] | 0 | 0 |
| 9 | event_momentum | volatile | $140.78 | [114.19, 168.39] | 0 | 0 |
| 9 | combined | volatile | $116.67 | [90.03, 143.96] | 0 | 0 |
| 9 | no_trade | volatile | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | original | volatile | $0.00 | [0.00, 0.00] | 0 | 0 |
| 9 | post_halt | volatile | $-0.46 | [-3.27, 3.14] | 0 | 0 |
| 9 | basket | volatile | $-27.98 | [-40.50, -17.42] | 0 | 0 |
| 9 | buy_hold | volatile | $-290.17 | [-948.64, 356.51] | 0 | 0 |

## Ablations (held-out seeds; no retuning)

| Week | Variant | Mean excess | 95% CI | Mean fills |
|---|---|---:|---|---:|
| 5 | event_momentum:ablation:no_fee_gate | $211.63 | [183.14, 240.63] | 10.8 |
| 5 | event_momentum:ablation:no_trailing | $204.86 | [175.20, 234.04] | 10.8 |
| 5 | market_making:ablation:no_adverse_filter | $140.02 | [32.81, 244.00] | 355.9 |
| 5 | market_making:ablation:no_microprice | $123.79 | [27.23, 220.09] | 300.8 |
| 5 | market_making:ablation:no_inventory_skew | $110.24 | [-25.33, 239.80] | 297.8 |
| 5 | post_halt:ablation:no_earnings_filter | $-2.14 | [-3.88, -0.77] | 0.8 |
| 5 | post_halt:ablation:no_reversal | $-35.94 | [-46.03, -25.46] | 6.1 |
| 9 | basket:ablation:fixed_beta | $-2.81 | [-4.60, -1.26] | 0.8 |
| 9 | basket:ablation:unhedged | $-4.66 | [-6.63, -2.82] | 0.8 |

## Profitability and robustness ranking

Week 5 (safety first; positive CI in base and all three stresses outranks an unstable mean):
1. event_momentum: $210.38 mean excess; positive CI in base and all stresses.
2. market_making: $129.80 mean excess; positive mean but not robust.
3. post_halt: $-2.14 mean excess; lost versus no-trade.

Week 9 (safety first; positive CI in base and all three stresses outranks an unstable mean):
1. market_making: $180.31 mean excess; positive CI in base and all stresses.
2. event_momentum: $126.44 mean excess; positive CI in base and all stresses.
3. post_halt: $-0.89 mean excess; lost versus no-trade.
4. basket: $-5.34 mean excess; lost versus no-trade.


## Independent profit-ranked portfolio confirmation

Momentum plus market making was chosen by calibration mean excess, following the primary study. It is evaluated on 50 new seeds (2000–2049), not reused primary-study seeds. All sleeves share one total capital allocation and team constraints.

| Week | Strategy | Regime | Mean account P&L | Mean excess | Excess 95% CI |
|---|---|---|---:|---:|---|
| 5 | original | base | $2617.13 | $1845.54 | [1457.12, 2257.59] |
| 5 | combined_profit | base | $1119.32 | $347.73 | [261.63, 429.73] |
| 5 | market_making | base | $993.29 | $221.70 | [103.33, 341.61] |
| 5 | event_momentum | base | $965.95 | $194.36 | [169.14, 219.70] |
| 5 | no_trade | base | $771.59 | $0.00 | [0.00, 0.00] |
| 5 | original | fees_60bps | $2199.14 | $1427.55 | [1080.18, 1799.72] |
| 5 | combined_profit | fees_60bps | $1043.66 | $272.06 | [190.29, 349.72] |
| 5 | no_trade | fees_60bps | $771.59 | $0.00 | [0.00, 0.00] |
| 5 | original | latency_3ticks | $1598.75 | $827.16 | [588.41, 1078.22] |
| 5 | combined_profit | latency_3ticks | $962.63 | $191.04 | [105.50, 274.02] |
| 5 | no_trade | latency_3ticks | $771.59 | $0.00 | [0.00, 0.00] |
| 5 | original | volatile | $2674.57 | $1718.41 | [1268.69, 2231.44] |
| 5 | combined_profit | volatile | $1252.79 | $296.62 | [198.64, 397.14] |
| 5 | no_trade | volatile | $956.16 | $0.00 | [0.00, 0.00] |
| 9 | combined_profit | base | $996.30 | $317.58 | [234.71, 401.14] |
| 9 | market_making | base | $911.53 | $232.82 | [135.16, 332.30] |
| 9 | event_momentum | base | $808.50 | $129.78 | [111.85, 148.57] |
| 9 | no_trade | base | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | original | base | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | combined_profit | fees_60bps | $929.84 | $251.12 | [171.94, 332.52] |
| 9 | no_trade | fees_60bps | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | original | fees_60bps | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | combined_profit | latency_3ticks | $886.82 | $208.11 | [138.27, 279.23] |
| 9 | no_trade | latency_3ticks | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | original | latency_3ticks | $678.72 | $0.00 | [0.00, 0.00] |
| 9 | combined_profit | volatile | $1145.27 | $358.85 | [257.90, 462.60] |
| 9 | no_trade | volatile | $786.43 | $0.00 | [0.00, 0.00] |
| 9 | original | volatile | $786.43 | $0.00 | [0.00, 0.00] |

## Secondary historical taker replay

This is not an exact queue reconstruction. Maker performance is intentionally not estimated. These earlier-week tapes contain no halt/resume pair and do not enable ARENA10 futures. Week 4 is chronologically later than Weeks 2–3. These tapes were inspected during engineering and are supplementary diagnostics, not a pristine historical holdout. No historical tape is used for parameter selection.

| Recording | Strategy | Account P&L | Excess versus no-trade | Fills |
|---|---|---:|---:|---:|
| session_week02_20261009_204000.jsonl.gz | basket | $1057.47 | $0.00 | 0 |
| session_week02_20261009_204000.jsonl.gz | buy_hold | $2034.19 | $976.71 | 317 |
| session_week02_20261009_204000.jsonl.gz | event_momentum | $907.37 | $-150.10 | 95 |
| session_week02_20261009_204000.jsonl.gz | no_trade | $1057.47 | $0.00 | 0 |
| session_week02_20261009_204000.jsonl.gz | post_halt | $1057.47 | $0.00 | 0 |
| session_week03_20261016_204000_market.jsonl | basket | $777.98 | $0.00 | 0 |
| session_week03_20261016_204000_market.jsonl | buy_hold | $-673.55 | $-1451.53 | 300 |
| session_week03_20261016_204000_market.jsonl | event_momentum | $734.97 | $-43.01 | 21 |
| session_week03_20261016_204000_market.jsonl | no_trade | $777.98 | $0.00 | 0 |
| session_week03_20261016_204000_market.jsonl | post_halt | $777.98 | $0.00 | 0 |
| session_week04_20261023_204025_market.jsonl | basket | $400.85 | $0.00 | 0 |
| session_week04_20261023_204025_market.jsonl | buy_hold | $469.80 | $68.95 | 226 |
| session_week04_20261023_204025_market.jsonl | event_momentum | $171.01 | $-229.84 | 82 |
| session_week04_20261023_204025_market.jsonl | no_trade | $400.85 | $0.00 | 0 |
| session_week04_20261023_204025_market.jsonl | post_halt | $400.85 | $0.00 | 0 |

## Interpretation and limits

* Positive account P&L is not automatically alpha. Use excess over the paired no-trade account, which holds the same initial grants and earns the same configured cash interest.
* Synthetic seeds are independent price/event/flow draws, not 50 independent historical trading days. External liquidity and taker behavior are modeled; actual hosted profitability is unproven.
* The actual order book supplies price-time priority, partial fills, stale resting quotes and cancellations; there is no touch-price fill shortcut. Commands execute no sooner than the next tick, before a new decision.
* Futures are disabled in Week 5. Week 9 is a separate common-universe comparison; the original Week 5 bot intentionally remains inactive outside Week 5.
* All equity strategies retain long-only stock inventory, per-seat caps, a 60% projected cash reserve and min(40% capital, $70k) gross bound at new-risk entry. Subsequent fees, price moves and futures variation can change those balances. The basket can short a capped cash-settled future in Week 9. Hedge legs are non-atomic and delayed; hedge failures and residual overlays are recorded.
* P&L is conservatively marked by the real exchange. Fees/rebates/carry and actual cancellation charges are included once, not subtracted again. The fee audit replays maker runs, checks identical P&L/fill counts, and extracts authoritative total charges; trade commissions remain separately available. Slippage is a separate diagnostic relative to the decision mid. Sharpe is a session statistic, not annualized. Win rate is for FIFO closing fills including costs.
* Baseline no-trade receives no special liquidation at the end. Trading sleeves try to return to their initial inventory during the final 12 ticks; halts or lack of liquidity can prevent this, and remaining overlay is reported.
* Bootstrap intervals resample paired seeds, conditional on the generator. Multiple strategies are compared; the intervals are descriptive and are not a guarantee or a multiple-testing-adjusted discovery claim.
* No hosted connection or live order is used. The proven live bot files and .env are unchanged.


## Diagnosis

The synthetic generator preserves the exchange calendar ramps and a simplified liquidity population. Success under those seeds is conditional on that structure, not proof of prediction in a live market.

Historical momentum diagnostics (same order paths, fees added back only for cost attribution):

| Recording | After-fee excess | Fill commissions | Excess plus those commissions |
|---|---:|---:|---:|
| session_week02_20261009_204000.jsonl.gz | $-150.10 | $75.23 | $-74.87 |
| session_week03_20261016_204000_market.jsonl | $-43.01 | $12.54 | $-30.47 |
| session_week04_20261023_204025_market.jsonl | $-229.84 | $73.52 | $-156.32 |

Adding fees back does not rerun a zero-fee strategy or remove market impact/cash-interest differences. Here the historical deficit is not explained by commissions alone; delayed price confirmation did not produce enough subsequent continuation on these recorded books.

Post-halt reversion has few eligible trades: the actual five-minute halt is much longer than the default 15-tick news ramp, and the permanent-earnings filter excludes most large displacements. The remaining trades do not establish a positive edge.

Market-making gains depend substantially on rebates and the modeled taker population. The historical tapes cannot verify its maker queues, and the Week 5 latency interval crosses zero. Fee-insensitive simulated flow is a material assumption.

Basket residual z-scores do not establish a stationary relationship. After the cost gate, integer futures sizing and non-atomic legs, the few executed trades have negative average excess. There is no profitable basket result to promote.

The original Week 5 bot remains the strongest tested Week 5 baseline. The new strategies and profit-ranked portfolio are not installed into the live bots. No new strategy has demonstrated historical-validated profitability from these inputs.
