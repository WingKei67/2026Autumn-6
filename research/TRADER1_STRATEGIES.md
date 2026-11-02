# Trader 1 strategies implemented and tested

All run through the real `ExchangeServer` and the real `_Bot` adapter (`research/harness.py`); only transport and halt timers are virtualized. Controls: `cash_trader` (no orders, same seed), `baseline_trader` (deployed `MyTrader`, unmodified), `season_trader` (corrected baseline).

| name | what it is | status |
|---|---|---|
| baseline_trader | deployed MyTrader; `_week5_active` only for week 5, so 0 orders in weeks 6-10 | control |
| season_trader | same logic, activated for weeks 5-10, ARENA10 (future) excluded | corrected baseline |
| short_trader | + short leg for negative events (parent state machine) | dev only; ~= season_trader (+0.114 vs +0.105) |
| aggressive_trader | 2x caps | dev only; unreliable (2 DQ flags in the early, pre-guard code) |
| ipo_trader | + VLTA/LUNA IPO indication | dev only (+0.143, dev seeds, before harness fixes); NOT validated |
| imbalance / reversion | never fire by construction; separate signal study shows OBI/microprice have no forward-return signal | rejected |
| **event2_trader** | `EventTrader`: enters on the fired event's signed move, long or short, notional budget (15% NW per event), depth-aware IOC chunks (<=100), exit at 0.9 x overshoot target / 3 x ramp time stop / 0.5 x |pct| stop, 4 symbols max on market-wide prints, 3% drawdown kill | **best candidate** |
| event2_long_trader | same, long only | validated; worse than season_trader on score |

Not implemented / not tested: overshoot fade after the peak, Kelly/vol-targeted sizing, cross-asset lead-lag, online models, passive entries for Trader 1, futures trading, Week-7 message-quota stress beyond latency. These remain open; they are NOT rejected on evidence.


---
# Update - production integration
`prod_baseline_trader` = actual `team.trader.MyTrader()` with week-aware activation (GRADED_WEEKS 5-10), futures guard, IPO unchanged. `prod_event2_trader` = actual `MyTrader(strategy='event2')` using `team/event2.py` (shared with the research wrapper; parity verified: identical orders and P&L on sealed seeds 1001-1006,1010 and dev2 3001/3002/3004 across parameter-default refactors). `deployed_trader` = verbatim original. Differences research harness vs bot: harness uses a virtual FIFO wire and emulated reconnect; research `event2_trader` lacks IPO participation and uses `SeasonTraderBot._place_order` (no pending-buy reservation/price-band step); production `_Bot._place_order` applies those plus the short gate, SSR, seat cap and equity floor again at the outbound boundary.
