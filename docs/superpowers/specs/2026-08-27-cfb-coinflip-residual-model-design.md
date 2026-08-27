# CFB COINFLIP Candidate 2 — Market-Residual Ridge Design

**Date:** 2026-08-27
**Status:** Approved in conversation; pending written-spec review
**Scope:** One frozen CFB experiment and conditional immediate deployment for
`COINFLIP` picks

## 1. Decision and goal

Candidate 1 remains a null result and Elo remains the production CFB
`COINFLIP` decider. Candidate 2 tests a materially different hypothesis:
recent team-specific errors in the late market may persist enough to improve a
forced pick against the earlier frozen line.

The model treats the late market spread as the baseline forecast and learns
only a ridge-shrunk correction. It does not extend Candidate 1's cross-book
microstructure features.

The project continues to optimize raw ATS pick accuracy. It does not optimize
vig, ROI, bankroll, opponent picks, contrarian value, or confidence points.
`STRONG` and `LEAN` remain controlled exclusively by line divergence.

If Candidate 2 clears every predeclared historical gate, it is eligible to
replace Elo immediately for remaining 2026 CFB `COINFLIP` picks. The deployed
artifact must be trained entirely on 2021–2025 data and frozen before any 2026
result is read. It cannot refit or tune during the 2026 season.

## 2. Why Candidate 1 is not extended

Candidate 1 improved on Elo by 27 correct picks, but it did not establish
incremental market-microstructure signal:

- Candidate 1 was 785-764 (50.68%).
- Elo was 758-791 (48.93%).
- Always home was 789-760 (50.94%).
- The frozen-line favorite was 790-759 (51.00%).
- Candidate 1's three features have pairwise correlations from 0.94 to 0.97.
- Its 296 away selections lost four net picks relative to always home.
- Its Brier score was 0.2502 and its paired bootstrap interval included zero.

The next experiment therefore changes both the information family and the
target. No additional book-dispersion, movement, price, or interaction feature
is permitted in Candidate 2.

## 3. Binding exclusions

Candidate 2 contains only team identity, game results, and the stored frozen
and submission spread proxies. It excludes:

- Candidate 1's median movement, mean movement, and book-balance features;
- Elo or another pregame rating as a feature;
- rest, neutral site, travel, weather, injuries, recruiting, availability,
  efficiency, EPA, totals, and moneylines;
- team-by-season interactions, conference effects, home-field parameters, and
  nonlinear interactions;
- 2026 outcomes in any fit, selection, calibration, or acceptance decision;
  and
- a second window, half-life, penalty grid, probability mapping, decision
  threshold, or post-result feature specification.

External prior-week ratings are a possible separately approved Candidate 3,
not a fallback inside this experiment.

## 4. Population and stored inputs

No network call, paid archive request, or database mutation is part of this
experiment. The operational database already contains the required 2021–2025
CFB games, final scores, frozen proxy lines, and submission proxy lines.

### 4.1 Training population

Training uses every completed CFB game from the permitted prior seasons that
has at least one finite submission-proxy spread captured before kickoff.
Training is not restricted to `COINFLIP` games. ATS pushes remain valid
continuous training outcomes.

For each game, select the deterministic latest pre-kickoff quote from every
book and take their consensus median. A missing or nonfinite submission
consensus excludes the training row and is reported.

### 4.2 Evaluation population

Evaluation reuses Candidate 1's population contract exactly. A game is
eligible when it is completed, has valid pre-kickoff frozen and submission
proxies, and satisfies:

```text
abs(frozen_spread - submission_consensus) < 1.0
```

The evaluator must predict all 1,577 frozen Candidate 1 outer-fold game IDs,
including 28 pushes. The 1,549 decided games form the accuracy and probability
denominators. Candidate 2 may not improve its result by dropping an unseen
team or another difficult row.

### 4.3 Proxy limitation

