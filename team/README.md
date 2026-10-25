# Six Rings — AlgoArena aggressive event trader (v4.2)

A **single-file drop-in** `team/trader.py` for the FINM 33500 AlgoArena SDK included in the user's course archive. The goal is aggressive **event-driven net returns**, subject to hard constraints. This is research software, not a guarantee of profitability, and **has not been tested against the live exchange**.

## Why v4.2 differs from v3

1. **Correct startup reconciliation.** The archive's exchange server sends book + leaderboard on reconnection but does not consistently send `PortfolioUpdate`. The supplied SDK consequently continues to hold its **default cash of $100,000 and no inventory**. This version waits for an authoritative portfolio message. If none arrives after 8 trading ticks, it submits **one simulated one-share IOC buy** in the lowest-priced eligible instrument costing no more than $60. A fill forces the venue to push an authoritative `PortfolioUpdate`. If no snapshot follows, it halts. This probe costs real simulated cash/fees and cannot be made completely risk-free.
2. **IOC execution** instead of limit orders that rest indefinitely. The course's `PlaceOrder` protocol supports `order_type="ioc"`: any unfilled remainder cancels immediately. The bot keeps its order lock until an exchange `OrderAck` is processed; missing acknowledgments cause a halt.
3. **All inherited LONG holdings are risk-managed**, not just three symbols. Concentration trimming, peak-to-trough trailing exits, and total account drawdown are applied to actual portfolio positions. The venue lacks original cost bases in the `PortfolioUpdate`; no invented entry prices are used.
4. **Aggressive but selective alpha**: positive earnings continuation and macro-shock rebounds only when a heuristic expected continuation exceeds estimated round-trip taker fees and spread. The code also contains exploratory plain momentum logic, **disabled by default** because there was no demonstrated net-positive candidate in the available recordings.
5. **Fast event exits**: protective sells are exempt from entry cooldowns. Take-profit based on the announced earnings magnitude; time exits after ~28 seconds for positive-event positions, ~58 seconds for macro rebound positions, and ~160 seconds for ordinary-alpha positions.
6. **No hidden assumptions**: no shorting, no automatic leverage, no maker rebate modeling for IOC orders. If inherited shorts are present, the strategy halts and requires manual intervention. It will not sell long inventory that does not exist.

## Installation (Mac)

Stop the old bot (`Control+C`), verify there are **no old resting orders** left from earlier versions in the portal, and run in a second Terminal:

```bash
cd ~/Desktop/FINM_33500/2026Autumn-6-fresh
source .venv/bin/activate
cp team/trader.py team/trader_backup_$(date +%Y%m%d_%H%M%S).py
cp ~/Downloads/trader.py team/trader.py
python -m py_compile team/trader.py
TEAM_ID=6_rings_trader_1 python -m team.trader
```

If the console shows a certificate error, use the existing environment's certificate bundle (do not modify the strategy itself). Do not expose `.env` or `ARENA_TOKEN`.

Expected logs:

```text
STRATEGY 6rings-aggressive-v4.2-ioc initialized
SYNC WAIT ...                  # briefly
SYNC PROBE ...                 # only if reconnect lacks snapshot
FILL buy INTC ...              # expected if the one-share probe executes
SYNC READY server equity=... cash=... positions={'NFLX': 16, ...}
PORTFOLIO BASELINE equity=... positions=... (legacy tracked)
RISK equity=... gross=... fee=30.0bps
IOC ORDER buy AAPL ...         # only when a qualifying opportunity arrives
IOC ACK id=...
IOC settled ...
```

Your previous account had about **$194.8k net worth** and several holdings. **Do not trade if `SYNC READY` displays the wrong account, `VALUATION DISAGREEMENT`, or `UNCONFIRMED IOC`.** A failure to synchronize means the bot needs additional work; do not remove its protection.

## Adjustable parameters (inside `MyTrader`)

| Parameter | Default | Interpretation |
|---|---:|---|
| `MAX_WEIGHT` | `0.12` | 12% maximum per-security portfolio weight |
| `TARGET_GROSS` | `0.65` | Stop increasing aggregate gross long exposure beyond 65% of equity |
| `MAX_GROSS` | `0.80` | Trigger gross-exposure reduction above 80% |
| `MAX_LOSS_FROM_PEAK` | `0.045` | 4.5% live peak-equity drawdown triggers flattening and blocks buys |
| `MAX_NEW_ORDER_SHARES` | `60` | Per-IOC maximum new shares (also capped by risk budget) |
| `MAX_TRADE_RISK` | `0.0035` | Position sizing risk budget, 0.35% of equity for a hypothetical stop |
| `LEGACY_TRAIL` | `0.028` | 2.8% starting trailing limit for inherited positions, widened by volatility |
| `NEW_POSITION_TRAIL` | `0.025` | 2.5% starting trailing limit for newly purchased positions |
| `ENABLE_ORDINARY_ALPHA` | `False` | Momentum experimental feature, disabled without out-of-sample evidence |

Loss controls **do not guarantee a maximum realized loss**: prices can gap, quotes can disappear, IOC orders can remain unfilled, and exchange sessions can stop. The exchange may automatically issue market orders at session close.

## Evidence and testing

- `python -m py_compile trader.py`: syntax check.
- `pytest -q test_trader.py`: 22 offline tests, using the actual course SDK message models and a synthetic market. The tests include the user's approximate 11-position account shape, price mark reconciliation, inherited stops, event profit exits, macropanic trough retention, ack/partial-fill lifecycle, and the one-share synchronization probe. Tests are **not** exchange integration tests.
- `python research_check.py PATH_TO_COURSE_ARCHIVE.zip`: explores trade costs on old team CSVs. These are 5-second snapshots (not actual 0.5s order fills); one holding horizon (120s) and overlapping sessions mean it is neither an exact backtest nor independent out-of-sample validation. Seven plain momentum candidates cleared the heuristic cost gate; none showed positive net round-trip return in this illustrative comparison. Accordingly momentum is off by default.

## Fees and strategic economics

At the current reported taker fee of **30 bps per side**, round-trip fees alone cost **60 bps**. A $12,000 buy-and-sell cycle spends approximately **$72** on taker fees, plus effective spread and adverse selection. The agent only enters if its forecast proxy plausibly exceeds this hurdle; the proxy is **not calibrated** from a large training sample. True cost-adjusted returns must be measured in local open play or on the next independent session.

The Season Guide quotes a `$40,000` fee-tier upgrade. With low volume it is unlikely to repay its cost; do not buy it solely on the expectation that it will make this bot profitable. The server-supplied `FEE_SCHEDULE` remains authoritative.

## Deployment limitations

- Old resting orders from *earlier* bot versions must be canceled manually before switching. IOC orders issued by v4.2 will not leave resting remainders.
- No external web requests or credentials are required by `trader.py`. The built-in authenticated SDK connects using your existing `.env` as before.
- The server gives no original average cost in `PortfolioUpdate`. The strategy manages inherited inventory based on observed prices **from startup onward**, not from historical purchase prices.
- **Do not run two instances simultaneously** for the same trader ID.
- Price/event hypotheses are unvalidated; this is a robust experimental implementation, not a tested money-making machine.
