# Alpha research changes

Existing `team/trader.py`, `team/broker.py`, `.env`, registration, and account
state were not overwritten. Existing unrelated working-tree changes remain.
The real exchange implementation was inspected and reused, not replaced.

## Reusable strategies

| Path | Purpose |
|---|---|
| `team/alpha/__init__.py` | Strategy registry; no live startup side effects. |
| `team/alpha/core.py` | Causal observations/intents, rolling histories, position and exit plans. |
| `team/alpha/event_momentum.py` | Post-public-event, price-confirmed momentum with explicit cost gate. |
| `team/alpha/post_halt.py` | Lagged-reference reversion after an observed halt/resume and reversal. |
| `team/alpha/market_making.py` | Post-only microprice/imbalance/trend quotes with inventory/volatility controls. |
| `team/alpha/basket.py` | Lagged beta/residual spread trading with a real ARENA10 futures hedge. |

## Offline exchange harness

| Path | Purpose |
|---|---|
| `alpha_lab/__init__.py` | Offline package definition. |
| `alpha_lab/data.py` | Historical recording inventory and safe market-data readers. |
| `alpha_lab/exchange.py` | Real exchange with in-memory transport and virtual halt timers. |
| `alpha_lab/risk.py` | Conservative reservation gateway, share/cash/gross/margin limits, delayed cancel/hedge execution. |
| `alpha_lab/baselines.py` | Actual original bot adapter, constrained buy-and-hold, no-trade. |
| `alpha_lab/metrics.py` | MTM P&L, trade fees, slippage, Sharpe, drawdown, turnover, closing-fill win rate and safety metrics. |
| `alpha_lab/runner.py` | Isolated accounts/roster/persistence, real auctions/calendar/impact/carry, seeded external population. |
| `alpha_lab/experiment.py` | Full parameter grids, frozen calibration selection, disjoint-seed batches, CSV/JSON exports. |
| `alpha_lab/history.py` | Low-level historical taker-replay machinery; production entry points use the funded/phase-aware adapter below. |

## Commands and dependencies

| Path | Purpose |
|---|---|
| `scripts/__init__.py` | Regular package to avoid the third-party `scripts` namespace collision exposed by full-suite testing. |
| `scripts/alpha_backtest.py` | Calibration, primary OOS, ablations, stresses, illustrative paths and historical workflow. |
| `scripts/alpha_report.py` | Paired-seed confidence intervals, rankings, cost/risk tables, diagnoses and five PNG charts. |
| `scripts/alpha_portfolio.py` | Profit-ranked portfolio confirmation on new seeds 2000–2049, with resume checks. |
| `scripts/alpha_history.py` | Funded, bounded background inventory and recorded session-phase enforcement for historical replay. |
| `scripts/alpha_fee_audit.py` | Authoritative maker cancellation-charge extraction, requiring reproduced P&L/fill counts. |
| `requirements-alpha.txt` | Existing requirements plus Matplotlib; no change to live dependencies. |
| `.gitignore` | Ignore report font caches and transient runner PID files. |

## Tests and documentation

* `tests/test_alpha.py` — 24 cases covering causal signals, fee gates, halt
  eligibility, lagged references, earnings filtering, actual futures gating,
  next-tick execution, FIFO queue priority, partial fills, rebates, stale
  post-only rejection, cancellation races, concurrent reservations, partial
  hedge sizing, cash/stock-short constraints, reproducibility, historical
  funding/session phases and unchanged live source hashes.
* `tests/conftest.py` — isolate the trader ledger directory before constructing
  test bots; never load a real seat's saved ledger into an isolated test.
* `ALPHA_README.md` — exact commands, design, constraints, assumptions and limitations.
* `ALPHA_FILES.md` — this manifest.

## Executed results

`alpha_results/study/` is the final study. `alpha_results/development/` contains
superseded engineering/calibration artifacts and is excluded from conclusions.

* `protocol.json`, `frozen_selection.json`, `combined_definition.json` — source,
  scenario, seed/cash/fee protocol and frozen parameters.
* `calibration.{csv,json}` — 624 calibration runs; `sensitivity.{csv,json}` — all 50 parameter combinations.
* `out_of_sample.{csv,json}` — 800 primary held-out runs.
* `ablations.{csv,json}` — 550 held-out ablation runs.
* `stress.{csv,json}` — 2,400 stress runs at doubled fees, three-tick latency, and higher volatility/events/spreads.
* `portfolio_confirmation/` — 500 base and 900 stress runs on 50 fresh seeds, with an explicit portfolio definition.
* `historical.{csv,json}` and `historical_funded.{csv,json}` — the same 15 final funded, phase-aware historical taker replays.
* `fee_audit.{csv,json}` — 450 full maker-run replays with matching P&L/fill counts and actual cancellation charges.
* `data_inventory.json` — recording coverage, market/event counts, observed spread/move statistics and file checksums.
* `example_curves.json` — eight fixed-seed illustrative paths/fill details, not selected winners.
* `validation.json` — independent seed splits, dataset counts, safety totals and unchanged live-source checks.
* `summary.{csv,json}`, `rankings.json`, `conditions.json`, `ablation_summary.json`, `portfolio_confirmation_summary.json` — report data.
* `REPORT.md` — numerical conclusions, all comparisons, costs, limitations and negative historical diagnostics.
* `oos_profitability.png`, `stress_profitability.png`, `calibration_sensitivity.png`, `example_equity.png`, `portfolio_confirmation.png` — chart artifacts.

`evidence/alpha-tests.xml` and `evidence/alpha-make-test.txt` record **317 passed,
3 skipped**. The skips are explicit external AI/Yahoo/hosted-exchange smoke
checks. `evidence/alpha-changes.patch` is the source/documentation diff for this
work; it excludes unrelated changes, credentials, account state, generated
result matrices and virtual environments.
