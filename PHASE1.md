# Phase 1 — Team 6 Rings

**Team:** Yao Zhao, Zhuoyan Ma, Jiacheng Li, Yiran Shao  
**Bot:** `team/trader.py`, seat `6_rings_trader_2`

**State tracking.** Our `arena.Trader` subclass generates event-driven IOC orders using the live book. `team/ledger.py` maintains cash, positions, average entries, realized P&L and fees from confirmed fills. Starting-share grants, IPO allocations and dividends are handled separately. Bounded cash accruals are recorded explicitly. At close, the revised reconciliation compares accounting fields and unrealized P&L using the venue’s same-row valuations; missing valuation data is reported as incomplete.

**Recovery.** On disconnect, the bot reconnects with exponential backoff and jitter, sends a fresh handshake and rebuilds the book from incoming snapshots. Authoritative updates restore both the ledger and the portfolio used by the strategy. Entries unavailable in a portfolio update are recovered from the leaderboard. Strategy decisions pause while recovery is pending.

**ORCA decision.** We chose to indicate 150 shares at the $28 upper bound. Under the scenario’s stated lognormal valuation model, the expected valuation premium is approximately 14.6%, with approximately a 25% probability of finishing below the offer. We limited the indication to our intended position rather than inflating the bid to offset pro-rata allocation. The October 24 live log shows 111 ORCA shares at a $28 average entry.

**Evidence.** Session 3 records same-tick fills for our seat following both NVDA and TSLA earnings events. Session 4 records its first fill at session-open +812 ticks, a fill 12 ticks after the GOOGL event, and a same-tick fill following the economic print. Both exports are market-only, so live uptime and rejection counts remain unverified. The original local rehearsal records 10 acknowledged orders, 26 fills, zero logged rejects and a survived disconnect/reconnect. Its close check omitted unrealized P&L; a new full-session log demonstrating the revised check remains pending. `evidence/pytest.txt` records 252 tests passed, with no failures or skips.

**Purchase window 1.** We made no upgrade purchases.