Historical CBS lines do not exist. `frozen_spread` remains the early-week Odds
API proxy and `submission_market_spread` remains the pre-kickoff Odds API
proxy. Candidate 2 measures performance against that operational proxy, not a
claim that the archived early-week median exactly equals the historical CBS
number.

## 5. Statistical model

For submission market spread `S`, frozen spread `L`, and actual home margin
`M`:

```text
market_error        = M + S
predicted_ATS_margin = L - S + predicted_market_error
```

A positive spread means points are added to the home team, matching the
repository's canonical sign convention.

### 5.1 Team-effect design

Fit ridge regression to `market_error`. Each row contains:

- `+1` in the home team's column;
- `-1` in the away team's column; and
- an unpenalized intercept.

All team coefficients are penalized equally. An absent team has effect zero at
inference. The model contains no separately learned home-field effect because
the submission market spread already contains the market's home-field
estimate.

### 5.2 Recency weighting

For a fit made at cutoff `T`, a training game completed `age_days` before `T`
receives:

```text
weight = 2 ** (-age_days / 365)
```

The 365-day half-life is fixed and is not selected. The outer-season cutoff is
the earliest stored kickoff in that test season. The final 2026 cutoff is the
earliest stored 2026 CFB kickoff; schedule metadata is permitted, but no 2026
score or graded pick is.

### 5.3 Ridge selection

The only selected hyperparameter is ridge `alpha`, from the frozen grid:

```text
{10, 30, 100}
```

Selection uses chronological inner validation and raw `COINFLIP` accuracy:

- For the 2022 outer fold, fit on 2021 weeks 1–8 and validate on later eligible
  2021 weeks.
- For later outer folds, use expanding prior-season validation folds wholly
  inside the outer training span.
- Every inner fit recomputes recency weights relative to its own validation
  cutoff.
- Pool inner validation decisions across folds and choose the alpha with the
  most correct picks.
- Ties select the largest alpha, favoring the most shrinkage.

No test-season outcome participates in alpha selection.

### 5.4 Season-locked outer replay

Generate the same four outer test seasons as Candidate 1:

| Training seasons | Test season |
|---|---:|
| 2021 | 2022 |
| 2021–2022 | 2023 |
| 2021–2023 | 2024 |
| 2021–2024 | 2025 |

For each fold, select alpha inside the training span, fit once at the test
season's opening cutoff, and hold every coefficient fixed for the entire test
season. Earlier results from the test season cannot update later test weeks.
This mirrors the approved 2026 operating rule.

### 5.5 Decision and probability

- Positive `predicted_ATS_margin` picks home.
- Negative `predicted_ATS_margin` picks away.
- Exact zero delegates to Elo.

Probability comes from chronological out-of-fold errors generated wholly
inside the current outer training span. For each inner validation prediction:

```text
residual_error = observed_market_error - predicted_market_error
```

Reweight these errors to the outer cutoff with the same 365-day half-life. For
predicted ATS margin `A`, estimate the probability that the home team covers
with the weighted empirical survival distribution at `-A`. Threshold ties
receive half weight. Add one neutral pseudo-observation so probabilities do
not reach zero or one:

```text
p_home = (0.5 + sum(weight * [error > -A])
               + 0.5 * sum(weight * [error == -A]))
         / (1.0 + sum(weight))
```

At least 100 chronological out-of-fold residual errors must be available for
an outer fold or the experiment fails closed. The empirical distribution is
not refit on outer-test outcomes.

## 6. Locked comparators

The primary comparator is the frozen-line favorite on the identical decided
games:

- negative frozen home spread: pick home;
- positive frozen home spread: pick away; and
- exact zero: pick home.

The current frozen population contains no zero-spread decided rows, but the
rule is specified for completeness. This comparator was 790-759 (51.00%) and
was the strongest locked simple rule in the Candidate 1 diagnosis.

The report also includes the production Elo replay and always-home rule as
diagnostics. Neither can substitute for the primary comparator or weaken the
acceptance gate.

## 7. Metrics and acceptance gate

