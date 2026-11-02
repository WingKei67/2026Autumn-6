# Alpha Discovery Round II

**No new strategy robustly beats the original bot.** See [ROUND_II_REPORT.md](ROUND_II_REPORT.md) for full paired confidence intervals, costs, risk counters, charts and reproducibility.

The strongest observed relationship is **remaining synthetic event repricing**: publicly announced magnitude, last pre-announcement mid and the observed phase of the programmed shock help predict subsequent prices. The original already consumes this event information. The event model and its allocator improve on Round I combined, but they do not outperform the incumbent on 100 untouched paired test seeds.

**Peer returns** show some synthetic continuation around common macro shocks. Their separate strategy earns little beyond no-trade and loses that advantage under doubled taker fees. Shared shocks and bundled background spread/depth/refresh regimes confound a causal cross-asset interpretation. Microprice, imbalance and trade-flow features have not independently demonstrated incremental after-cost profitability.

**Overshoot reversal is unsupported**: only one qualifying validation opportunity was found. It remains disabled. The third implemented strategy is an allocator of event and peer predictions, not a third independent alpha.

The earlier label screen excluded opportunities encountering future halts. That selection invalidates its model-support confidence claims. A separate corrected diagnostic retains intermediate halts, scores rejected arrivals, delays exits and counts censoring; it was tested but not used to refit or tune the frozen policies. Correcting this selection does not necessarily lower a sample mean. Actual exchange OOS execution, including all halts, determines the final rankings.

Across three previously inspected recordings, the event strategy never trades and the active new strategies lose relative to endowed no-trade. No untouched historical holdout is available. The synthetic event advantage has therefore not demonstrated empirical transfer.

Research outputs and deferred work are recorded in [experiment_status.json](round2/results/experiment_status.json); [oos_metrics.csv](round2/results/oos_metrics.csv) ranks execution outcomes. Further model tuning requires a new development cycle and new sealed test seeds. Preserve the incumbent until that evidence exists.
