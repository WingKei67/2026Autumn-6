# 6 Rings — Phase 0 Capital Allocation

Team: **6 Rings** · Members: Yao Zhao, Jiacheng Li, Yiran Shao, Zhuoyan Ma
Repository: https://github.com/WingKei67/2026Autumn-6

Our approved allocation is one broker desk with $400,000 and four trader seats with $150,000 each. We hold no exchange licence.

| Seat | Bot ID | Run by | Allocated capital |
|---|---|---|---:|
| Broker | `6_rings_broker` | all four, in rotation | $400,000 |
| Trader 1 | `6_rings_trader_1` | Yao Zhao | $150,000 |
| Trader 2 | `6_rings_trader_2` | Jiacheng Li | $150,000 |
| Trader 3 | `6_rings_trader_3` | Yiran Shao | $150,000 |
| Trader 4 | `6_rings_trader_4` | Zhuoyan Ma | $150,000 |
| **Total** | | | **$1,000,000** |

$400,000 + 4 × $150,000 = $1,000,000.

## Rationale

The four trader seats provide separate accounts for testing and comparing strategies under a shared team budget, one per teammate. Equal trader allocations make comparisons easier to interpret, and no single strategy can lose more than 15% of the budget. The broker desk receives 40% of the budget to support inventory and two-sided quoting across securities: a market maker's fills leave it long or short, and a thick capital buffer is what lets it keep quoting through an adverse move. We chose not to operate an exchange so that our initial work can focus on trading, market making, and reliable execution.

## Registration revision

Professor Donadio confirmed that this revised allocation was approved and applied to the arena. The original $300,000 exchange licence purchase was reversed, and `6_rings_exchange` was removed. Re-registration was not required, and our existing team token remained valid.

At the time of that confirmation, `6_rings_trader_1` already had a live account from open play. Its cash and positions could not be resized while the exchange was running; the professor stated that it would be reset to $150,000 in cash with no positions after the block. The allocation above records the approved plan, rather than interpreting the legacy live account value as newly allocated capital. Completion of that reset should be checked against the current portal.
