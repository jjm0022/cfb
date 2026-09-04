# Task 9 report: backtest boundaries and remaining CLI contexts

- Task: `logging-observability / Task 9` (iteration 1)
- Status: complete; implementation and tests are ready for parent integration.
- Outcome: backtest and calibration now emit low-volume started/finished
  records with input sports/seasons and current report totals. Their sweep
  bodies use the existing context-local decision-log suppression, so nested
  consensus, measurement, tiebreak, and edge records do not leak. The eight
  remaining CLI commands now bind exact `cli:<command>` entries and database
  facts; `poll-odds` and `report` remain single-wrapped.
- Contract changes: none to public signatures, return values, or behavior.
- RED evidence: the three new boundary/context tests failed before the
  implementation because no boundary/context records existed.
- GREEN evidence: focused owned modules passed `57 tests`; full suite passed
  `486 tests`.
- Verification: `UV_CACHE_DIR=/tmp/cfb-uv-cache uv run pytest` → `486 passed,
  355 warnings`; `UV_CACHE_DIR=/tmp/cfb-uv-cache uv run ruff check src tests` →
  `All checks passed!`; `git diff --check` passed.
- Residual risks: none identified in the owned scope; warnings are existing
  third-party deprecations/future warnings.
- Decision required: none.
- References: `src/pickem/backtest/runner.py`,
  `src/pickem/backtest/calibration.py`, `src/pickem/cli.py`,
  `tests/test_backtest_runner.py`, `tests/test_calibration.py`,
  `tests/test_cli.py`.

## Fix round 1

- Finding: the empty-result `calibrate` CLI path raised `typer.Exit(0)` inside
  `run_context`, which produced a false traceback-bearing `run_failed` ERROR
  and skipped `run_finished`.
- Fix: return normally after the existing empty-result output. Typer retains
  the successful exit code and the context now closes normally.
- RED/GREEN evidence: the sink-backed regression failed with `0` finished
  rows before the fix and passes after it, asserting exit 0, exactly one
  `run_finished`, and no ERROR rows.
- Verification: owned calibration/CLI tests and Ruff pass; no concurrent
  ContextVar or pre-existing teardown issue was changed.
- Fix commit: follow-up to `089d7fc` (Task 9 fix round 1).
