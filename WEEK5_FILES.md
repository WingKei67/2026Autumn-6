# Files repaired for Week 5

Unrelated changes to README.md and lab1_race.py were left alone. `.env`,
registration, data/reports, and existing logs were not overwritten.

## Restored tracked files

These were absent at the start and restored from HEAD. Restorations matching
HEAD do not appear in a normal Git diff.

* `team/ledger.py` — real fill-based signed average-cost ledger and reconciliation.
* `team/__init__.py` — package initialization.
* `team/config.py` — original configuration facade.
* `team/exchange.py` — original exchange entry point.
* `team/README.md` — original package documentation.
* `scripts/backtest.py` — backtest entry point.
* `scripts/create_team.py` — registration tooling.
* `scripts/phase1_evidence.py` — original evidence tooling.
* `scripts/replay_session.py` — session replay tooling.
* `scripts/run_phase1_rehearsal.py` — isolated rehearsal tooling.
* `scripts/season_sim.py` — restored simulator, then repaired its engine import.
* `scripts/tca_report.py` — original execution analysis tooling.
* `scripts/test_remote.py` — original remote test tooling; not executed.
* `tests/conftest.py` — engine paths and isolated persistence fixtures.
* `tests/sim_session.py` — original simulator entry point.
* `tests/test_arena_sdk.py` — original SDK regressions.
* `tests/test_ledger.py` — original ledger regressions.
* `tests/test_maker_taker.py` — original fee regressions.
* `tests/test_messages.py` — original schema regressions.
* `tests/test_orderbook.py` — original matching regressions.
* `tests/test_phase1_final.py` — original reconciliation regressions.
* `tests/test_reconnect.py` — original local WebSocket regressions.
* `tests/test_risk_controls.py` — original exchange risk regressions.
* `tests/test_smoke.py` — restored, then updated for Python 3.10 and explicit live opt-in.
* `tests/test_wire.py` — restored, then updated with realistic authorized account fixtures.

## Modified or added implementation

* `.gitignore` — ignore isolated Python environments.
* `Makefile` — Python 3 selection, isolated environment default, engine module path.
* `requirements.txt` — correct the Python 3.10 support declaration.
* `engine/broker/broker.py` — overridable error dispatch for correlated completion handling.
* `engine/trader/trader.py` — preserve authoritative liquidated status in account mirrors.
* `engine/exchange/scenario.py` — load the actual repository season directory by default.
* `engine/exchange/server.py` — replay authoritative halt state on handshake (local server).
* `scripts/season_sim.py` — bootstrap engine imports for direct execution.
* `team/trader.py` — pending IOC risk, snapshot gates, cash reserve, deduplication,
  halt/reconnect state, reduction-first resumption, and local seat lock.
* `team/broker.py` — bounded IOC quotes, exact completion handling, cash/share/gross
  limits, fresh snapshots, throttled quoting, halt/resume evidence, and seat lock.
* `team/risk.py` — shared seat allocations, structured exposure telemetry, local locks.
* `scripts/week5_launch.py` — dry-run by default; explicit live supervisor.
* `scripts/week5_audit.py` — read-only log evidence and observed exposure maxima.
* `tests/test_week5_safety.py` — adapt existing overlay tests to confirmed IOC completion
  and preserved reconnect halts; retain original safety coverage.
* `tests/test_week5_regressions.py` — 23 new regressions, including actual bots with the
  real exchange handlers, shared-team caps, full inventory loss, and ACK correlation.
* `tests/test_smoke.py` — Python 3.10 support; external calls require RUN_LIVE_SMOKE=1.
* `tests/test_wire.py` — authoritative account/price fixture consistent with new risk gates.

## Delivery artifacts

* `WEEK5_READINESS.md` — risk assumptions, results, hosted limitations, schedule, launch.
* `WEEK5_FILES.md` — this manifest.
* `evidence/week5-tests.xml` — actual full-suite results.
* `evidence/week5-season.txt` — actual requested unseeded preset-bot season run.
* `evidence/week5-season-seed5.txt` — second preset-bot run; simulator offsets seed by week.
* `evidence/week5-audit-dry-run.txt` — audit with intentionally missing logs.
* `evidence/week5-changes.patch` — reviewable implementation/documentation diff,
  excluding unrelated user changes, credentials, local data, and environments.
