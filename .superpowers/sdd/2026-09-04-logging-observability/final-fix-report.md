# Final logging fix wave

Status: complete

## Root causes and fixes

- `run_context` re-raised the same exception without identifying its already-owned
  traceback record, so APScheduler intercepted and emitted it again. The context now
  marks that exception and the stdlib interceptor suppresses only a matching marked
  ERROR, leaving APScheduler's execution event and starting record intact.
- The two coinflip evaluation adapters had no run boundary or decision-log volume
  guard. Both now use their exact CLI entry name, database/sport/season facts, and
  context-local `suppress_decision_logging()`.
- `/status` caught failures inside `run_context`, causing a false `run_finished`.
  It now catches after the context re-raises, preserving one traceback-bearing
  `run_failed` and the existing unavailable response.
- Stdlib interception began its depth calculation inside the handler module. It now
  uses the standard frame walk and binds the original mixed-case `logger_name`.
- Archive ledger claims bypassed the Store audit. `commit_archive_request` now emits
  `rows_written` after COMMIT with rows 1/0 and request scope; rollback emits none.
- Rejected coinflip evaluation invocations validated before entering their run
  boundary. Both adapters now enter their exact context, including the requested
  database, sport, and season range, before validation; decision suppression remains
  limited to accepted evaluation work.
- `/status` included successful-response delivery in its computation exception
  handler. It now computes the embed inside `run_context`, handles only computation
  failures with the unchanged unavailable response, and performs the success send
  afterward so delivery exceptions propagate without retry or masking.

## Verification

- TDD mutation red: all four rejected-evaluation cases failed because the old
  boundary emitted no lifecycle; the delivery regression failed because the old
  `/status` handler attempted two responses.
- Exact final-fix regressions: `6 passed, 1 warning`.
- Complete affected modules: `72 passed, 201 warnings` in 124.57s.
- Full `uv run pytest` attempt reached the pre-existing
  `test_monitor_change_notification_uses_status_pick_format`, reported it `PASSED`,
  then hung while pytest-asyncio closed its event loop. The isolated test reproduced
  that post-pass teardown hang and was stopped by a 60-second guard (`exit 124`).
- Full remainder: `499 passed, 1 deselected, 405 warnings` in 143.15s.
- `uv run ruff check src tests`: clean.
- `git diff --check`: clean.

Residual risk: the unrelated Discord monitor-notification test can hang after its
assertions pass during pytest-asyncio event-loop teardown. The known third-party
deprecation warnings are unchanged.