Report, on identical paired rows:

- candidate and comparator wins, losses, pushes, and hit rates;
- candidate minus primary-comparator correct picks and percentage points;
- the paired difference for each outer season;
- a deterministic 95% interval from a season/week-clustered bootstrap;
- Brier score and log loss;
- the existing five probability calibration bins with counts;
- Elo and always-home diagnostic results;
- selected alpha, intercept, team effects, training cutoff, training count,
  effective training weight, and residual-distribution count for every fold;
  and
- every training and evaluation exclusion.

Candidate 2 passes only if all conditions hold:

1. accuracy exceeds the frozen-line favorite by at least 1.0 percentage point
   on identical decided games;
2. the candidate has more correct picks than the frozen-line favorite in at
   least three of four outer test seasons;
3. the paired week-clustered bootstrap 95% lower bound is strictly above zero;
4. Brier score is strictly below `0.25`;
5. no calibration bin with at least 100 observations differs between mean
   probability and observed rate by more than 5.0 percentage points;
6. every prediction is certified as season-locked and trained strictly before
   its outer test season;
7. independent reruns produce byte-identical predictions, metrics, fold state,
   and exclusions; and
8. the experiment reads no 2026 outcome.

At the current 1,549-game denominator, the first condition requires at least
16 more correct picks than the primary comparator. The exact count is derived
from the paired decided denominator rather than hard-coded.

If any condition fails, Candidate 2 is a null. Elo remains production, no
model artifact is created, and the team-residual family stops. The result may
not authorize another half-life, grid, calibration rule, threshold, or added
feature.

## 8. Deep experiment module

Create `src/pickem/backtest/coinflip_residual.py` as a pure, deep module. Its
main interface is:

```python
evaluate_residual_candidate(
    games: Sequence[Game],
    frozen_lines: Sequence[MarketLine],
    submission_lines: Sequence[MarketLine],
) -> ResidualEvaluation
```

Callers supply timestamped domain records. Behind this interface the module
owns row construction, season cutoffs, weighting, team encoding, inner
selection, outer fitting, probability conversion, comparators, metrics,
bootstrap, certification, and exclusions.

The implementation may reuse stable Candidate 1 population and Elo-replay
functions, but it must not change Candidate 1's frozen estimator, gate, report,
or artifact. Internal fitting helpers are not a public seam and tests should
exercise behavior through the main interface.

The module also renders deterministic prediction JSONL and a Markdown report.
It performs no database, filesystem, environment, clock, or network I/O.

## 9. CLI adapter and experiment outputs

Add a CFB-only command with an explicit Candidate 2 name. It accepts only the
approved 2021–2025 range and explicit output paths. The CLI:

1. reads the operational database without mutating it;
2. splits stored frozen and submission proxies by source;
3. calls the pure evaluator twice independently;
4. refuses certification unless serialized results are byte-identical;
5. writes deterministic prediction JSONL and Markdown results; and
6. reports `PASS` or `NULL — retain Elo`.

The command constructs no API client and makes no network call. It refuses a
different sport or season range. A null run writes the audit artifacts but no
live model artifact.

The Candidate 2 result is frozen in a new research document and experiment
ledger entry whether it passes or fails. Candidate 1's frozen JSONL and report
remain untouched.

## 10. Conditional 2026 artifact

Artifact generation and production integration occur only after a passing
historical evaluation. For a passing result:

1. select alpha using chronological inner folds confined to 2021–2025;
2. fit team effects once on all valid 2021–2025 training rows, weighted to the
   2026 opening cutoff;
3. create the probability distribution from chronological out-of-fold
   2021–2025 residual errors;
4. serialize a deterministic versioned JSON artifact; and
5. verify the artifact reproduces the final in-memory fit exactly.

The artifact contains:

- format version and sport;
- training range and 2026 cutoff;
- fixed half-life, alpha grid, and selected alpha;
- intercept and sorted team effects;
- weighted empirical residual errors and weights;
- feature and sign-convention identifiers;
- training-row, residual-row, and exclusion counts;
- hashes of the canonical training inputs, frozen evaluation predictions, and
  design contract; and
