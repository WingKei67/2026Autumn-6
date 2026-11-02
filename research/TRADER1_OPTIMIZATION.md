# Trader 1 optimization record

Protocol (frozen in `research/SPLITS.json` / PROGRESS.md before use): dev seeds 11-70 (search on 11-30), validation 101-160, sealed 1001-1100 (used once). One seed = one independent run; weeks rotate 6-10; 1500 ticks (covers every scheduled event).

1. Design stage (dev seed 11 trace, informed `event2` design): the deployed/corrected bot buys ~130 shares (~$3k) on a +5% INTC event; depth at the touch is 1-30 shares so entry is depth-limited.
2. Random search: 24 random configs of 10 parameters (`research/rsearch.py`, 20 dev seeds). Max-t paired bootstrap (`research/maxboot.py`) against the default: best config +0.022, adjusted p = 0.28 (critical t 3.82). **No tuned config beat the default; defaults retained.** Raising notional or caps did not help; low take-profit (0.6) and low entry fraction (0.3) hurt.
3. Validation and sealed runs used the frozen defaults only.

Evidence that more capital is not the lever: gross exposure averages $95k (max $136k) for event2 vs $71k for season_trader, while drawdown p95 stays under 1%; the gain is limited by displayed depth and by the number of events (3-5 per week), not by risk limits.
