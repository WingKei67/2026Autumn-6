# Phase 1 — Team 6 Rings

**Team:** Yao Zhao, Zhuoyan Ma, Jiacheng Li, Yiran Shao  
**Bot:** `team/trader.py` · Seat: `6_rings_trader_2`

**State tracking.** Our `arena.Trader` subclass generates event-driven IOC orders from the live book. `team/ledger.py` updates cash, positions, average entry prices, realized P&L and fees from confirmed fills. Starting-share grants, IPO allocations and dividends are recorded separately. Bounded cash differences inferred from authoritative snapshots are explicitly logged and totalled; we do not treat them as independently verified interest. At close, reconciliation compares accounting fields and unrealized P&L using valuations from the same leaderboard update. Missing valuation data produces an incomplete result.

**Recovery.** After a disconnect, the bot reconnects with exponential backoff and jitter, repeats the handshake and rebuilds its book from incoming snapshots. Authoritative updates restore both the ledger and the strategy’s portfolio. Average entry prices missing from portfolio updates are recovered from the leaderboard. Trading pauses during initial recovery; subsequently, an unresolved entry price blocks only the affected symbol.

**ORCA decision.** We indicated 150 shares at the $28 upper bound. The valuation model in `engine/exchange/ipo.py` uses `POP_MU=0.12` and `POP_SIGMA=0.18`, implying an expected valuation premium of `exp(0.12 + 0.18²/2) − 1 = 14.59%` and a `Φ(−0.12/0.18) = 25.25%` probability of valuation below the offer. These are model assumptions, not guaranteed returns. We capped our indication at the intended position rather than inflating demand to compensate for pro-rata allocation. The October 24 live log records 111 ORCA shares at a $28 average entry.

**Replay and runtime evidence.** Session 3 records same-tick fills following both NVDA and TSLA earnings events. Session 4 records our first fill 812 ticks after session open, a fill 12 ticks after the GOOGL event and a same-tick fill following the economic print. These exports contain market data only, so they do not establish live uptime or rejection counts.

The revised **real-time local rehearsal** completed 2,400 open-market ticks using real WebSocket transport and exchange matching. It recorded 53 fills, zero trader rejects, two connections spanning an induced disconnect/reconnect, and an allocation of 150 ORCA shares at $24. Final unrealized P&L matched the exchange at **−$257.64**. The cash audit disclosed **$451.7227 in inferred adjustments**, with zero residual after adjustments. See `evidence/phase1_final_rehearsal.log` and `evidence/phase1_validation.txt`. This validates local recovery and accounting; it does not establish original Friday participation.

**Purchase window 1.** We purchased no upgrades. Our planned 150-share positions did not require a higher position limit, and the available execution evidence did not establish that fee savings would recover the cost of a fee-tier upgrade.
