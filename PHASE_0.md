# Phase 0 — 6 Rings

**Course:** FINM 33500 — Systematic Trading Technologies
**Team:** 6 Rings
**Members:** Yao Zhao, Jiacheng Li, Yiran Shao, Zhuoyan Ma
**Seats:** `6_rings_broker`, `6_rings_trader_1`, `6_rings_trader_2`, `6_rings_trader_3`, `6_rings_trader_4`
**Repository:** https://github.com/WingKei67/2026Autumn-6 (branch `joanna-week1`)
**Date:** October 10, 2026

## Deliverables in this submission

| Deliverable | File |
|---|---|
| $1M allocation and justification | `ALLOCATION.md` |
| UML class diagram | `docs/uml/` (class diagram PNG, `message_flow` PNG, `README.md` mapping boxes to files) |
| Connected + `order_ack` + fills, from the venue recording | `docs/evidence/week2_tape_6rings.txt` (section 4 below) |
| Code | `team/trader.py`, `team/broker.py` |

All four members confirmed that `make test` passes on their local clone.

## 1. Team setup and allocation

Our approved allocation is one broker desk funded with $400,000 and four trader seats funded with $150,000 each, for a total of $1,000,000. We hold no exchange licence. The full table, who runs each seat, and the rationale are in `ALLOCATION.md`.

Professor Donadio updated the arena registration directly, reversed the exchange licence purchase, and confirmed that our existing token remained valid. We did not register a second team.

Separate bot IDs are separate trading accounts. Teammates run different seats and must avoid running two processes under the same bot ID. Live account values include existing positions and earlier open-play activity, so they are not evidence of the approved allocation or of a strategy's performance.

## 2. Environment and connectivity

The development environment is Python 3.11 in a project virtual environment. The smoke test reported all six checks passing (Python version, SDK import, AI provider, Yahoo Finance feed, engine/SDK operation, exchange connectivity).

An initial TLS certificate verification failure blocked the trader. Setting `SSL_CERT_FILE` to the bundle provided by `certifi` fixed it. A separate broker launch under system Python 3.10 raised a NumPy/pandas binary-compatibility error; running from the project's virtual environment fixed the feed import, and later broker logs showed `venue+yahoo` quotes.

Credentials are kept out of this report and out of all logs and screenshots.

## 3. SDK design and strategy behaviour

The SDK separates strategy decisions from exchange connectivity. A strategy subclasses `Trader` (the only abstract base class; `Broker` and `Exchange` are ordinary classes with default hooks) and implements `on_tick(market, portfolio)`, returning a `Signal` or `None`. The underlying `TraderBot` handles authentication, market messages, portfolio updates and order submission; an adapter (`_AdapterStrategy`) calls our `on_tick`. `on_fill` records executions and `on_event` receives market events.

Our Level 1 strategy (`team/trader.py`, run as `6_rings_trader_4`) places one 1-share AAPL limit buy at the best ask after checking `portfolio.can_buy`, then sits out. It overrides `on_fill` (logs each execution) and `on_event`, which re-arms the single order when the arena broadcasts `WEEK_CHANGED`, so the bot also trades in the graded scenario if it was started during open play.

The SDK runs its connection on `asyncio`. Our `on_tick` uses only local quote and portfolio checks, with no network calls, sleeps or other blocking work.

## 4. Week 2 graded-block evidence

The Week 2 requirements were: connected for at least 80% of the live block and still connected at the end, at least one fill, and no blocking work inside `on_tick`. The evidence below comes from the venue's own recording of the block (`session_week02_20261009_204000.jsonl`, 15:40:00–16:30:02 Chicago time). Other teams' ids are replaced by `<other team>` in `docs/evidence/week2_tape_6rings.txt`.

**Session and calendar events**

| Time (CDT) | Event |
|---|---|
| 15:40:00 | `SESSION_OPEN` — "Trading session is now OPEN." |
| 15:44:46 | `CALENDAR` announcement — "Upcoming: earnings AAPL @ t400" |
| 15:46:52 | `CALENDAR_EVENT` — "Earnings: AAPL moving +6.2%" |
| 15:53:27 | `CALENDAR` announcement — "Upcoming: econ_print MARKET @ t900" |
| 15:55:33 | `CALENDAR_EVENT` — "Econ Print: market-wide moving -2.0%" |
| 16:30:01 | `SESSION_CLOSED` — "Flatten positions." |

**Orders, acknowledgements and fills (our seats)**

| Time (CDT) | Seat | Message |
|---|---|---|
| 15:40:01.008 | trader_1 | `order_ack` buy 1 AAPL @ 196.92, then `trade_execution` (fee 0.5908) |
| 15:40:01.128 | trader_4 | `order_ack` buy 1 AAPL @ 196.92, then `trade_execution` (fee 0.5908) |
| 15:42:21.922 | trader_1 | `order_ack` sell 1 AAPL @ 194.83, then `trade_execution` |
| 15:48:19.622 | trader_1 | `order_ack` buy 1 AAPL @ 210.93, then `trade_execution` (after reconnect at 15:48:19) |
| 15:48:59.483 | trader_3 | `order_ack` buy 1 INTC @ 20.68, then `trade_execution` |
| 15:51:20.067 | trader_1 | `order_ack` sell 1 AAPL @ 209.60, then `trade_execution` |

The 15:40:01.128 `order_ack` for `6_rings_trader_4` is our Level 1 order: handshake, acknowledged limit order, immediate fill. Our seats also appear in every leaderboard broadcast of the block (1,294 broadcasts), which is the feed behind the class dashboard.

**Connectivity (15:40:00–16:30:00, 3,000 s)**

| Seat | Disconnect → reconnect | Time offline | Connected |
|---|---|---|---|
| trader_1 | 15:48:17.7 → 15:48:19.1 | 1.4 s | 99.95% |
| trader_3 | 15:45:48.8 → 15:48:53.0 | 184 s | 93.9% |
| trader_4 | 16:19:38.2 → 16:23:50.6 | 252 s | 91.6% |

All three seats reconnected and were connected at the close. At 16:30:02 each of them received `SESSION_CLOSED` rejections for the engine's automatic post-close flatten orders (10 per seat); these are rejections, not fills, and show an active connection at the end of the block. The recording starts at market open, so the pre-open period is not visible in it; the official percentage is determined by the instructor from the server recording.

## 5. Results and lessons

The main outcome is a working connection with recorded, acknowledged, filled orders. Displayed net worth includes legacy inventory and earlier open-play state, so its change cannot be attributed to a newly launched strategy, and realised P&L on a sale depends on the account's existing cost basis.

Trading costs were material. The venue schedule is a 30 bps taker fee and a 5 bps maker rebate. The recording shows the full 30 bps charged to the aggressor (for example 0.5908 on a 196.92 share), while the client's `Fill` log line shows half of that (0.2954); portfolio accounting and the venue schedule should be used to interpret costs, not that log line.

The Level 1 strategy is deliberately minimal and makes no claim of profitability; its purpose is to prove the connection, order and fill path.

The Week 2 recap showed that event messages carry the sign and size of the move at the moment the event fires, and that the engine ramps the price over several ticks. That is the most promising input for the next phase. Before deploying any event strategy we will test executable prices, competition, timing and transaction costs.

## 6. Notes

- The `_AdaptedTraderBot` flatten-on-close orders are rejected after `SESSION_CLOSED`; session-boundary order handling is an improvement area.
- `6_rings_trader_1` was to be reset to $150,000 cash with no positions after the Week 2 block; its status should be checked against the current portal.
