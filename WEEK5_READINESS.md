# Week 5 readiness audit — October 29, 2026

Implemented and tested locally; no live exchange orders were sent. This file
supersedes the older overlay instructions in WEEK5_README.md.

## Root causes and repairs

The risk overlay was applied to an incomplete tree: 25 tracked support files
were deleted. Restored the original ledger, package initialization, config,
exchange adapter, scripts, and tests from HEAD. The adjacent archive
`../2026Autumn-6-fresh.zip` also contains the original ledger; its bytes matched
the restored implementation. The ledger uses the exchange's signed average-cost
accounting, fee attribution, grants, dividends, allocations, persistence, and
snapshot reconciliation; it is not a replacement stub.

The original risk patch expired IOC reservations after 0.3 seconds, cleared
halt flags on reconnect, and used a lifetime broker submission budget that
quickly exhausted. The repaired clients retain risk until an exact-ID absence
reply follows their IOC on the same socket, deduplicate fills/ACKs, retain
known halts on reconnect, require fresh authoritative account snapshots, and
produce accepted-order evidence after a resume. Uncorrelated errors force a
fresh connection and account reconciliation instead of guessing which order
failed. Expected ORDER_NOT_FOUND completion replies are logged as completion,
not trading rejections. No order is sent during auctions or after close.

The broker normalizes quotes to IOC. A cancelled maker order can disappear
before another client's matching handler finishes reporting its fill; the
hosted protocol supplies no globally ordered cancellation/account barrier.
Avoiding resting orders removes that ambiguity. It sacrifices maker rebates
and passive liquidity. Normal quotes may ACK without filling; post-resume
probes are marketable when safe. Quotes rotate one symbol per 0.5 seconds to
stay below the default per-seat message quota, including completion checks.

## Enforced constraints

* Trader seats: long-only, 150 shares/symbol including pending IOC quantities;
  one outstanding IOC per seat; $70,000 entry gross bound; reserve at least
  $105,000 cash, including outstanding buys and buffered fees.
* Broker: long-only, 20 shares/symbol including pending orders; one order per
  symbol/side; $75,000 entry gross bound; reserve $210,000 cash including buys
  and fees. Buy fee allowance tracks the advertised fee, with a 0.5% minimum.
* All three seats together: independent allocations sum to 320 shares/symbol,
  below the scenario's 800. This assumes these are the only trading seats,
  one gateway per seat, and initial holdings satisfy the team constraint.
* Cash-funded long-only positions cannot create margin debt or short-squeeze
  losses. With the configured $175k/$175k/$350k starting allocations, the 60%
  cash reserve remains above the 50% maintenance baseline even if inventory
  loses its entire value. External debits, pre-existing shorts/debt, and
  different hosted capital allocations are outside this guarantee.
* No new buys below 70% of the configured starting equity. Long liquidation
  sells remain possible; the clients do not send after-close market orders.
* Entry notionals are bounded; subsequent price appreciation can increase
  marked gross. Net absolute notional is bounded by gross for these longs.
* Unknown marks, snapshots older than 10 seconds, and missing snapshots block
  new risk. Prices use a 7% inner collar versus the scenario's 8% LULD band.
* Week 5 IPO indications are disabled because allocations bypass order checks.
* Per-symbol and market halts gate orders; unaffected symbols remain eligible.
  Resume logs require an ACK for a newly submitted order. Other accounts'
  liquidation broadcasts do not disable these bots.

## Exchange facts and schedule

`season/week05.json` specifies limit 800, maintenance fraction 0.5, opening
auction 12 ticks, LULD 8%, and earnings at relative ticks 300 (NFLX) and 750
(AMD), with a market economic event at tick 1050. The server computes the
calendar tick relative to the start of continuous trading. At a one-second
exchange clock and the supplied 17:10 auction start, these yield 17:10:12
continuous trading, 17:15:12 NFLX, 17:22:42 AMD, and 17:27:42 economic news.
The local files do not independently certify the hosted 17:00:20 activation
or 18:00 close; these remain the supplied schedule, not a local scheduler.
Actual halt occurrence/duration depends on the exchange's price path.

