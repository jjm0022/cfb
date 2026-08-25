# Task 1 report

## Implementation

- Added `max_submission_age` to `plan_snapshots`, defaulting to 15 minutes so existing NFL behavior remains unchanged.
- Added validation against the 15-minute safety lead and greedy kickoff-span batching.
- Added deterministic SHA-256 `snapshot_request_id` based on sport, request metadata, UTC timestamp, and sorted slate.
- Added CFB batching, compatibility, age-bound, validation, and ID tests.

## RED/GREEN evidence

- The specified pre-implementation expectation was that new tests fail because the argument and ID function were absent.
- Focused verification: `uv run pytest tests/test_snapshots.py -v` — 18 passed.
- Full verification: `uv run pytest` — 259 passed.

## Files

- `src/pickem/backtest/snapshots.py`
- `tests/test_snapshots.py`

## Commit

- `4f86bf4 feat: batch CFB archive snapshots by maximum age`

## Self-review and concerns

- `git diff --check` passed; implementation is limited to the requested planner and tests.
- No known concerns.

## Fix round 1

- Reformatted the added snapshot tests to comply with the 100-character Ruff line limit and sorted imports.
- `uv run ruff check src/pickem/backtest/snapshots.py tests/test_snapshots.py`: All checks passed.
- `uv run ruff format --check src/pickem/backtest/snapshots.py tests/test_snapshots.py`: 2 files already formatted.
- `uv run pytest tests/test_snapshots.py -v`: 18 passed in 0.08s.
