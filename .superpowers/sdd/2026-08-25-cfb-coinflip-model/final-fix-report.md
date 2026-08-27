# Final fix wave report — CFB COINFLIP Candidate 1

**Baseline:** `3091198350ceee2d60ad15303312a903eaa9b972`  
**Branch:** `feature/cfb-coinflip-model`  
**Date:** 2026-08-27

## Root-cause investigation

The final review identified two distinct exact-half semantics. The historical
Candidate 1 evaluator obtains class labels from sklearn at probability `0.5`,
which yields AWAY. This is intentional under ledger Ruling 5: historical
candidate accuracy must remain independent of the Elo comparator. The design's
Elo-at-half rule applies only to prospective, live artifact inference. The
frozen production JSONL has no exact-half probabilities, so this clarification
does not change the frozen result.

The actual acceptance-gate defect was self-certification. `CoinflipEvaluation`
gave `leakage_safe` and `deterministic` the default value `True`; direct calls
to `summarize_coinflip_predictions` therefore could claim both gate conditions
without proving fold boundaries or comparing another evaluation. The CLI also
ran the evaluator only once and inherited the default deterministic flag.

The remaining review notes were documentation/message accuracy defects:
submission requests are bounded-age kickoff batches, the empty archive message
was NFL-specific, and the handoff incorrectly said all work was on `master`.

## TDD evidence

The following focused RED command was run before production changes:

```text
uv run pytest tests/test_coinflip.py tests/test_cli.py tests/test_archive.py \
  -k 'outer_folds_never or summary_cannot or gate_rejects or rejects_a_run or empty' -v
```

It selected four tests. The existing fold test and explicit false-gate test
passed; the two new behavior tests failed exactly as expected:

```text
FAILED test_summary_cannot_self_certify_an_invalid_fold_boundary
  AssertionError: assert not True  # result.leakage_safe was True

FAILED test_evaluate_coinflip_rejects_a_run_before_determinism_is_compared
  assert 1 == 2  # CLI called evaluate_coinflip only once
```

After the minimal implementation, the same command passed:

```text
4 passed, 54 deselected
```

## Changes

- Made `CoinflipEvaluation` certification flags fail closed by default.
- Derived leakage safety from actual fold train/test season boundaries and
  prediction-to-test-fold membership. Invalid, duplicate, absent, future, or
  non-prior training boundaries cannot certify the gate.
- Added deterministic comparison of independently evaluated serialized
  metrics and JSONL predictions. The production CLI evaluates twice, fails
  loudly if the outputs differ, and marks its output deterministic only after
  that comparison.
- Added tests for unverified/default gate rejection, invalid leakage
  boundaries, and a CLI second-run mismatch.
- Documented the intentionally different historical evaluator and prospective
  live exact-half paths adjacent to the evaluator's class-label call.
- Corrected bounded-age snapshot wording, the sport-neutral archive error,
  and the feature-branch handoff wording.

## Verification

Focused affected modules, before final full checks:

```text
uv run pytest tests/test_coinflip.py tests/test_cli.py tests/test_archive.py -q
58 passed, 354 warnings
uv run ruff check src tests
All checks passed!
```

Final required checks:

```text
uv run pytest -q
318 passed, 354 warnings
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
54 files already formatted
```

The warnings are the pre-existing, expected sklearn `penalty='l2'`
deprecation warnings retained by the frozen estimator contract.

## Frozen-artifact comparison

The evaluator was run only against the operational database with explicit
temporary output paths and no network command:

```text
uv run pickem evaluate-coinflip --sport cfb --from 2021 --to 2025 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb \
  --predictions /private/tmp/cfb-coinflip-final.KaPaiC/predictions.jsonl \
  --report /private/tmp/cfb-coinflip-final.KaPaiC/report.md
```

It remained `Candidate 1: NULL — retain Elo`. SHA-256 and byte comparisons:

| Artifact | Frozen SHA-256 | Temporary SHA-256 | `cmp` |
| --- | --- | --- | --- |
| JSONL predictions | `5b2a3cff74d7009b3982a71ecbbe1bd49fbd4d3da5bf9b00f98a7bc7cf6b6d5c` | same | exit 0 |
| Markdown report | `bee47747383e312b03330007a496c2934b9f5d4d5cf65369d10521eae514d1e4` | same | exit 0 |

No frozen artifact was overwritten or retuned.

## Files changed

- `src/pickem/backtest/coinflip.py`
- `src/pickem/cli.py`
- `src/pickem/backtest/snapshots.py`
- `src/pickem/backtest/archive.py`
- `tests/test_coinflip.py`
- `tests/test_cli.py`
- `tests/test_archive.py`
- `docs/HANDOFF.md`
- this report

## Commit and concerns

Commit: `149fcbf61df7807399b66495a72d303d588f3fcc`
Concern: none. Candidate 1 remains the already-frozen NULL result; future
live-model work remains unauthorized.
