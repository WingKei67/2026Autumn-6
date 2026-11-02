# Trader 1 baseline (verified)

Control = deployed `team.trader.MyTrader` at commit e4d8c54, run through the real adapter in `research/harness.py` against the real exchange.

1. **Deployed bot is inactive in Weeks 6-10**: `_week5_active` is set only when week == 5 (`team/trader.py` ~166). It places 0 orders; its raw score is endowment drift. Paired vs the cash control (40 dev seeds): dScore +0.034 [+0.009,+0.062] - noise-level, P(>0)=0.30.
2. **Corrected baseline** (`season_trader`: activation for weeks 5-10 + futures guard): dScore +0.105 [+0.073,+0.137], dPnL +$1,310, P(>0)=0.88.
3. **Futures defect (real)**: the econ-print plan covers every symbol including ARENA10 (cash-settled future); the share-only ledger then disagrees with exchange cash by ~$7-8k (weeks 9/10). The guard lives in research variants only because `tests/test_alpha.py::test_live_source_hashes_are_unchanged` pins production sources; an intentional production change must update that pin with a documented reason.
4. **ORDER_NOT_FOUND** is the intended IOC terminal-confirmation handshake, not an error.
5. Raw scores must never be compared across seeds; only same-seed deltas vs `cash_trader`.
Reproduce: `python -m research.suite batch --stage dev --count 40 --ticks 1500 --names cash_trader,baseline_trader,season_trader && python -m research.paired --stage dev --role trader`
