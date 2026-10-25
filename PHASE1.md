# PHASE1.md — Team 6 Rings

**Team:** Yao Zhao, Zhuoyan Ma, Jiacheng Li, Yiran Shao · **Bot:** `team/trader.py` (seat `6_rings_trader_2`) · **Tag:** `phase1`

The bot is an `arena.Trader` subclass. It trades scheduled events (earnings, macro prints, teacher SHOCKs): it rides a positive move and sells into the overshoot; on a negative move it sells first and buys back past the trough. Orders are IOC limits priced off the live book, and the seat holds at most 150 shares of any one symbol.

## 1. How we track state — `team/ledger.py`

- **Trade-driven numbers come from fills only.** Every `TradeExecution` naming our seat updates position, average entry, realized P&L, fees and cash. We use the exchange's own signed average-cost rule: adding on the same side re-weights the entry; trading the other way realises P&L on the closed part; a flip through zero re-opens at the fill price. `tests/test_ledger.py` replays 20 × 200 random fills through our `Ledger` and through the exchange's `Portfolio`. Position, cash, realized P&L and entry come out identical.
- **Unrealized P&L** is marked to the venue's `ref_price`, as the exchange does.
- **Exchange-side changes are booked only for a known reason.** These arrive with no trade print: the starting-share grant, IPO allocations at the offer price, and dividends. Interest on idle cash and margin carry accrue every tick with no message at all. We book them under a separate counter, `accruals`, and report it, so it is never hidden. A position change that no fill or known reason explains is left in place to show up as a MISMATCH.
- **Reconciliation against the dashboard.** The exchange broadcasts the leaderboard every 2 s. Our row in it carries cash, positions, `avg_cost`, realized P&L and fees, which are the dashboard's numbers. Every 30 s the bot logs a `BOOKS` table with `matches exchange` or the exact differences. After `SESSION_CLOSED` it logs a strict `RECONCILE` covering positions, entries, cash, fees and realized P&L.

## 2. How we reconnect

- Any drop ends `listen()`: a clean close frame, `ConnectionClosed`, an `OSError`, or an unexpected exception. The trading loop is cancelled with it, and the bot reconnects after an exponential backoff (1 → 2 → 4 … 30 s, times a 0.5–1.0 jitter). The backoff resets after a connection that lived longer than 30 s.
- **On every connect** the bot sends a fresh `Handshake` and rebuilds the order book from the first `BookSnapshot`. It forgets orders still in flight (an IOC has already filled or been cancelled) and **resyncs its books**. An existing seat gets no `PortfolioUpdate` on reconnect, so the resync uses whichever comes first: the leaderboard row (sent at handshake) or a `PortfolioUpdate`.
- Tested in `tests/test_reconnect.py`: a fake venue drops the bot and the bot handshakes again. A second test sends only a leaderboard and checks that the books are rebuilt from it, entries included. Setting `DROP_AFTER_S=N` makes the bot close its own socket once, to produce the evidence below.

## 3. Protocol discipline

- **Outgoing:** every frame is a constructed model (`Handshake`, `PlaceOrder`, `IPOSubscribe`) serialised with `model_dump_json()`.
- **Incoming:** every frame goes through `TypeAdapter(AnyExchangeMessage).validate_json()`, the discriminated union on `type`. There is no `json.loads` and no dict access. A malformed or unknown frame is dropped at the boundary (`tests/test_wire.py`); all 48,075 frames of the Session 3 recording parse cleanly.
- **Avoiding rejects:**
  - IOC orders only
  - one order in flight per symbol
  - local position patched on the fill itself, before the lagging `PortfolioUpdate`
  - nothing sent during auctions or after the close
  - the SDK's post-close flatten disabled; in week 2 it caused 10 rejects per seat

## 4. IPO decision — ORCA

**Decision: subscribe 150 shares at the top of the range, $28.00.**

- **Why participate.** Listing value is `offer × exp(N(+12%, 18%))`. That is an expected pop of `exp(0.12 + 0.18²/2) − 1 ≈ +14.6%`, with `P(break issue) = Φ(−0.12/0.18) ≈ 25%`. The edge is positive.
- **Why the top of the range.** The pop is measured from the offer price, wherever the book prices it. Bidding below the top does not improve the pop. It only risks being left out of a hot deal.
- **Why 150 shares, not more.** Allocation is pro rata, so bidding more than we want gets us more shares when the book is cold, which is exactly the case that breaks issue. We bid the size we actually want to hold, the seat's 150-share cap (inside the team's 800 limit). That is $4,200, or 2.8% of the seat's capital. A −2σ listing (−21%) costs about $900, 0.6% of capital.
- **What happened (Session 4, live).** The deal priced at the top, **$28.00**: 5,917 shares were subscribed for 4,000 offered. Only indications at or above the offer price share the allocation (`exchange/ipo.py`). Pro rata cut us back to **111 of 150 shares (74%)**, booked at exactly $28.00. 111/150 implies roughly 5,400 shares bid at $28, so about 500 shares bid below the top got nothing. That is the risk we avoided by bidding the top of the range. On listing day ORCA opened at $28.00, reached +2.9% by t+610, broke issue at −1.1% after the macro print (t+1204), and closed at +0.5%. By 10/24 it was marked at $29.45 (+5.2%, `evidence/live_arena_reconnect.log`).
- **Rehearsal (local, same week-4 scenario).** The deal was undersubscribed: it priced at the bottom, $24.00, and we got the full 150 shares. ORCA closed at $24.77 (+3.2%). The rehearsal log shows each step: `on_ipo` → `IPO_SUB_RESULT` → `IPO ALLOCATED` → listing.

