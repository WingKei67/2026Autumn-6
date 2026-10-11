# Phase 0 — 6 Rings

**Course:** FINM 33500 — Systematic Trading Technologies
**Team:** 6 Rings
**Members:** Yao Zhao, Jiacheng Li, Yiran Shao, Zhuoyan Ma
**Seats:** `6_rings_broker`, `6_rings_trader_1`, `6_rings_trader_2`, `6_rings_trader_3`, `6_rings_trader_4`
**Repository:** https://github.com/WingKei67/2026Autumn-6 (branch `joanna-week1`)
**Date:** October 10, 2026

## Deliverables

| Deliverable | File |
|---|---|
| $1M allocation and justification | `ALLOCATION.md` |
| UML class diagram | `docs/uml/class_diagram.png` (`docs/uml/README.md` maps each box to its file) |
| Connected, `order_ack` received, fills | section 2 below; full excerpt in `docs/evidence/week2_tape_6rings.txt` |
| Code | `team/trader.py` |

All four members confirmed that `make test` passes on their local clone.

## 1. Allocation

One broker desk at $400,000 and four trader seats at $150,000 each, totalling $1,000,000, with no exchange licence. The professor reversed our original exchange-licence purchase and applied this allocation in the arena; our token stayed valid. The table, who runs each seat, and the rationale are in `ALLOCATION.md`.

## 2. Level 1 evidence

Our Level 1 bot (`team/trader.py`, run as `6_rings_trader_4`) subclasses `arena.Trader` and implements `on_tick`: it places one 1-share AAPL limit buy at the best ask after checking `portfolio.can_buy`, then sits out. It overrides `on_fill` (logs each execution) and `on_event`, which re-arms the single order when the arena broadcasts `WEEK_CHANGED`. `on_tick` makes no network calls and does not block.

The evidence below is from the venue's own recording of the Week 2 graded block (`session_week02_20261009_204000.jsonl`, 15:40:00–16:30:02 Chicago time). Other teams' ids are replaced by `<other team>` in the excerpt file.

**Session and calendar events**

| Time (CDT) | Event |
|---|---|
| 15:40:00 | `SESSION_OPEN` — "Trading session is now OPEN." |
| 15:44:46 | `CALENDAR` announcement — "Upcoming: earnings AAPL @ t400" |
| 15:46:52 | `CALENDAR_EVENT` — "Earnings: AAPL moving +6.2%" |
| 15:53:27 | `CALENDAR` announcement — "Upcoming: econ_print MARKET @ t900" |
| 15:55:33 | `CALENDAR_EVENT` — "Econ Print: market-wide moving -2.0%" |
| 16:30:01 | `SESSION_CLOSED` |

**Orders, acknowledgements and fills**

| Time (CDT) | Seat | Message |
|---|---|---|
| 15:40:01.008 | trader_1 | `order_ack` buy 1 AAPL @ 196.92, then `trade_execution` |
| 15:40:01.128 | trader_4 | `order_ack` buy 1 AAPL @ 196.92, then `trade_execution` |
| 15:42:21.922 | trader_1 | `order_ack` sell 1 AAPL @ 194.83, then `trade_execution` |
| 15:48:19.622 | trader_1 | `order_ack` buy 1 AAPL @ 210.93, then `trade_execution` |
| 15:48:59.483 | trader_3 | `order_ack` buy 1 INTC @ 20.68, then `trade_execution` |
| 15:51:20.067 | trader_1 | `order_ack` sell 1 AAPL @ 209.60, then `trade_execution` |

The 15:40:01.128 `order_ack` for `6_rings_trader_4` is our Level 1 order: handshake, acknowledged limit order, immediate fill. Our seats also appear in every leaderboard broadcast of the block.

**Connectivity (15:40:00–16:30:00, 3,000 s)**

| Seat | Disconnect → reconnect | Time offline | Connected |
|---|---|---|---|
| trader_1 | 15:48:17.7 → 15:48:19.1 | 1.4 s | 99.95% |
| trader_3 | 15:45:48.8 → 15:48:53.0 | 184 s | 93.9% |
| trader_4 | 16:19:38.2 → 16:23:50.6 | 252 s | 91.6% |

All three seats reconnected and were connected at the close: at 16:30:02 each received `SESSION_CLOSED` rejections for the engine's automatic post-close flatten orders (rejections, not fills). The recording starts at market open, so the official percentage is the instructor's, taken from the server recording.
