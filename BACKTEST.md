# BACKTEST.md — Team 6 Rings, Phase 2

**Team:** Yao Zhao, Zhuoyan Ma, Jiacheng Li, Yiran Shao · **This report:** seat `6_rings_trader_2` (Jiacheng Li)

**Code.** `team/trader.py` has a shared `RiskTrader` (hard limits, halts, order lifecycle) and two strategies:
- `MomentumTrader`: **the Phase 2 signal**, and the subject of this report.
- `EventTrader`: the event bot that traded Sessions 3–6 live.

`STRATEGY=momentum|event` picks one. The connection code is in `team/live.py`.

**Reproduce:**
- `make test`: 288 passed (`evidence/pytest_phase2.txt`)
- `make sim`
- `python scripts/phase2_sim.py`: SimSession, about 2 min
- `python scripts/phase2_backtest.py`: the recorded sessions, about 1 min
- `python scripts/phase2_live_vs_backtest.py`
- `python scripts/phase2_plot.py`: the figure

## 1. Hypothesis (stated before the results)

**Short-term price momentum pays after costs.**
- **Rule:** when the mid has risen more than `ENTRY` over the last `LOOKBACK` snapshots, buy. Sell after 60 s, when momentum turns negative, or 1 % under the entry.
- **Economic claim:** order flow is persistent. A run of buying means someone is still working an order, so the price keeps drifting.
- **Falsified if:**
  - the mean P&L across test seeds, after the 0.30 % taker fee on every fill, is ≤ 0, or
  - reading the feature late does not change the result.

## 2. Data, and not fitting where we evaluate

| Set | Use |
|---|---|
| SimSession seeds 0–9 | **Training only.** 4 × 3 grid: `LOOKBACK` ∈ {10, 30, 60, 120} × `ENTRY` ∈ {0.25, 0.5, 1} %. Rule fixed in advance: among settings with ≥ 10 round trips per seed, take the highest mean P&L **before** fees, i.e. the strongest raw edge. |
| SimSession seeds 10–29 | **Test.** Never used for choosing. |
| Recorded class sessions, weeks 3–6 | **Second test**, on the real market (other teams, house bots, halts). Never used for choosing. |

## 3. Method

- **SimSession** (`scripts/phase2_sim.py`), as the assignment prescribes: `SimSession().run(n_ticks=5000, strategies=[("me", as_signal_fn(MomentumTrader()))], symbols=[AAPL, MSFT, NVDA, TSLA], seed=s)`, with $100k. Three things in the simulator would have made the numbers wrong; we found and corrected each:
  - (a) SimSession splits each trade's fee between the two sides. We set `fee_rate=0.006`, so we pay the hosted 0.30 % per fill.
  - (b) `seed=` does not change the prices; the path comes from `FUNDAMENTAL_SEED`. We set that per seed, and a test checks it.
  - (c) the `as_signal_fn` bridge never calls `on_fill`, so we derive fills from the change in position.
- **Recorded sessions** (`scripts/phase2_backtest.py`). We use the course's replay harness (`week02_event_backtest.py`):
  - IOC orders walk the recorded depth, with a 0.30 % fee per fill
  - $150k, seat cap 150
  - plus: no fill while the venue has the symbol halted, and the one-order-in-flight rule
  - **excess** = bot P&L − P&L of holding the 20 granted shares (DoNothing on the same tape)
- **Metrics:** P&L per session, s.d. across seeds, hit rate (round trips that made money **after** fees), worst drawdown.

## 4. Results

**Training, seeds 0–9.** P&L after fees (before fees in brackets):

| Lookback \ entry | 0.25 % | 0.5 % | 1 % |
|---|---:|---:|---:|
| 10 | −2,832 (−503) | −86 (−9) | 0 (0 trades) |
| 30 | −4,387 (−765) | −978 (−137) | −32 (−11) |
| 60 | −4,691 (−811) | −1,554 (−231) | **−169 (−32)** |
| 120 | −4,275 (−681) | −1,976 (−361) | −481 (−116) |

- Every cell loses **even before fees**.
- The rule picks (60, 1 %). The two cells (10, 0.5 %) and (30, 1 %) were excluded: they trade fewer than 10 round trips per seed.
- With no edge anywhere, the choice is fragile. An earlier version of the code, with a different cash reserve, picked (10, 0.5 %), with the same conclusion.

**Test:**