- exact-zero decision rule.

The artifact contains no 2026 result. Its schema is strict: unknown fields,
missing fields, duplicate teams, wrong sport or version, a mismatched contract
identifier, nonfinite numbers, unsorted residual state, or invalid hashes fail
loudly. A present but invalid artifact never silently falls back to Elo.

## 11. Conditional live inference

Production inference is implemented only after the frozen result passes. A
pure inference module validates the artifact and accepts:

- home and away team IDs;
- frozen home spread; and
- submission consensus home spread.

It returns predicted ATS margin, home-cover probability, and side. It performs
no fitting, database access, filesystem access, clock access, or network I/O.
An unseen 2026 team validly receives a zero team effect.

The decision pipeline remains:

```text
STRONG / LEAN -> existing divergence side
CFB COINFLIP + valid accepted Candidate 2 artifact -> residual-model side
CFB COINFLIP + exact zero residual score -> Elo side
NO_MARKET -> Elo side
NFL -> existing behavior
```

The rendered rationale identifies the residual model, predicted ATS margin,
and home-cover probability. The artifact remains unchanged for all of 2026.
No command in the weekly operating path may refit it.

## 12. Failure handling

The experiment and conditional live path fail closed:

- Lines captured at or after kickoff are excluded before book selection.
- Missing or nonfinite training consensus is reported, never imputed.
- Evaluation population mismatches name missing and unexpected game IDs.
- Test-season games in an outer fit, validation rows in their own fit, fewer
  than 100 calibration residuals, invalid weights, or nonfinite fitted state
  prevent certification.
- A nondeterministic rerun prevents certification and artifact creation.
- A null result cannot create or load a Candidate 2 artifact.
- An invalid present artifact raises rather than silently delegating to Elo.
- An absent artifact retains the existing Elo path.
- No 2026 result-loading or synchronization command is part of evaluation,
  final fitting, or deployment.

## 13. Test strategy

Tests use the deep experiment interface as their primary surface. Required
coverage includes:

- hand-calculated market-error targets and ATS scores;
- canonical spread signs and latest-pre-kickoff book selection;
- use of all valid prior training games but only `COINFLIP` evaluation games;
- exact reuse of Candidate 1's 1,577-game evaluation population;
- 365-day weights at zero, one half-life, and multiple half-lives;
- home `+1`, away `-1`, unpenalized intercept, and unseen-team zero effect;
- inner-fold isolation, outer-season isolation, and season-locked predictions;
- alpha selection and largest-alpha tie behavior;
- chronological out-of-fold residual probabilities and endpoint smoothing;
- candidate, favorite, Elo, and always-home paired scoring;
- pushes in reports but outside accuracy and Brier denominators;
- week-clustered deterministic bootstrap;
- every acceptance condition failing closed independently;
- byte-identical evaluation, JSONL, Markdown, and exclusion order;
- CLI refusal of other sports/ranges and proof that it creates no client or
  database mutation; and
- no artifact on a null result.

Only if the frozen historical result passes, add tests for strict artifact
validation, exact fit/artifact parity, conditional pipeline routing,
unseen-team inference, exact-zero Elo fallback, rationale rendering, and proof
that the weekly path cannot refit.

## 14. Delivery sequence and authorization gates

1. Implement and verify the pure Candidate 2 evaluator and CLI adapter.
2. Run the frozen 2021–2025 evaluation once and save both pass and null
   evidence.
3. Record the result without changing production behavior.
4. If null, stop.
5. If passing, create and validate the 2026 artifact.
6. Only then implement the narrow live inference and pipeline integration.
7. Re-run the full suite and render a stored 2026 report without syncing or
   grading outcomes.

This approved design authorizes neither implementation nor evaluation by
itself. Both begin only after the written spec is reviewed and an
implementation plan is approved.
