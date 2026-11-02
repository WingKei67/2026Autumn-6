# Baseline audit — Six Rings competition optimization

Audited control commit: `e4d8c5422d4e3500008131fb9bb555bb8e8d3a91` (`castor-phase1-final`). Research branch: `research/competition-optimization`; isolated worktree: `/private/tmp/six-rings-research`. All line references below are to the **control commit**, before research edits. No credentials, `.env`, private portfolios, live seat files, or hosted endpoint were read for this audit. Existing research reports are evidence of earlier work, not newly reproduced measurements.

## Architecture and scope

| Component | Responsibility and evidence |
|---|---|
| `team/trader.py` | `MyTrader` subclasses `ShockSwitchTrader`; `_Bot` subclasses the real SDK adapter (106, 448). Signals pass final outbound risk at 911 before typed IOC submission at 995. The adapter owns socket reconnect, message dispatch, ledger, snapshot reconciliation and reservations. |
| `week2/week02_alpha_bot.py` | Event plan state machine: positive-news RIDE, negative-news inventory derisk then FADE; per-event cost filter (129–159), urgency-ranked single signal (189–217), entry/exit logic (221–325). |
| `team/broker.py` | `MyBroker` supplies spread/skew callbacks; `_RiskBrokerBot` owns guarded quote/cancel paths, snapshots, fills and reconnection. Its custom quote path (238–257) bypasses the inventory-skew callback at 351–353. |
| `team/ledger.py` | Signed average-cost accounting (42–57), allocation/grant/dividend and carry accounting (61–81), authoritative resync (85–105), strict positions and tolerant cash/entry reconciliation (120–141), disk persistence (179–190). |
| `team/risk.py` | Independent fixed seat caps, risk telemetry, and local process seat lock (12, 15–24, 27–39). It is not a team-wide transactional risk coordinator. |
| `arena/trader.py`, `arena/broker.py`, `engine/trader/trader.py`, `engine/broker/broker.py` | Student interface and adapters. The trading-loop clock and exchange tick clock are separate. Baseline custom broker loops one symbol per 0.5 wall seconds (`team/broker.py:225–236`). |
| `engine/exchange/server.py` | Execution authority: order validation/matching (816 onward), cancels (1310 onward), fill settlement/fees (1931 onward), tick/carry/SSR (2169 onward), session controls, risk/liquidation and score snapshots (3705–3725). |
| `engine/shared/orderbook.py` | Actual CLOB, price-time priority and monotonic arrival sequence (8, 44, 99), post-only handling (156), IOC does not rest (169). |
| `engine/exchange/config.py`, `scenario.py` | Runtime environment configuration, per-team upgrades and week overrides. `Scenario.apply` changes position cap, quota, maintenance fraction and whitelisted microstructure fields (151–174). |
| `engine/exchange/calendar.py`, `ipo.py`, `upgrades.py` | Public news timing, realized announcement and ramps; IPO subscription/allocation with private fundamental; upgrade catalog and purchase effects. |
| `scripts/season_sim.py`, `engine/sim/arena.py`, `engine/sim/bots.py` | Existing population simulator uses actual server handlers but simulator bot presets, not automatically our real adapters (`season_sim.py:167–175`). |
| `alpha_lab/`, `team/alpha/`, `scripts/alpha_*`, `round2/`, `audit_review/` | Prior strategy research, recorded-tape diagnostics, seeded studies, audit/fixes and tests. These are useful controls and negative hypotheses, not new independent evidence. |
| `tests/`, `round2/tests/`, `audit_review/code/test_*.py` | Existing wire, reconnect, ledger, orderbook, maker/taker, safety, regressions and concurrency diagnostics. Passing a diagnostic that reproduces an exchange defect does not repair that defect. |
| `scripts/run_phase1_rehearsal.py`, `scripts/phase1_evidence.py`, `scripts/week5_audit.py` | Local rehearsal and operational evidence infrastructure. Any research invocation must use isolated paths and local-only endpoints. |

