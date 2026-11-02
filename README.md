# Phase 1 fixes and validation

Base: phase1 branch commit c05b23bcf0a23ab106fdeba46fd6d349b44d3f5c.
This package supplies a patch and separately labelled local validation evidence.
It does not change your existing working directory until you apply it.

## Apply in your repository

    cd /Users/castorzhao/Desktop/FINM_33500/2026Autumn-6-fresh
    git apply --check "$HOME/Downloads/phase1_full_marks_fix/phase1_fixes.patch"
    git apply "$HOME/Downloads/phase1_full_marks_fix/phase1_fixes.patch"
    cp "$HOME/Downloads/phase1_full_marks_fix/evidence/phase1_validation.txt" evidence/
    cp "$HOME/Downloads/phase1_full_marks_fix/evidence/phase1_final_rehearsal.log" evidence/
    cp "$HOME/Downloads/phase1_full_marks_fix/evidence/phase1_rehearsal_console.txt" evidence/

If the check fails, do not force it: your local files differ from the reviewed base.
Review the conflicting context rather than discarding your work.

## What changes where

- team/trader.py: immediate cash mirror includes fees; IPO allocations use the
  announced offer price and can book while unrelated orders are in flight;
  unresolved entries gate individual symbols after initial recovery; reaction
  fallback uses venue ticks and clears on an exchange acknowledgement;
  inferred cash residuals are logged individually and totalled at close;
  the IPO docstring describes eligibility rather than guaranteed allocation.
- team/ledger.py: non-trade cash-flow label acknowledges inferred residuals.
- PHASE1.md: cites the actual IPO model and calculations, explains the no-buy
  decision, identifies local validation and preserves the historical evidence limits.
- tests/test_phase1_final.py: five regression cases for the above failure modes.
- scripts/run_phase1_rehearsal.py: self-contained accelerated local rehearsal.

## Verify on your own machine

    .venv311/bin/python -m pytest -q > evidence/pytest.txt 2>&1
    tail -n 15 evidence/pytest.txt
    .venv311/bin/python scripts/run_phase1_rehearsal.py > evidence/phase1_rehearsal_console.txt 2>&1
    tail -n 15 evidence/phase1_rehearsal_console.txt

For real wall-clock survival evidence (about 40 minutes), run:

    .venv311/bin/python scripts/run_phase1_rehearsal.py --realtime > evidence/phase1_rehearsal_console.txt 2>&1

The default accelerated rehearsal normally completes in under two minutes. It uses a temporary local
venue and separate temporary seat state, not the live arena or live credentials.
It rewrites evidence/phase1_final_rehearsal.log with your own actual run.
An AssertionError means the rehearsal did not pass: inspect it rather than
reporting success. Counts and P&L vary with the generated event directions.
Refresh the validation summary to identify your own rerun results when available.

The recorded sample passed with 2 connections, 33 fills, zero trader rejects,
an ORCA allocation and final reconciliation. See evidence/phase1_validation.txt.

## Remaining original-Friday evidence

Obtain the official Part A pass/partial/fail lines or full private audit records
for the October 16 and 23 sessions. Market-only exports do not establish uptime
or rejection counts; replaying that same public stream does not restore them.
A new local rehearsal cannot change historical Part A performance.

## Submission reference

The reviewed phase1 BRANCH points to c05b23b; the phase1 TAG still pointed to
60ed0f3 when checked. Submit a pinned corrected commit URL or corrected ZIP.
If the course strictly requires the exact phase1 tag, coordinate its update
with your team; this package does not move any shared tag.

## Scope of the assessment

No full mark is guaranteed. This patch addresses the confirmed implementation
issues and the missing revised local close-reconciliation demonstration. The
original Part A readiness result and the instructor's grading remain separate.
The IPO model is already present in engine/exchange/ipo.py (POP_MU=0.12,
POP_SIGMA=0.18). TICK_INTERVAL changes the bot polling loop, not the exchange's
one-second venue clock. Week 4's published Part A checks do not separately
require an AMZN reaction. Long source files and inherited helper files do not
have automatic numerical penalties in the pasted rubric.
