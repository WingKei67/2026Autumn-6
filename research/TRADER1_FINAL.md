# Trader 1 final report (integration phase)

**Decision: GO for the corrected baseline. NOT-YET / CONDITIONAL for Event2.** Nothing has been deployed, pushed or run against the live exchange.

## Final evaluation - untouched seeds 4001-4100, run once, code/parameters frozen first (commit bc5dbac)
500/500 runs completed; 0 liquidations; 0 accounting errors; only wire errors are the intended IOC-confirmation ORDER_NOT_FOUND. Paired by seed, official `score_equity`.

| candidate | dScore vs cash | dPnL vs cash | vs original deployed Trader 1 | vs corrected baseline (deployed limits) |
|---|---|---|---|---|
| 1. Original deployed Trader 1 (`deployed_trader`, verbatim e4d8c54) | +0.026 [+0.014,+0.040] | +$304 | - | -0.098 |
| 2. **Corrected baseline** (`prod_baseline_trader`: weeks 5-10 + futures guard + IPO kept; deployed limits 70k/0.60) | +0.124 [+0.102,+0.147] | +$1,448 | **+0.098 [+0.081,+0.117], P(win)=0.91, +$1,144** | - |
| 3. Baseline with relaxed limits only (gross 105k, reserve 0.35) | +0.143 [+0.118,+0.167] | +$2,345 | +0.117 | +0.018 [+0.010,+0.027], +$897 |
| 4. **Event2** (`prod_event2_trader`, frozen defaults; relaxed limits 105k/0.35, notional 0.30, 6 symbols) | +0.201 [+0.168,+0.236] | +$2,839 | +0.175 [+0.143,+0.209], P(win)=0.93, +$2,535 | **+0.077 [+0.051,+0.105]**, P(win)=0.78, +$1,391 [+1,183,+1,615] |

Event2 vs the EQUAL-capital baseline (#3), which isolates strategy from capital: **+0.059 [+0.034,+0.086], P(win a seed)=0.66, dPnL +$494 [+291,+718], median +$153, p10 -$584, worst -$1,114.** By week (vs #3): wk6 +0.037, wk7 +0.116, wk8 +0.002, wk9 +0.012, wk10 +0.126.
Risk (final): max drawdown mean 0.30% / worst 1.21% (baseline worst 1.01%); peak gross ~$100k mean (baseline $72k); realized long +$1,068/run and short-side +$1,012/run for Event2 (baseline +$328 / +$519; "short" = sell-first, includes the baseline's sell-then-restore of endowed inventory).

## Which result is deployable?
- **Corrected baseline - yes, GO (pending your authorization to deploy).** Same risk limits as the deployed bot; +0.098 [+0.081,+0.117] over the original, wins 91% of paired seeds, no liquidation/accounting failures, passes the real-WebSocket rehearsal in weeks 6-10 including forced reconnects. Most of the gain is simply that the original is silent in weeks 6-10.
- **Event2 - conditional.** Under the deployed limits (70k/0.60) it does NOT beat the corrected baseline (dev2: +0.016, CI [-0.007,+0.043], dPnL -$61). Its edge appears only after relaxing the gross cap and cash reserve, which is a risk-appetite decision that is yours; at equal relaxed capital it adds a modest +0.059 and loses on a third of seeds.
- Original sealed-test claim (+0.044 over baseline at 400 shares/140k) does NOT transfer to the deployable 150-share seat cap; the 400-share results are not deployable.

## Robustness (dev2 seeds 3001-3030 per condition, corrected harness; Event2 vs equal-capital baseline, dScore/dPnL)
latency +1s: +0.032/+$136 | fees x2: +0.104/+$714 | volatile x2: +0.088/+$817 | competitive opponents: +0.076/+$595 | **thin liquidity: +0.001/-$176 (no edge)** | **team headroom 60 < seat cap: -0.031/-$331 (worse)**. Event2 vs cash is positive in thin and low-headroom cases, i.e. it never destroys value, but it is not better than the baseline there.

## Evidence on previously unresolved items
- Latency cash discrepancy ($50-380): harness artifact, fixed; the 3 flagged runs reconcile exactly with a production-faithful trader reconnect (research/test_regressions.py).
- SYMBOL_HALTED rejects (IOC racing a velocity halt) occur for baseline and Event2 alike; fail-closed reconnect recovers.
- Open, small: under the headroom stress seed 3011 (19 reconnects) both bots show an identical ~$3.12 ledger-vs-exchange cash drift; not Event2-specific; cause not established.

## Remaining risks / not established
- Simulator-only; no real-data out-of-sample. Per-run score is one week.
- Bots do not know team-wide headroom (other seats' holdings): `POSITION_LIMIT` rejects force reconnects. Keep `SEAT_POSITION_CAP` consistent with the other seats.
- Event2 edge absent in thin books and weeks 8-9; relies on relaxed capital limits.
- Rehearsal used a controlled counterparty in accelerated time: engineering evidence, not performance.
- Not tested: overshoot fade, Kelly/vol sizing, lead-lag, IPO tuning, hosted rate limits/Week-7 quotas beyond latency.

## Production files changed (research branch only)
`team/trader.py` (modified), `team/event2.py` (new), `tests/test_alpha.py` (pin test rewritten, not deleted), `tests/production_pins.json` (new, approved hashes + reasons), `tests/test_trader1_integration.py` (new, 16 tests). `team/broker.py` unchanged. Original trader kept verbatim at `research/legacy_trader_e4d8c54.py` (verified equal to the original pinned hash). Activation default: `TRADER_STRATEGY=baseline`; Event2 is opt-in via `TRADER_STRATEGY=event2`.

## How to run / reproduce
`python -m research.suite batch --stage final --count 100 --ticks 1500 --names cash_trader,deployed_trader,prod_baseline_trader,prod_event2_trader` (results already in registry; seeds 4001-4100 are now CONSUMED), `python -m research.paired --stage final --role trader [--control deployed_trader]`, `python -m research.analyze_trader --stage final --a prod_event2_trader --b prod_baseline_trader`, rehearsal: `python -m research.rehearsal --candidate prod_event2_trader --week 7 --all-symbols --reconnect`. Fresh seeds needed for any new candidate (e.g. 5001-5100).
