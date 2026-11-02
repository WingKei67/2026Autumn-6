# Offline AlgoArena alpha research

The live `team/trader.py`, `team/broker.py`, `.env`, registration, and existing
Week 5 risk controls are not modified by this work. New strategy imports never
start a live connection. The experiment calls the real local exchange directly,
using isolated accounts and temporary roster/persistence files. It does not use
`season_sim.py` presets as a substitute for our strategy implementations.

## Reproduce

Use the existing isolated Python 3.10 environment:

```sh
.venv310/bin/python -m pip install -r requirements-alpha.txt
.venv310/bin/python scripts/alpha_backtest.py --output alpha_results/study --workers 4
.venv310/bin/python scripts/alpha_portfolio.py --input alpha_results/study --workers 2
.venv310/bin/python scripts/alpha_fee_audit.py --input alpha_results/study --workers 4
.venv310/bin/python scripts/alpha_report.py --input alpha_results/study
.venv310/bin/python -m pytest -q tests/test_alpha.py
make test
```

A full study is substantial: 624 calibration runs, 800 base held-out runs,
550 held-out ablations, 2,400 stress runs, 8 fixed-seed illustrative paths, and
15 secondary historical replays. The independent profit-ranked portfolio
confirmation adds 1,400 runs on another 50 seeds (2000–2049). A read-only
fee audit replays 450 fee-bearing maker runs to extract total charges and
verify matching P&L/fill counts. It uses 12 calibration seeds (0–11) and 50
held-out seeds (1000–1049), repeated across regimes. The primary study has 62 independent
seed identities (112 including portfolio confirmation), **not thousands of independent historical sessions**.

Stages can be run independently, with the same ticks/cash and frozen source:

```sh
.venv310/bin/python scripts/alpha_backtest.py --stage calibrate --output alpha_results/reproduction
.venv310/bin/python scripts/alpha_backtest.py --stage evaluate --output alpha_results/reproduction
.venv310/bin/python scripts/alpha_backtest.py --stage stress --output alpha_results/reproduction
.venv310/bin/python scripts/alpha_backtest.py --stage history --output alpha_results/reproduction
.venv310/bin/python scripts/alpha_report.py --input alpha_results/reproduction
```

Use a new output directory for a different protocol. The source hash check
rejects changes between calibration and evaluation. Earlier development results
in other `alpha_results` subdirectories are superseded; the delivered study is
`alpha_results/study`.

## Strategies

* `team/alpha/event_momentum.py`: only observed CALENDAR_EVENT releases activate
  it. Price direction comes from post-event quotes, not the public pct/target.
  Tests confirmations 3/5/10/15, holding 20/40/60/120, and trailing multipliers
  2/4. The cost gate includes a causal next-tick slippage allowance, two taker
  fees, spread, exit allowance, and safety margin. Entry limits stay fixed while
  the order travels. Negative momentum sells held inventory; it does not create
  new equity shorts.
* `team/alpha/post_halt.py`: records a lagged pre-halt reference; after resume,
  requires reversal and z=2.5/3/3.5/4. Recent earnings repricing is excluded.
  Only actual observed halt/resume events activate it. Zero eligible trades is
  a legitimate result, not substituted with a fictional fade.
* `team/alpha/market_making.py`: post-only quotes combine mid, microprice,
  imbalance, and lagged trend, then apply inventory skew, volatility/trend
  adverse-selection protection, dynamic spread/size, news cooldown and halt
  gates. Adverse selection here is a conservative volatility/trend proxy, not
  an oracle estimate of future toxic flow. Queue position is determined by real
  order IDs/sequence and competing orders. Partial fills and cancel latency are
  retained; a cancel request does not immediately free a risk reservation.
* `team/alpha/basket.py`: estimates rolling beta and residual z-score using
  lagged data, with thresholds 2/2.5/3/3.5 and lookbacks 60/120. ARENA10 is the
  arithmetic average of the ten equity **price levels**, not an equal-weighted
  average of percentage returns. A stock leg and opposite index-future leg are
  non-atomic; the future arrives another tick later, scaled to a partial stock
  fill. Both legs pay real fees and post real futures margin. The strategy is
  disabled in Week 5 because the exchange disables futures. Week 9 supplies the
  separate futures-enabled comparison. Equity shorts remain disabled locally;
  expensive residuals can only sell existing equity inventory.

The basket uses the actual members in `engine/plugins/securities/futures.py`:
AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META, NFLX, AMD, INTC. Futures are gated
by `futures_enabled`, use $40 initial margin per contract by default, and settle
variation every 300 ticks. The actual configuration values remain the source
of truth.

## Market and execution model

`alpha_lab/runner.py` drives `OfflineExchange`, a subclass that replaces only
transport and wall-clock halt timers. Matching, price-time priority, partial
fills, auction processing, calendar ramps, trade impact, stock borrow rules,
price bands, carry, margin, liquidation and order quotas are inherited from
`ExchangeServer`. Halt duration remains 300 virtual seconds; it is not shrunk
to 0.05 seconds as in the existing headless season simulator.

The simulator uses the four liquid/event-relevant equities AAPL, NVDA, NFLX,
AMD, plus ARENA10 when enabled. The index still derives from all ten equities,
and starting grants/marks include all equities. This is a restricted trading
universe, not a claim to optimize every security.

Each candidate has a separate exchange. No candidate trades against another
candidate. Background liquidity has a separate funded provider for each active
symbol; each provider is subject to the actual per-team quota. An independently
seeded flow trader sometimes follows already observed price changes. Spread,
quote lifetime, displayed depth, volatility, event sign and event magnitude
vary by seed. Spread levels 4/12/30 bps cover the historical median-to-tail
range; vol multipliers 0.5/1/2/4 include deliberately harsher regimes. Actual
orders affect subsequent venue marks through the real price-impact engine.
Background agents are modeled populations, not reconstructed hosted traders.

