# BACKTEST.md — Team 6 Rings, Phase 2

**Team:** Yao Zhao, Zhuoyan Ma, Jiacheng Li, Yiran Shao · **Seat:** `6_rings_trader_2` (Jiacheng Li)

**Code:** `team/trader.py`. `MomentumTrader` is the Phase 2 signal and `EventTrader` is the live event bot; both use the hard limits in `RiskTrader`. **Reproduce:** `make test` (288 passed), `scripts/phase2_sim.py`, `phase2_backtest.py`, `phase2_live_vs_backtest.py`, `phase2_plot.py`.

## 1. Hypothesis (stated before the results)

**Claim: short-term momentum pays after costs, because order flow is persistent.** Rule: buy when the mid rose more than `ENTRY` over the last `LOOKBACK` snapshots; sell after 60 s, when momentum turns negative, or 1 % below the entry. **Falsified if** the mean test P&L after the 0.30 % taker fee per fill is ≤ 0, or if reading the feature late changes nothing.

## 2. Data and method

- **Training (SimSession seeds 0–9).** Grid of `LOOKBACK` {10, 30, 60, 120} × `ENTRY` {0.25, 0.5, 1} %. Rule set in advance: the highest P&L *before* fees, among settings with ≥ 10 round trips per seed.
- **Tests, never used for fitting:**
    - SimSession seeds 10–29: `SimSession().run(n_ticks=5000, strategies=[("me", as_signal_fn(MomentumTrader()))])` on 4 stocks, $100k.
    - The recorded class sessions of weeks 3–6, in the course replay harness: IOC orders walk the recorded depth, no fills while halted, $150k. **Excess** = bot P&L − holding the granted shares.
- **Simulator fixes:** the fee is split between both sides, so `fee_rate=0.006` makes us pay 0.30 %; `seed=` does not move prices without `FUNDAMENTAL_SEED`; the bridge never calls `on_fill`.

## 3. Results

**Training**, P&L after fees (before fees in brackets):

| Lookback \ entry | 0.25 % | 0.5 % | 1 % |
|---|---:|---:|---:|
| 10 | −2,832 (−503) | −86 (−9)* | 0 (no trades) |
| 30 | −4,387 (−765) | −978 (−137) | −32 (−11)* |
| 60 | −4,691 (−811) | −1,554 (−231) | **−169 (−32)** |
| 120 | −4,275 (−681) | −1,976 (−361) | −481 (−116) |

\* < 10 round trips per seed, so excluded. Every cell loses even before fees. The rule picks **(60, 1 %)**.

**Test.** Hit rate = share of round trips profitable after fees.

| Set | n | Mean P&L | s.d. | Before fees | Hit rate | Fees | Worst DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| SimSession seeds 10–29 | 20 | **−137** ± 21 | 93 | −18 | 5 % | 118 | 365 |
| Recorded wk 3 / 4 / 5 / 6 (excess) | 4 | −2,829 / −1,969 / −5,048 / −1,280 | — | −1,937 / −201 / −2,628 / −350 | 10–30 % | 930–2,419 | 5,431 |

**Rejected.** In SimSession no drift follows a 1 % rise. In the real sessions the strategy loses *before* fees every week: prices **mean-revert** after sharp rises. (For comparison, `EventTrader` made +627 / +1,554 / −1,629 / −1,495 on the same tapes.)

## 4. Look-ahead check (Figure, right)

The feature at tick t uses only `prices[: t+1]`. `SIGNAL_DELAY = k` reads it k ticks late; a unit test checks that a one-tick delay hides a last-tick jump.

| Read k ticks late | 0 | +1 | +2 | +5 |
|---|---:|---:|---:|---:|
| SimSession, before fees | −18 | −18 | −18 | −22 |
| Recorded wk 3–6, excess before fees | **−1,279** | −576 | −299 | −295 |

SimSession is flat: there is no information to lose. In the real sessions the loss decays with delay, the way a real edge does, but with the **sign reversed**: the timely signal is reliably wrong. A look-ahead bug would flatter k = 0, which is the worst here. Next: test mean reversion, on fresh sessions only.

## 5. Live vs backtest

`MomentumTrader` first trades live in Session 7. Sessions 5–6 ran `EventTrader` (replaying them reproduces its original backtest to within $17). Below, its live fills against a replay of the same tape:

| Event | Fills live / bt | P&L live / bt | Why they differ |
|---|---:|---:|---|
| Wk 5 NFLX +7.6 % | 2 / 2 | +56 / +189 | A halt froze both runs |
| Wk 5 market −3.0 % | 90 / 51 | **+865 / −1,818** | **Inventory:** live held shares from earlier blocks to sell. **Reopening:** the replay sold into a thin post-halt book (701 vs 812 live) |
| Wk 6 MSFT +3.1 % | 1 / 2 | −42 / −399 | **Latency:** the replay bought a stale ask; live, the price had already moved |
| Wk 6 market −2.2 % | 10 / 52 | −144 / −1,097 | **Fill model:** rivals took the depth; live IOCs went unfilled |
| **All 6 events** | 110 / 109 | **+739 / −3,124** | Live also had 30 `INSUFFICIENT_CASH` rejects |

**Lesson:** the replay assumes first place in the queue, all the displayed depth, and no inventory. For momentum, which needs fast fills into a rising book, this flatters the backtest, so live should do no better than §3.

## 6. Session 5 (Part A): replay evidence

From `scripts/phase2_session_evidence.py` on the recording and our reconciled log (`evidence/session5_replay.txt`). **Halts:** 6 halts and 8 resumes, zero fills while halted, and an order within 0–3 s of every resume. **Positions:** max |position| 140, within the seat cap of 150 and the limit of 800. **Liquidation:** none; `RECONCILE at close — OK`.