The executable authority and immediately relevant research infrastructure were inspected. This is not a claim that every generated artifact or third-party dependency in the repository was individually read. No `AGENTS.md` was found in the tracked worktree.

## What the control actually does

The trader waits for **realized public** `CALENDAR_EVENT` announcements. A positive event is bought during the first half-ramp, at a limit no greater than `pre × (1 + 0.5 × pct)`. It targets 90% of the announced overshoot, stops at half the event magnitude below entry, and time-exits after three ramp durations (`week02_alpha_bot.py:232–255`). A negative event sells existing inventory early, waits for the trough, buys the rebound and restores original inventory. It never creates a stock short. The fade take-profit is the **pre-event price**, although the modeled post-event settlement remains permanently lower; absent other recovery, the fade can end through its stop/time stop (287–325). This is a plausible loss mechanism requiring experiment attribution, not a measured universal explanation.

The default event parameters are 25% equity notional/symbol, 200 parent maximum shares, 60/order, 1 second/venue tick, 3% drawdown entry kill, 1.5% fade stop, six-ramp fade time stop and a designed-edge/round-trip-fee filter (`week02_alpha_bot.py:73–85`). `MyTrader` reduces the share cap to 150 (81, 291–293). Quiet-market alpha is absent (parent 206–212). The reaction fallback submits a one-share IOC 2% below bid after 20 venue ticks; this usually demonstrates activity without executing, so it is not spread capture (`team/trader.py:336–352`). Resume probes also demonstrate accepted order flow rather than independently established alpha (295–334).

The broker proposes two-share quotes around the venue reference with a full spread at least max($0.30, 0.6% price), widened by recent absolute returns up to 3% (`team/broker.py:249–256,341–349`). However `_send` converts every limit quote to IOC (281–285). A normal bid below reference therefore vanishes immediately unless another participant's resting sell is unusually cheap. There is no resting queue priority and no maker rebate from this order. Long-only inventory means no ask can be sent from flat (299–301). Its `skew()` method exists but custom `quote_symbol()` does not use it. A quote visit with tracked orders only requests cancellations and returns; another full rotation is needed to place again (243–245).

## Capital, inactivity and throughput

| Control | Capital/risk choice | Exact evidence |
|---|---|---|
| Trader | $175,000 configured starting capital; $70,000 gross cap; $122,500 entry equity floor | `team/trader.py:84–86` |
| Trader | $105,000 cash reserve plus 5% buffer on remaining cash, fee allowance at least 0.5%; 150 shares/symbol | `team/trader.py:957–968` |
| Broker | $350,000 starting capital; $75,000 gross cap; $245,000 entry equity floor; $210,000 cash reserve | `team/broker.py:24–26,305–309` |
| Broker | 20 shares/symbol, two-share quotes, one level | `team/broker.py:22,29–30,256,300` |
| Team | Intended independent allocations 150+150+20=320 shares/symbol, below Week 5's 800 and later 1000 caps | `team/risk.py:1–12`; this assumes only those seats and already-compliant inherited inventory |

Cash reserves are minimum **new-risk entry** restrictions, not guaranteed future cash. Market moves, fees, dividends, existing holdings and allocations can change the account. Share caps often bind before gross caps. The nominal trader gross limit is 40% of starting equity; broker limit is 21.43%. Unused allowance alone does not prove increasing it improves score.

The control trader enables trading only when `week == 5` at both `WEEK_CHANGED` (166) and reconnect/session-open (1019), then enforces the flag at `_place_order:915`. The broker has the same equality at 111 and 117, enforced by `_ready:271`. Thus normal secondary-market activity is disabled in Weeks 6–10. IPO is a separate path: the trader returns `None` **when** `_week5_active` is true (145), otherwise it can indicate up to 150 shares within 95% cash (149–151). Broadening that flag without separating IPO eligibility changes IPO behavior too.

