# Trader 1 deployment-readiness report

Status: **READY FOR YOUR DEPLOYMENT DECISION (corrected baseline).** Event2 is verified and selectable but is a separate risk decision. Nothing has been pushed, merged, deployed or connected to the hosted arena by this work.

## Winning candidate and exact production configuration
- **Default (recommended): corrected baseline** = `team.trader.MyTrader` with `TRADER_STRATEGY` unset (or `baseline`). Week-aware activation (weeks 5-10), ARENA10 futures guard (plans, reaction guarantee, outbound), IPO behaviour unchanged, all deployed risk limits unchanged (seat cap 150, gross cap 70,000, cash reserve 0.60).
- **Opt-in: Event2** = `TRADER_STRATEGY=event2`, frozen defaults: cap_shares 150, gross_cap 105,000, cash_reserve 0.35, ev_notional 0.30, ev_maxsyms 6, ev_entry 0.5, ev_take 0.9, ev_time 3, ev_stop 0.5, ev_window 0.5, ev_chunk 60, ev_kill 0.03. Overridable with `EVENT2_<FIELD>` env vars. It relaxes two deployed risk limits (gross 70k->105k, reserve 0.60->0.35): your decision.
- Fallback / rollback: unset `TRADER_STRATEGY` (baseline), or restore the original files from commit e4d8c54 (kept verbatim as `research/legacy_trader_e4d8c54.py`).

## Final paired-seed results (untouched seeds 4001-4100, run once, frozen at bc5dbac; independently recomputed from raw artifacts)
| comparison | dScore (95% CI) | P(win seed) | dPnL |
|---|---|---|---|
| corrected baseline vs ORIGINAL deployed Trader 1 | +0.098 [+0.080,+0.117] | 0.91 | +$1,144 (worst seed +$140) |
| Event2 vs ORIGINAL | +0.175 [+0.143,+0.209] | 0.93 | +$2,535 (worst +$421) |
| Event2 vs corrected baseline | +0.077 [+0.051,+0.104] | 0.78 | +$1,391 (worst -$1,114) |
| Event2 vs equal-relaxed-capital baseline | +0.059 [+0.034,+0.086] | 0.66 | +$494 |
Audit: final seeds never used outside stage `final`; all 1000 final rows started after the freeze commit and carry one source hash identical to the current source; 0 liquidations, 0 accounting errors, 0 non-handshake wire errors; max drawdown original 1.84% / baseline 1.01% / Event2 1.21%.

## Test and rehearsal outcomes
- Full suite: **353 passed, 3 skipped.** Skips = hosted-exchange / external-AI / live-market-data smoke tests that require `RUN_LIVE_SMOKE=1`; deliberately NOT run (they connect to the live arena).
- Source-hash checks: PASS. `team/broker.py` equals original pin; `team/trader.py` and `team/event2.py` equal `tests/production_pins.json` (reason + approval recorded); original trader control verified byte-identical to the original pin.
- Accounting reconciliation: per-tick identity check in every harness run; exact ledger==exchange at close in all rehearsals.
- Real-process rehearsal (`python -m team.trader`, seat `6_rings_trader_1`, isolated local exchange, real 1 s ticks, week 6, 700 ticks, server drops the socket at tick 350, SIGINT shutdown), script-verdict **PASS for BOTH**:
  - baseline: 7 orders / 7 fills / 7 IOC-complete, 0 rejects, reconnect CONNECT #2 within ~1 s + resync, `RECONCILE at close OK`, ledger cash == exchange cash ($171,015.95), positions equal, no resting orders after close, duplicate seat refused by lock, SIGINT exit clean, lock released.
  - event2: 9 / 9 / 9, 0 rejects, trading halt on INTC handled, reconnect at 0.7 s, `RECONCILE at close OK`, ledger == exchange ($172,354.93), same lifecycle checks pass.
  - Process-level issue found and fixed in the TEST (not the bot): first attempt reported FAIL because the script polled once per tick and could not see a 0.7-1 s reconnect; criterion now reads the bot's own log. The bot's behaviour was correct in all four runs; no production code or strategy was changed, so the final evaluation remains valid (source hash unchanged).
- In-process WebSocket rehearsals weeks 6-10 with forced reconnects (earlier): 0 accounting errors, 0 liquidations.

## Remaining risks
1. **Simulator-only evidence**; no real-market out-of-sample. Score per run is one week.
2. **Event2 is not proven under the deployed limits** (dev2 +0.016, n.s.); its gain needs the relaxed limits, is concentrated in weeks 7 and 10, ~0 in weeks 8-9, absent in thin books, negative when team headroom is small, and wins only 66% of seeds vs an equal-capital baseline.
3. **Team headroom unknown to the bot**: bots only enforce their own seat cap (150); other seats' holdings can trigger `POSITION_LIMIT` rejects -> fail-closed reconnect storms (one stress seed showed ~19 reconnects and a ~$3 ledger drift in both bots; resync repairs it live, cause not established). Keep `SEAT_CAPS` in `team/risk.py` consistent with other seats.
4. **SYMBOL_HALTED races**: an IOC sent on the tick a velocity halt starts is rejected and forces a reconnect (both bots; recovered in all runs).
5. **Short path not exercised in the real-process rehearsal** (no negative event hit the two week-6 runs); covered by unit tests and harness (100+ seeds) only. Weeks 7-10 were rehearsed in-process, not as the real process. Hosted rate limits/Week-7 quotas not tested.
6. **A Trader 1 process is currently running from your main checkout against `wss://feed.algoarena-st.duckdns.org`** (pid 69881, started 10:38, original code). I did not start, touch or signal it. Deploying means replacing files and restarting it - your call and timing.
7. The 3 skipped live smoke tests were not run.

## Exact files ready for deployment (copy from the research worktree/commit; nothing else differs from e4d8c54)
| file | sha256 (first 16) | change |
|---|---|---|
| `team/trader.py` | 15b65c0b56e7eefb | modified (+103/-15): GRADED_WEEKS activation, futures guard, short-gate/SSR state, Event2 hooks, TRADER_CASH_RESERVE |
| `team/event2.py` | 70336c502d482c77 | NEW (204 lines) |
Unchanged and required as-is: `team/broker.py`, `team/ledger.py`, `team/risk.py`, `week2/week02_alpha_bot.py`. Test-side (not deployed): `tests/test_alpha.py`, `tests/production_pins.json`, `tests/test_trader1_integration.py`.
Deploy steps (when you authorize): copy the two files, run `python -m pytest -q`, restart Trader 1 with `TRADER_STRATEGY` unset. Do not set `TRADER_STRATEGY=event2` unless you accept risk item 2-3.