## 5. Evidence

### Session 3 (Fri Oct 16) — `scripts/phase1_evidence.py` on the recording (`evidence/session3_replay.txt`)

The two events were NVDA earnings −3.8% (t+300) and TSLA earnings −5.2% (t+700). `6_rings_trader_2` had fills in the **same tick** as both events.

The NVDA event shows the FADE logic at work: the bot sold 8 NVDA at $168.72 at t+0, then bought back about 60 shares at $161.6–162.4 between t+13 and t+17. The same pattern followed TSLA: it sold 8 at $227.34, then bought about 120 at $216.5–218.2. The course published only a market-only export of this session, with sampled trades and no acks, rejects or connects. So uptime and the reject count for Session 3 cannot be measured from it.

### Session 4 (Fri Oct 23)

`scripts/phase1_evidence.py` on the recording (`evidence/session4_replay.txt`). Events: AMZN earnings −7.9% (t+350), ORCA bookbuild t+150–450 / listing t+550, AAPL dividend (t+600), GOOGL earnings −5.7% (t+800), econ print −2.5% (t+1150). `6_rings_trader_2`:

| Check | Result |
|---|---|
| Bid in the ORCA bookbuild | ✅ Allocated 111 ORCA at the $28.00 offer, so `on_ipo` indicated in the book |
| Orders after the opening cross | ✅ Every fill comes after `AUCTION_RESULT` (first at t+812); nothing is sent during the pre-open (auction gate) |
| GOOGL −5.7% | ✅ Bought 52 @ 163.87 near the trough (t+812–817), sold 52 @ 171.31 on the rebound (t+874): about +$387 before fees |
| Econ print −2.5% (FADE) | ✅ Sold MSFT, NVDA, NFLX and INTC in the event tick (t+1150–1155), bought them back 1.4–2.4% lower 23 ticks later (e.g. NVDA 60 @ 112.56 → 110.63) |
| AMZN −7.9% | ⚠️ No fill of ours appears within 60 ticks |

The course again posted a market-only export. It has no acks, rejects or connects, and its trades are a sample (55,251 venue-wide). So the reject count cannot be measured from it. For AMZN it cannot show whether orders were sent and missed, filled but not sampled, or never sent; the bot's rule would have traded it (designed edge 6.3% against 0.6% round-trip fees). The full local rehearsal below covers what this export cannot: 10 orders → 10 ACKs, 0 rejects.

### Full-session log — local rehearsal of the week-4 scenario (`evidence/local_week4_rehearsal.log`)

Local exchange with `GAME_WEEK=4`; `6_rings_broker` and 2 retail-flow bots provided liquidity; 817 ticks. Every required item appears in the log:

| What | Log lines |
|---|---|
| Handshake, initial resync | `CONNECT #1`, `RESYNC from leaderboard` |
| Starting-share grant | `GRANT starting shares (no trade print): +20 × 10 symbols` |
| Pre-open respected; first order after the cross | auction 12 ticks, nothing sent; first order at the AMZN event |
| Orders acknowledged | **10 `Order sent` → 10 `ACK`, 0 `REJECT`** |
| Fills applied | **26 `FILL` lines**, each with the updated position, entry and P&L |
| AMZN +3.5% (RIDE) | bought 130 @ 197.43 → sold 130 @ 204.95 within 16 s |
| GOOGL −7.4% (FADE) | sold 20 @ 181.83 at the event → bought 170 @ 165.42 near the trough |
| IPO | indicated 150 @ $28 → allocated 150 @ $24.00 → listed |
| Induced drop | `INDUCED DROP after 480s` → `RECONNECT in 0.7s` → `CONNECT #2` → `RESYNC … books already agree` |
| Close | **`RECONCILE at close vs leaderboard — OK`**: positions, entries, cash, fees and realized P&L (+1,161.15) all match; non-trade cash flows (interest + AAPL dividend) +165.84 |

### Live arena (10/24) — `evidence/live_arena_reconnect.log`

The bot resynced 11 carried positions from the leaderboard on connect, including ORCA 111 @ 28.00. An induced drop was followed by a reconnect 0.6 s later, and the books came back with `books already agree`. All 10 of the 30 s `BOOKS` checks reported `matches exchange`.

### Tests — `evidence/pytest.txt`

268 passed, covering the ledger against the exchange's math, the reconnect, the leaderboard resync and wire typing.

## 6. Known limitations

- After a resync from a `PortfolioUpdate` alone, the entry of a new or flipped position is approximated by the mark until the next leaderboard row corrects it.
- Interest and carry are absorbed only up to a plausibility bound per row (max($5, 0.01% of cash)). A larger unexplained cash gap stays a MISMATCH.