Trader additional inactivity gates: auction; session closed/not yet open; resync/first book; unknown entries; halted market/symbol; no authoritative snapshot; snapshot older than 10 seconds; liquidation (913–924). Only one outstanding order **across the seat** is allowed (928–929). Filled quantity is retained at zero until terminal confirmation (363–370), which reduces throughput but prevents uncertain completion being mistaken for risk release.

The broker gates on graded week, continuous session, snapshot age, halt and liquidation (270–274). It handles `SESSION_PREOPEN` and `SESSION_CLOSED`, but omits `SESSION_PRECLOSE` from `_continuous=False` (119). The parent broker also lacks preclose handling (`engine/broker/broker.py:342–359`). Once activation is extended, the new Week 6 closing auction can therefore expose invalid IOC submissions unless the research version explicitly gates preclose.

## Correctness protections versus optimizable conservatism

**Preserve:** authoritative post-connect state; unknown-mark refusal; no known-halt/closed-session orders; account identity checks; fee-aware cash and pending risk reservations; deduplicated fills/ACKs; terminal confirmation; rejection-triggered reconciliation; hard venue limits; local seat locks; explicit reconciliation. Trader fill processing is immediate because portfolio updates can lag (`team/trader.py:616–621`). Reconnect clears uncertain socket state but waits for new account/book data (`490–496`). Broker unknown errors disable snapshots and close socket rather than releasing reservations (208–223).

The IOC barrier is specific: ACK queues a cancel for its exact ID, and `ORDER_NOT_FOUND` on the same serial socket releases the reservation (`team/trader.py:565–613`). It is not a generic maker cancel acknowledgment. The prior audit documents the original exchange matching-before-awaited-ACK settlement race and an MOC reservation defect (`audit_review/REPORT.md:271,351`). A separate patched exchange fixture exists in prior research. **Do not substitute that fixture for the official authority** and do not remove reservations on elapsed wall time. True passive market making needs fresh concurrency and delayed-fill testing.

**Optimizable with evidence:** Week 5-only applicability, constant cash fraction, fixed gross/share limits, serial cross-symbol restriction, quote size/refresh frequency, reference-centered quotes without skew, IOC conversion, long-only strategy choice, constant clock-to-ramp conversion, fixed timing and fee gates, and single-venue operation (`team/broker.py:355–360`). None is proven beneficial to remove wholesale.

## Executable score: exact interpretation

For equity snapshots `e`, call **`exchange.scoring.score_equity(e)`**, not a bespoke Sharpe. `scoring.py:97–119` uses arithmetic consecutive returns, population standard deviation and mean return less the configured cash hurdle. It first clips the ratio to `[-cap, cap]`, then applies:

- Nonnegative ratio: `ratio × max(0, 1 - penalty × max_drawdown)`.
- Negative ratio: `ratio × (1 + penalty × max_drawdown)`.

The final negative score can therefore exceed the nominal ratio cap in magnitude. It must not be clamped a second time. Fewer than three snapshots gives score zero. Drawdown is measured on the scored snapshot path, which can miss intra-snapshot losses; report high-frequency drawdown separately.

The configured hurdle is `(1 + CASH_INTEREST_PER_TICK) ** SEASON_SNAPSHOT_TICKS - 1`, computed at module import (`scoring.py:51–58`). Source defaults are interest 0.000002/tick, snapshot spacing 60, penalty 2, volatility floor max(hurdle, 0.0001), ratio cap 10. These are **source defaults, not verified hosted settings**. A research run must serialize the actual imported values and scenario-adjusted configuration, and avoid mutating config snapshot spacing after scoring import without matching the hurdle. No `.env` needs to be inspected to do so.

