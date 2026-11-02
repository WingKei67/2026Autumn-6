# Round II preserved research and completion artifacts

The primary deliverable is `../ROUND_II_REPORT.md`. `../ALPHA_DISCOVERY.md` points to the completed research verdict.

Existing Round II strategy files, frozen model and selected parameters were preserved for the final test. `features.py` still contains the biased legacy training labels deliberately: changing them would obscure which model was actually selected and evaluated. Use `labels_review.py` for subsequent research. Do not describe the old support flags as valid statistical confidence.

Completion additions:

- `finish_experiments.py`: reduced cost/shock suite and historical completion; refuses output overwrites and resumes only unsaved historical combinations.
- `labels_review.py`: corrected diagnostic labels with rejected entry, delayed exit and censor accounting.
- `label_diagnostics.py`: diagnoses existing validation data without refitting.
- `tests/test_labels_review.py`: four targeted label regressions.
- `completion_report.py`: regenerates status, metrics, accounting decomposition, figures and reports from saved runs only.
- `results/experiment_status.json`: completed/partial/deferred inventory, counts and hashes.
- `results/test.jsonl`: actual 100-seed paired execution journals and equity paths.
- `results/priority_stress.jsonl`: fixed 20-seed cost/shock stresses.
- `results/historical_frozen.jsonl`: five preserved outputs from the interrupted historical batch.
- `results/historical_remaining.jsonl`: ten previously unsaved combinations after fixture repair.
- `results/verification.json`: risk, timing, provenance and rejection counters.
- `results/oos_metrics.csv`, `priority_stress_metrics.csv`, `historical_metrics.csv`, `pnl_attribution.csv`: detailed measured outcomes.
- `figures/`: before/after equity, drawdown, paired P&L and cost charts.

`history_eval.py` was repaired to import the feature index from the correct module and preserve the real exchange's closed-session rejection for orders already in transport. No frozen synthetic trading code changed during test evaluation. The original historical fixture and its failed log remain intact.

Large raw outputs are Git-ignored but preserved locally. `round2_changes.patch` includes reviewable Round II source, frozen models/protocol, result summaries and reports; it does not contain recording data, credentials or live-account state. Original project changes shown by Git predate this completion and are not included in that patch.