| Set | Sessions | Mean P&L (after fees) | s.d. | s.e. | Before fees | Hit rate | Round trips/session | Fees | Worst DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SimSession seeds 10–29 | 20 | **−137** | 93 | 21 | −18 | 5 % | 15 | 118 | 365 |
| Recorded week 3 (excess) | 1 | −2,829 | — | — | −1,937 | 10 % | 58 | 892 | 2,840 |
| Recorded week 4 | 1 | −1,969 | — | — | −201 | 16 % | 61 | 1,768 | 2,662 |
| Recorded week 5 | 1 | −5,048 | — | — | −2,628 | 13 % | 71 | 2,419 | 5,431 |
| Recorded week 6 | 1 | −1,280 | — | — | −350 | 30 % | 46 | 930 | 1,280 |

**Verdict: the hypothesis is rejected.**
- **SimSession:** a 1 % rise over 60 ticks is followed by no drift (−18 before fees against $118 of fees), and 95 % of round trips lose after fees.
- **Recorded sessions:** the strategy lost **before** fees in all four weeks, because after a sharp rise the price falls back. That is mean reversion, the opposite of our claim.

## 5. Look-ahead check (Figure, right)

The feature at tick t uses only `prices[: t+1]`. `SIGNAL_DELAY = k` reads it k ticks late, and `tests/test_trader.py` checks that a one-tick delay hides a jump that happened in the last tick.

| Feature read k ticks late | 0 | +1 | +2 | +5 |
|---|---:|---:|---:|---:|
| SimSession, test seeds, before fees | −18 | −18 | −18 | −22 |
| Recorded weeks 3–6, mean excess before fees | **−1,279** | −576 | −299 | −295 |

**SimSession:** flat. There is nothing to degrade, because the feature carries no information there.

**Recorded sessions:** this is the informative row. The loss shrinks steadily as the feature gets staler, and it is mostly gone after two ticks. That is exactly how a real edge decays, but with the **opposite sign**: the timely signal is reliably wrong, so the market reverts within a few seconds of a move.

Two consequences:
- The backtest is not using the future. A look-ahead bug would flatter delay 0; here delay 0 is the worst.
- The natural next hypothesis is short-horizon **mean reversion**. It has to be tested on fresh data, because these four tapes have now been seen.

## 6. Live vs offline

**MomentumTrader has not traded live yet; its first live block is Session 7.** The bot that traded Sessions 5–6 live is `EventTrader`, the same code as in `team/trader.py` today. Replaying the two recorded blocks through it reproduces the original bot's offline result: week 5 exactly, week 6 within $17.

`scripts/phase2_live_vs_backtest.py` compares, event by event, the live fills from our log with the offline replay (fees included):

| Event | Live fills | Backtest fills | Live P&L | Backtest P&L | Main reason for the gap |
|---|---:|---:|---:|---:|---|
| Wk 5 NFLX +7.6 % | 2 | 2 | +56 | +189 | NFLX halted 7 s later and froze both runs |
| Wk 5 AMD −8.3 % | 0 | 0 | 0 | 0 | The halt came before any order |
| Wk 5 market-wide −3.0 % | 90 | 51 | **+865** | **−1,818** | **Inventory:** live carried shares from earlier blocks (110 NVDA, …), so "sell first" sold real stock. **Reopening:** the replay sold NFLX into the first thin snapshot after the halt (701); live sold 1 s later at 812–823. |
| Wk 6 INTC −5.8 % | 7 | 2 | +4 | +1 | — |
| Wk 6 MSFT +3.1 % | 1 | 2 | −42 | −399 | **Latency/queue:** the replay bought the stale ask 0.3 s after the message; live, the ask had moved past our cap first. |
| Wk 6 market-wide −2.2 % | 10 | 52 | −144 | −1,097 | **Fill model:** the replay takes depth that rivals took live; most live IOCs came back unfilled. |
| **Total** | 110 | 109 | **+739** | **−3,124** | Also live week 5: 30 `INSUFFICIENT_CASH` rejects |

**Lesson.** The offline replay assumes it is first in the queue, with the whole displayed depth and no inventory of its own.
- That made it worse than live here, because the losses came from trades live never got.
- For momentum the same bias flatters the backtest, since momentum needs fast fills into a rising book. So live momentum should do no better than the losses in §4.

## 7. Session 5 (Part A): how the bot behaved through the risk events

Source: `scripts/phase2_session_evidence.py`, run on the recording and on the exchange-reconciled live log (`evidence/session5_replay.txt`; week 6 in `evidence/session6_replay.txt`).
- **Halts.** 6 `TRADING_HALT` / 8 `TRADING_RESUMED`. No fills by any 6_rings seat in a halted symbol. After every resume, the first order went out within 0–3 s.
- **Position limits.** Largest |position| was 140 against a seat cap of 150 and a limit of 800. Week 6: 134 against 1,000.
- **Liquidation.** None. `RECONCILE at close — OK`.

These rules live in `RiskTrader` (`_check`, `on_event`), shared by both strategies and covered by `tests/test_trader.py`.