Crucially, executable `server.snapshot_equity()` scores **reference-price equity**, rounded to 4 decimals (3720–3723), deliberately avoiding own-quote marking. `docs/SEASON_GUIDE.md:151–155` still says conservative bid/ask marking; that description is stale. `HeadlessArena.equity` is conservative bid/ask equity (569–573), so passing that series directly into `score_equity()` gives a different objective. Use `server.equity_history` for the official score and conservative marks for liquidation/exit diagnostics. Standings are ranked separately by role (`scoring.py:184–198`).

Economic consequences to test: below the volatility floor, scaled genuine excess gains can improve score; above it, approximate mean/vol scale cancellation leaves drawdown/cost changes decisive. More trades can reduce smooth excess returns through fees or improve them through diversified realizations; count alone carries no score reward. Cash interest largely cancels its hurdle, but endowed inventory, forced close snapshots and unequal snapshot intervals prevent assuming every inactive account scores exactly zero. Negative-tail losses worsen both ratio and sign-aware drawdown multiplier.

## Weeks 6–10 and runtime gates

All five files enable shorts, post-only, circuit breakers and multi-venue; all score and have 1000-share position limits. All have 8% LULD, a closing auction, and public price-event directions drawn only when released. Config overrides must be applied/restored per scenario, not carried accidentally from another experiment.

| Week | Quota | Latency / cancellation fees | Borrow pool | SSR trigger | Opening / closing auction | Main public events |
|---|---:|---|---:|---:|---|---|
| 6 | runtime default | off / off | 2000 | 6% down | 12 / 10 ticks | INTC 250; VLTA subscription 150–420, listing 520; MSFT 600; NVDA dividend 800; macro 950 |
| 7 | 8/tick | on / on | 1200 | 8% down | 12 / 10 | TSLA 400; macro 650; AAPL 900; META dividend 1200; purchase window |
| 8 | 8/tick | on / on | 1000 | 8% down | 12 / 10 | GOOGL 300; LUNA subscription 150–450, listing 550; NFLX 700; macro 1000 |
| 9 | 10/tick | on / on | 800 | 8% down | 12 / 10 | macro 350; MSFT dividend 500; NVDA 700; futures enabled |
| 10 | 10/tick | on / on | 600 | 8% down | 15 / 12 | AAPL 250; TSLA 550; macro 850; AAPL dividend 1100; futures enabled |

Evidence: `season/week06.json` through `season/week10.json`. Week 6 explicitly sets maintenance fraction 0.4 (line 15); later files omit it, so use runtime/scenario restore semantics rather than silently retaining 0.4 or Week 5's 0.5. Defaults: taker 15 bps and maker rebate 10 bps (`config.py:633–634`), cancel $0.05/order (521), latency 200 ms versus colocated 20 ms (568–569), borrow 0.00001/tick, margin cash carry 0.00002/tick (140–141). Fees can come from runtime/roster, so log effective values rather than declaring defaults universal. Quotas meter **orders and cancels**, with default burst 3× quota (`config.py:507–521`).

Calendar public previews omit direction (`calendar.py:75–98`); realized `pct` is legitimate only after the event. Ramps walk to overshoot then settle to permanent shock (`158–175`). IPO offer and allocation follow actual demand and affordability (`ipo.py:114–152`). Hidden fundamental is keyed by symbol and a separate fundamental seed (`50–58`): changing only Python's general random seed does **not** necessarily vary IPO outcomes. Never reveal this seed or realized hidden pop to the strategy.

Upgrade source prices: position limit $30k, margin $40k, fee tier $40k, quota $30k, colocation $50k, calendar $35k, locate $35k, risk shield $45k (`upgrades.py:55–64`). Source advertising/earlier ROI claims do not establish value for this bot. Purchases require paired cost-deducted remaining-horizon evaluation; no purchase is authorized by this audit. Calendar feed buys earlier timing, not direction (153–159).

## Existing evidence and historical limits