## Validation

* `python3 -m pytest -q --junitxml=evidence/week5-tests.xml` on macOS Python
  3.10.11: **293 passed, 3 skipped**. Skips explicitly opt out of external AI,
  Yahoo Finance, and hosted exchange smoke tests. No skipped test is claimed
  as a pass. Local socket tests require permission to bind localhost.
* The 23 new regression cases include actual bot dispatch through real
  exchange matching/risk handlers for each of the three seat IDs. Each shows
  halt gating, an accepted post-resume IOC, a fill, and surviving maintenance.
  A shared-team test fills all three real risk gateways to 150+150+20=320.
  Other cases cover partial/duplicate fills, delayed/concurrent submissions,
  exact limits, cancellation confirmation, stale/missing snapshots, cash,
  equity, unknown marks, reconnect halt reconciliation, and near-total loss
  of long inventory. Existing Week 5 tests also cover rejection bands and
  foreign liquidation events.
* `python3 scripts/season_sim.py --weeks 5`: executed successfully, 1,500 ticks,
  all 3 shocks reached. A second reproducible run with `--seed 5` is recorded
  in `evidence/week5-season-seed5.txt`. These use preset bots, **not our bots**.
  The preset leveraged punter may liquidate; this is not our account.
* Isolated `.venv310` installed from requirements; `pip check` passed and
  pandas/NumPy/yfinance plus both bot modules imported successfully. This
  avoids the incompatible global pandas/NumPy binaries discovered initially.
* `make test`: **293 passed, 3 skipped**, using the isolated Python 3.10
  environment. Launch dry-run creates no connections.

## Remaining hosted dependencies

The local server now replays `MARKET_STATE` on handshake. The original hosted
protocol sends account/session snapshots but does not replay current halts.
If it runs the original server, a resume missed while disconnected cannot be
recovered with certainty from its messages. Clients conservatively preserve a
known halt until TRADING_RESUMED or authoritative MARKET_STATE arrives. They
may remain paused on that symbol after a missed resume; they do not infer
permission to trade from a socket reconnect. A new process also cannot know
an unreported active halt until the exchange sends/rejects it. The hosted
server's replay support, actual starting capital, current positions/debt,
credentials, connectivity, and independent activity are unverified offline.
Portfolio messages lack sequence IDs; stale leaderboard ticks are ignored,
but correctness of unversioned account snapshots relies on the server's
ordered WebSocket stream. Duplicate fill IDs are ignored in the process;
restarts rebuild from fresh account state, assuming no historical fill replay.
Broker restart reconciliation relies on the exchange's default
CANCEL_ON_DISCONNECT=true sweep. Local locks prevent duplicate local seats,
but cannot control another computer or unrelated trading software.

The three grading requirements have local implementation and test evidence;
hosted reconnect-state limitations prevent an unconditional readiness or
full-marks guarantee. No live bots have been started.

## Launch

From the repository root, use three terminals:

```sh
TEAM_ID=6_rings_trader_1 TICK_INTERVAL=0.1 make trader
TEAM_ID=6_rings_trader_2 TICK_INTERVAL=0.1 make trader
TEAM_ID=6_rings_broker make broker
```

Alternatively run the supervisor (do not also launch the terminals):

```sh
.venv310/bin/python scripts/week5_launch.py --live
```

Make automatically uses `.venv310/bin/python` when present. Shell variables
still override `.env`; credentials and account-state files were not replaced.
The supervisor restarts crashed children up to five times; each bot reconnects
on network loss itself. Ctrl+C stops the supervisor and its children.

```sh
python3 scripts/week5_audit.py --date 20261029
```

Audit reports connections, Week 5, halts/resumes, accepted post-resume orders,
observed position/gross maxima, risk rejections, liquidation notices,
disconnects, and crashes. Positions are snapshots, not a complete exchange
fill audit; missing evidence is UNKNOWN. Consult the official recording for
final grading. Current logs remain untouched.
