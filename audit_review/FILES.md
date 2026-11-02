# Audit deliverables

- `REPORT.md`: executive verdict, complete metrics, evidence quality, severity-ranked findings, strategy attribution, charts and five-priority roadmap.
- `issues.json`: machine-readable code audit with files, functions and lines.
- `results/strategy_metrics.csv` / `.json`: fresh and stress metrics, including net/gross P&L, commissions/rebates/cancels, slippage, session Sharpe, drawdown, overlay FIFO profit factor/win rate, turnover, fills and observed risk failures.
- `results/final_validation.json`: complete matrix sizes, frozen code fingerprints, observed safety diagnostics and offline connection guard check.
- `results/reproduction_verification.json`: exact archive reproduction checks.
- `results/paired_correction_effects.csv`: matched-seed effects of infrastructure fixes.
- `results/historical_comparison.csv`: external-input historical replays and exact archived-result deltas.
- `results/pairwise_comparisons.csv`: paired strategy differences and bootstrap intervals.
- `results/event_manifest.csv`, `event_direction_conditions.csv`, `volatility_regimes.csv`, `regime_conditions.csv`: regime coverage and conditional results; these are descriptive, not causal attribution.
- `results/*_protocol.json`, `effective_configuration.json`, `environment.json`, `source_verification.json`, `historical_inputs.json`: reproducibility and input provenance.
- `results/archive_tests.xml`, `audit_tests.xml`: executed final test results.
- `figures/`: equity, drawdown, cross-seed P&L distribution, cost decomposition, parameter sensitivity and stress charts.
- `results/{reproduce,fresh,stress,paired_corrections}.jsonl`: full local equity paths and account-tagged fill ledgers; ignored by Git because of size.
- `changes.patch`: review patch for audit source, report and findings; numerical outputs remain separate artifacts.

## Corrected code and diagnostics

- `code/audit_study.py`: corrected research gateway, per-account fill diagnostics, unchanged archived strategies, deterministic parallel studies.
- `code/atomic_exchange.py`: separate buffered-settlement fixture fixing the demonstrated ACK suspension race. It is not a complete production venue replacement. The held-MOC defect remains unresolved.
- `code/fifo.py`: per-account overlay FIFO, matching trading changes relative to endowed inventory.
- `code/test_audit.py`, `test_exchange_concurrency.py`, `test_fifo.py`: 14 regressions/diagnostics. Some intentionally demonstrate bugs in the original venue; passing does not mean that venue is fixed.
- `code/network_guard.py`: rejects non-loopback socket connections for study/test processes.
- `code/build_report.py`: completeness checks, metrics, rankings and static figures.
- `code/paired_corrections.py`, `reproduce_history.py`, `event_coverage.py`: additional matched comparisons, checksum-verified external tapes and event coverage validation.
- `code/extract_archive.py`, `verify_sources.py`, `run_archive_tests.py`: archive setup and baseline verification.
- `conftest.py`: excludes extracted copies and isolated audit diagnostics from repository-wide pytest recursion; diagnostics still run by explicit file path.
- `code/run_root_tests.py`: guarded verification of default repository test collection.
- `code/findings.py`: reviewed finding definitions used to build the report.

The extracted archive is preserved under `extracted/2026Autumn-6-fresh/` and ignored by Git. The audit does not edit `.env`, credentials, live bots, root exchange code, or unrelated user changes. No hosted exchange orders are placed.

## Run

From the repository root, use Python 3.10. If the extraction is absent:

```bash
python3 audit_review/code/extract_archive.py --archive ../alpha_review_light.zip
```

Then:

```bash
python3 audit_review/code/verify_sources.py
python3 audit_review/code/run_archive_tests.py
python3 audit_review/code/run_root_tests.py
python3 -m pytest audit_review/code/test_audit.py audit_review/code/test_exchange_concurrency.py audit_review/code/test_fifo.py -q
python3 audit_review/code/audit_study.py --stage reproduce --workers 8
python3 audit_review/code/audit_study.py --stage fresh --workers 8
python3 audit_review/code/audit_study.py --stage stress --workers 8
python3 audit_review/code/paired_corrections.py
python3 audit_review/code/reproduce_history.py
python3 audit_review/code/event_coverage.py
python3 audit_review/code/validate_outputs.py
.venv310/bin/python audit_review/code/build_report.py
```

Historical replay requires the three external `sessions/` recordings listed in `results/historical_inputs.json`; they are not included in the ZIP. The report uses the existing `.venv310` installation of NumPy/Matplotlib. Study commands overwrite only their own audit output files; preserve these files before changing experiment definitions. The final report builder refuses incomplete matrices.