No session `.jsonl`/`.gz` recordings are present among tracked worktree files. `sessions/`, `hw4/`, and `week4/` directories are absent. Existing history tooling and reports reference external tapes; `audit_review/REPORT.md:31` records that its archive omitted all three recordings. This is a portability/data blocker, not permission to fabricate fills. A historical market tape alone cannot establish private order acknowledgment/reject history, queue identities, unobserved cancellations or counterfactual market impact. Passive execution cannot be reconstructed faithfully from top-of-book/aggregated-depth observations.

Earlier inherited results (not reproduced by this audit): `alpha_results/study/REPORT.md:207–211` reports event-momentum excess P&L of −$150.10/−$43.01/−$229.84 on Weeks 2/3/4 tapes; adding fill commissions leaves −$74.87/−$30.47/−$156.32. Those variants differ from today's exact `MyTrader` control. The diagnostic conclusion is that trading costs alone did not explain losses: price confirmation arrived without sufficient subsequent continuation. Do not present those results as observed executions of a new candidate or an untouched test. `ALPHA_DISCOVERY.md:13–15` explicitly says no pristine historical holdout and no demonstrated transfer of the synthetic advantage.

The previous local rehearsal report (`PHASE1.md:14`) records 2400 ticks, 53 fills, zero trader rejects, induced reconnect and allocation; it validates prior operational behavior, not future profitability. Current research must reproduce exact control metrics with the real adapters and official snapshot score. **New baseline reproduction metrics are pending the parent experiment runner and must be linked from the registry/results, not invented here.**

## Backtesting parity gaps to close

The built-in `HeadlessArena` correctly uses server order entry (`arena.py:490–491`) and price-time orderbook, but disables all modeled outbound latency (240–247), shortens halts to 0.05 wall seconds (249–254), serializes one action per bot per venue tick (552–565), and collects its own differently-marked equity series. It catches bot exceptions into `bot.errors`; ignoring that list can silently score a failed bot. Its built-in direct observation API can expose more than the real wire adapter. Therefore a new harness must feed typed public messages through actual `MyTrader`/`MyBroker` plumbing, reserve pending risk, preserve event ordering, and clearly separate virtual-clock mechanics from WebSocket transport evidence.

Persistence must be tested across weeks rather than treating each week as an independent fresh portfolio. Realistic cancellation races and asynchronous outbound latency require local transport rehearsal. Opponent cash/grants, symbols, message costs and capital must be specified. A no-trade endowed seat is distinct from a truly cash-only control.

## Prioritized research decisions

1. **Correctness blocker:** preserve control; fix explicit Week 6–10 activation and broker preclose gating in research, independently gate IPO. Preserve all reconciliation/session/halt protections.
2. **Measurement blocker:** score actual reference-price equity snapshots with effective runtime values; adapters, accounting and error assertions precede strategy ranking.
3. **Highest-value broker hypothesis:** real post-only quoting, inventory reservation and skew, with concurrency/cancel proof. Compare against unchanged IOC; examine rebates and adverse selection separately.
4. **Highest-value trader hypothesis:** public post-release long/short momentum with clock-correct remaining edge, robust exits and SSR/locate handling. Test fade-to-settlement rather than assuming return to pre-event price.
5. **Sizing:** compare exposure regions with paired seeds and official score, keeping fixed conservative control and hard team allocations. Capital utilization is a diagnostic, not a goal by itself.
6. **Predictive signals:** cheap imbalance/microprice/flow/reversion only if fees and actual fills permit. Existing reports give no reason to prioritize deep ML.
7. **Execution diversification:** add public venue books and fee-aware routing only after non-atomic legs and cross-venue limits are modeled; baseline broker uses one venue.
8. **IPO/upgrades:** separately seeded private outcomes and paired purchase costs. Do not promote on the catalog's expected effects.
9. **Promotion:** sealed independent seeds, scenario/opponent variation, confidence intervals at run level, complete safety/accounting checks and local WebSocket evidence. Without these, retain incumbent and report NO-GO.
