# Week 5 graded block — 6 Rings (final audited patch, Oct 29)

This ZIP is an **overlay**, not a whole repository. It contains `team/trader.py`, `team/broker.py`, `Makefile`, `tests/test_week5_safety.py`, and `scripts/week5_audit.py`. **It does not contain `.env` or credentials.**

## Install now (macOS)

1. Stop any old trader/broker processes (`Ctrl+C` in their terminals). This is essential: don't leave an outdated bot trading in parallel.
2. Download the patch ZIP and copy it into your *existing* `2026Autumn-6-fresh` root. Extract, overwriting the files above. Do not replace or delete your existing `.env`.
3. From the project root, run:

   ```sh
   python3 --version
   python3 -m pip install -r requirements.txt
   python3 -m pytest tests/test_week5_safety.py -q
   make test
   ```

   `Makefile` now uses `python3` on macOS, including `make trader`, `make broker`, `make season`, and `make test`.

4. Verify all bot names are registered in the dashboard and `.env` exists. **Do not print, paste, or commit its token.**

## Launch (three terminals, project root)

```sh
TEAM_ID=6_rings_trader_1 TICK_INTERVAL=0.1 make trader
```

```sh
TEAM_ID=6_rings_trader_2 TICK_INTERVAL=0.1 make trader
```

```sh
TEAM_ID=6_rings_broker make broker
```

These run indefinitely; leave them open. On reconnect they should recover automatically. Avoid other manually launched seats or scripts that trade on the same team.

## What is enforced

- Week 5 config: hard exchange TEAM-wide per-symbol limit ±800 shares; per-account forced liquidation below 50% of registered starting capital; 8% LULD limit/IOC price band; circuit breakers on.
- Each trader: 150 long shares per symbol; $70,000 gross cap including outstanding IOC buys on other symbols; cash buffer; no new buys below $122,500 equity; no entries during auctions or halted symbols; a small post-resume order (marketable if valid, otherwise passive) with ACK confirmation; reconnect and session switch cleanup.
- Broker: per-symbol ±20 inventory budget; $75,000 gross cap; limit quote size 2; only quotes during Week 5 continuous trading; wait for authoritative portfolio/leaderboard; disable entries below $245,000 equity; cancel quotes on halts; submit a 1-share IOC probe on resume with an additional reserved 10-share safety allowance per symbol; keep **monotone submitted quantity** in the risk budget even after a cancel attempt because the venue has no cancel ACK. This favors safety over trading volume.
- A `LIQUIDATION` broadcast for some **other account** will not disable your bot.

## Exact live event times — Thursday Oct 29, Chicago (CDT)

- 17:00:00 graded block starts, market closed
- 17:00:20 Week 5 switch and full calendar published
- 17:10:00 opening auction (limit orders only)
- 17:10:12 continuous trading begins
- 17:15:12 NFLX earnings
- 17:22:42 AMD earnings
- 17:27:42 market-wide economic event
- 18:00:00 graded block ends; 18:00:20 back to open play

The published Canvas instructions explicitly say *uptime is not graded*. Keeping the bots connected through the entire 17:00–18:00 block nevertheless maximizes the likelihood of observing halts and resume events.

## Logs / verification

While the block is live, watch for `RISK WEEK 05`, `RISK HALT`, `RISK RESUMED`, `RISK RESUME CONFIRMED`. `REJECT`, `POSITION_LIMIT`, `LIQUIDATED`, `RISK CRITICAL` are warnings. Your broker may print `RISK broker HALT` / `RISK broker RESUMED` instead. Actual halts depend on live price behavior, and the graded recording is controlled by the venue.

After the session, in the project root:

```sh
python3 scripts/week5_audit.py
```

Check the official dashboard and recorded block, too. The audit reads local logs only. If no halt occurs or the bot misses an event while disconnected, no offline test can certify that specific grading item.

## Validation performed here

- **271 passed, 2 skipped** from `make test` with the patched files over the original codebase; the skipped tests explicitly require an external environment / remote check.
- The separate `season_sim.py --weeks 5 --seed 17 --ticks 1200` successfully ran the Week 5 scenario when `SEASON_DIR=season` was set. **This simulator uses predefined headless bots, NOT your three custom network bots**, so its P&L and results cannot be taken as proof for your grade.
- No live authenticated trading was performed by the assistant. Your machine's `.env` was not available in the patch folder.

**There is no honest way to guarantee 100% marks without the session recording and the instructor's full rubric.** This patch focuses on the three published compliance requirements and defaults to much smaller risk than the venue permits.