A decision at tick t can execute no earlier than t+1, on the next book after
background quote updates. Its limit does not adapt to the unseen next quote.
Post-only orders can be rejected if stale quotes cross on arrival. Liquidity
consumed by one order is unavailable to later orders until real new quotes
arrive. Cancellations use the real handler and remain reserved in flight.
The serialized offline dispatcher can inspect completion after a handler
returns; this is not a new cancellation guarantee for the hosted protocol.

Starting cash is $175,000 from `team.config.CAPITAL['6_rings_trader_1']` for all
standalone candidates, including the broker research sleeve, to avoid capital
bias. This is a common research budget, not the full $350k live broker capital.
Opening grants use the real roster division across three seats: six shares per
name. A combined portfolio splits both cash and grants, rather than multiplying
them. Independent allocations enforce 150 equity shares per symbol (20 for
market making), at least 60% projected cash at new-risk entry, at most min(40% capital, $70k) gross at entry,
no new risk below 70% equity, and no equity shorts. Futures have a separate
20-contract ceiling and are counted at full notional in our risk bound, in
addition to actual exchange margin. Outstanding orders reserve each budget.

The baseline 30-bps taker fee is an explicit required override; the local code's
built-in default is 15 bps. Maker rebates use the actual configured rate and
venue-net cap. The report saves both commissions and rebates. A 60-bps stress
repeats the experiments. Other stresses use three-tick order latency and doubled
fundamental volatility plus 1.5x event magnitudes and doubled spreads.

## Evaluation and interpretation

Calibration chooses each strategy by mean **paired excess over no-trade** minus
half its standard deviation; safety failures receive a large penalty. Basket
parameters are calibrated in Week 9, the other three in Week 5. Parameters are
frozen before seeds 1000–1049 are run. The combined portfolio uses the top two
Week 5 calibration strategies with equal cash sleeves and shared team limits;
it is not selected from held-out winners.

The original baseline runs `MyTrader.on_tick` and its real `_Bot._place_order`
risk method, using a virtual clock and offline transport. That original bot
intentionally gates itself to Week 5 and is inactive in Week 9. Buy-and-hold is
risk-constrained and buys only the restricted tradable equity universe.
No-trade holds its original grant and cash. All baselines share the initial
endowment and cash allocation. No-trade earns actual configured cash interest.
Positive account P&L can therefore coexist with negative trading alpha.

Metrics: conservative exchange MTM P&L, paired excess, fees/rebates/carry,
decision-mid slippage, session Sharpe (not annualized), worst peak-to-trough
drawdown, turnover, FIFO closing-fill win rate including costs, liquidations,
position violations, pending/team share exposure and per-sleeve gross, hedge failures, unclosed overlay,
and same-tick-fill checks. The final 12 ticks try to return trading sleeves to
their initial inventory; a halt or empty book may prevent that. Such inventory
is marked, not magically closed at a signal price.

Bootstrap intervals resample paired seed outcomes. They describe this generator,
not live-exchange certainty, and are not multiple-comparison-adjusted discovery
claims. Conditions and ablations are reported without selecting new parameters.

The Week 2–4 historical tapes are separate diagnostic replays, not a pristine
holdout: they were inspected during engineering. At most three recorded levels
are reconstructed and taker participation is capped at 10% of the visible
touch. Execution uses a later observed book. Recorded prints are not recycled
as extra liquidity. Maker fills are deliberately not estimated because exact
historical queues cannot be recovered from the market-only tapes. The IPO and
full participant responses are not reconstructed. These results are secondary,
and cannot validate Week 5 breakers or Week 9 futures absent in the recordings.

## Results and artifacts

After execution, `alpha_results/study/REPORT.md` contains the numerical ranking,
stress tests, ablations, historical diagnostics and limitations. CSV and JSON
files preserve every seed/candidate result and parameter choice. PNG charts
show paired held-out profitability, stress behavior, calibration sensitivity,
and a fixed illustrative seed rather than a cherry-picked winner.

No result from this research automatically changes or launches the live bot.

The first combined portfolio uses calibration risk-adjusted scores. A second
profit-ranked momentum/market-making portfolio is tested on untouched seeds
2000–2049 after the first study; its results are reported separately. This
explicit confirmation split avoids calling a post hoc portfolio a fresh test
on seeds already used to motivate its design. Canonical `total_net_fees` and
`cancellation_fees_paid` include the fee audit; the original `net_fees` field
is the fill-only fee diagnostic, retained for auditability. Both definitions
are already reflected in account P&L, so do not subtract either again.

Historical offers in the long-only Week 2/3 scenarios require real background
stock inventory. `scripts/alpha_history.py` supplies 200 shares per provider
and caps each reconstructed level at 60 shares, in addition to the 10%
participation cap and three-level truncation. It does not bypass exchange
risk checks. Recorded pre-open/open/close gates are enforced; no subject order
is submitted outside continuous trading. This historical fixture correction is separate from the frozen
primary model; its checksum is saved in each historical result.

The final primary experiment covers 1,500 one-second ticks (an event-window
study, not a full-hour live trial). Week 9 gives momentum/reversion access to
the future too when public market-wide events affect it; market making
explicitly stays in equities. Cancellation charges and futures variation can
subsequently debit the entry cash reserve. Stops cannot execute through a halt.
The final report preserves the negative historical momentum results and
does not equate conditional simulated profitability with a validated live edge.

To rerun only the historical diagnostics, use
`.venv310/bin/python scripts/alpha_history.py --input alpha_results/study --workers 4`.
