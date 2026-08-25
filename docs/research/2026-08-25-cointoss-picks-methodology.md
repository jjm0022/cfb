# Improving COINFLIP picks without building a forecasting lab

**Written:** 2026-08-25  
**Scope:** maximize correct ATS picks on a forced-pick sheet; no opponent-pick,
contrarian, confidence-pool, or bankroll strategy.  
**Repository baseline:** NFL 2020–2025, 1,663 graded games: 53.2% overall,
63.7% STRONG, 54.2% LEAN, and 49.5% COINFLIP. The threshold sweep already
found no out-of-sample benefit from changing `strong` or `lean`.

## Recommendation

**Do not replace the line-divergence strategy. Replace only the COINFLIP
tiebreak, and begin with one small market-only model.**

The first candidate should be an L2-regularized logistic regression that
predicts whether the home team covers the frozen CBS line from three values
already present in each submission-time multi-book snapshot:

1. median line movement: `frozen_spread - median(book_spread)`;
2. mean line movement: `frozen_spread - mean(book_spread)`; and
3. book balance: share of books moved toward home minus share moved toward away.

Use it only when the current strategy says COINFLIP. Keep the existing
divergence side for STRONG and LEAN, and keep Elo only as the fallback when no
market exists or the model returns exactly 50%. Fit NFL and CFB separately,
through the same code path. Retrain in chronological order and pick home when
the out-of-sample probability is above 0.50, away when below it.

This is deliberately modest. The current code reduces roughly ten bookmaker
lines to one median, then asks Elo to decide the cases where that median barely
moved. Mean movement and book balance preserve information that is already in
the database and can break many median ties without adding injuries, weather,
player models, or a new paid feed. Regularization limits the damage if those
extra market summaries contain little signal.

If this model does not beat Elo out of sample, stop. The next candidate would be
a market-anchored ridge score model, but it should be a separate experiment,
not additional features silently added until the backtest improves.

## Why the market remains the baseline

The research supports treating the market spread as the forecast to improve
upon, not as one ordinary feature among many:

- Stern found that NFL margin over the point spread was not significantly
  different from a zero-mean normal distribution, with a standard deviation
  just under 14 points. In other words, the spread was an empirically justified
  center for score margin, while individual game error remained large
  ([Stern, 1991](https://doi.org/10.1080/00031305.1991.10475798)).
- Statistical tests in a large early NFL study could not reject point-spread
  market rationality, although economic tests found some departures
  ([Gandar et al., 1988](https://doi.org/10.1111/j.1540-6261.1988.tb02617.x)).
- Levitt's bettor-level NFL data found bookmakers more skilled than bettors and
  found little evidence of bettors who systematically beat the bookmaker. The
  paper also shows why a line need not be a literal 50/50 market-clearing price:
  bookmakers can exploit predictable bettor preferences
  ([Levitt, 2004; NBER working-paper version](https://www.nber.org/papers/w9422)).
- Glickman and Stern modeled time-varying NFL team strength, home advantage,
  and score margin and reported performance at least as good as the Las Vegas
  line on a small 110-game test set. The useful lesson is dynamic shrinkage;
  the small test is not evidence that a home model will reliably beat the line
  ([Glickman and Stern, 1998](https://glicko.net/research/nfl.pdf)).

The repository's own result agrees with that literature. Meaningful line
movement works; where the market has not moved, the homegrown Elo tiebreak is
448-457-21 (49.5%). The prior threshold study also found that lowering the lean
threshold gained nine picks in-sample but only one in walk-forward evaluation.
That is evidence to preserve the market signal and reject more threshold
tuning, not to discard the whole approach.

## Why this model, and what not to build yet

### Candidate 1: full-market logistic tiebreak

For a game with frozen home spread `L` and submission-time book spreads
`M_1 ... M_k`, define:

```text
median_delta = L - median(M)
mean_delta   = L - mean(M)
book_balance = (# books with L-M_i > 0 - # books with L-M_i < 0) / k
target       = 1 if home covers L, else 0
```

Exclude pushes from model fitting, matching the current backtest denominator.
Standardize the three inputs using training-fold statistics only. Fit an
L2-regularized logistic regression and select the penalty from a very small,
predeclared grid such as `{0.1, 1, 10}` inside each training window. There are
no interactions and no sport statistics in version 1.

The model's scope is COINFLIP only. That protects the proven divergence tiers
from a weak learner and makes the experiment answer one clean question:
**does the cross-book distribution beat Elo when median movement is small?**

The Odds API exposes spreads from multiple bookmakers and historical snapshots
at a requested timestamp; historical featured-market data begins in June 2020
([official v4 documentation](https://the-odds-api.com/liveapi/guides/v4/)). The
repo already stores the individual book spreads needed for these three
features, so no historical repurchase or schema change is required.

### Candidate 2: market-anchored ridge residual model, only if needed

If Candidate 1 is a null result, the next simple experiment is a dynamic score
model whose target is the market's error, not raw game margin:

```text
market_error = actual_home_margin + submission_market_spread
home_ATS_score = frozen_spread - submission_market_spread
                 + predicted_market_error
```

Predict `market_error` from ridge-shrunk home and away team effects, with at
most rest differential and a recency weight. The line remains fixed as the
baseline with coefficient 1; the model is allowed to learn only a correction.
This is a simpler descendant of established football rating work: Harville used
a linear model with team and home-field effects for college-football score
margins ([Harville, 1977](https://doi.org/10.1080/01621459.1977.10480991)), and
Glickman and Stern added time variation and shrinkage for NFL team strengths.
Regularized, recency-weighted least squares has also been applied to sports
prediction using only teams, dates, and scores
([Dubbs, 2018](https://doi.org/10.3233/MAS-180428)).

Do not run both candidates and then report only the winner. Candidate 2 begins
only after Candidate 1 has a frozen result, and both remain in the experiment
ledger.

### Defer EPA, injuries, weather, recruiting, and neural/boosted models

Those features may be valuable, but they multiply the timestamp and missingness
problems before the three-feature market model has established that the
COINFLIP pool contains learnable signal. In particular, any season rating that
was calculated after the game is unusable even if its row is labelled with the
correct season.

If a later round is justified, add only one feature family at a time:

- **NFL:** rolling score margin, rest differential, then rolling offensive and
  defensive EPA/play. nflverse's schedule dictionary provides game result,
  spread, total, and home/away rest
  ([official schedule dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)).
- **CFB:** neutral-site indicator and a rating explicitly stamped through the
  prior week. CFBD games expose neutral site and pregame Elo, while its CORE
  rating is explicitly keyed by `throughWeek` and includes overall, offense,
  and defense values
  ([official games API](https://api.collegefootballdata.com/api/games),
  [official ratings API](https://api.collegefootballdata.com/api/ratings)).
  CFBD also exposes provider lines, open spreads, totals, and moneylines
  ([official betting API](https://api.collegefootballdata.com/api/betting)).
- **Market enhancement:** The Odds API returns outcome prices as well as spread
  points. Retaining the home/away price at an unchanged spread could reveal
  pressure before a half-point move, but it requires a storage migration and
  historical data that the current parser discarded. Treat it as a later data
  project, not part of Candidate 1.

## Probability and calibration

The league ultimately wants a side, so the decision rule is intentionally
simple: choose the side whose estimated cover probability exceeds 50%.
Probability output is still useful because it makes degradation visible and
allows the same model to be assessed with more information than win/loss alone.

Report three metrics on untouched predictions:

1. **primary:** total correct picks and hit rate, plus the paired difference in
   correct picks versus the incumbent Elo tiebreak;
2. **secondary:** Brier score and log loss; and
3. **diagnostic:** calibration table/plot in coarse bins, with sample counts.

The Brier score was introduced specifically to verify probability forecasts
([Brier, 1950](https://doi.org/10.1175/1520-0493%281950%29078%3C0001%3AVOFEIT%3E2.0.CO%3B2)),
and a strictly proper scoring rule rewards reporting the forecaster's actual
probability rather than a strategically distorted one
([Gneiting and Raftery, 2007](https://doi.org/10.1198/016214506000001437)).
Calibration is a diagnostic, not permission to tune a different threshold for
every probability bin. For this forced-pick objective, a well-calibrated 51%
forecast is still a pick.

## Validation protocol

Random train/test splits are invalid here. They let later games teach ratings,
standardization, and hyperparameters used on earlier games. Time-series
cross-validation exists specifically because ordinary folds can train on the
future and evaluate on the past
([scikit-learn `TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)).

Use this protocol:

1. **Build one row per game from information captured before kickoff.** Freeze
   the exact submission snapshot rule. Never use a closing line recorded after
   the CBS deadline, a full-season rating, or a feature recomputed with later
   games.
2. **Outer walk-forward:** train through season `s-1`, predict season `s`, then
   advance. Keep history continuous, but compute every rolling feature as of
   that game. Pool only the outer-fold predictions for the headline result.
3. **Inner walk-forward:** within each outer training span, select only the L2
   penalty from the predeclared three-value grid. Scaling is also fit inside
   this training span.
4. **Compare paired picks:** report candidate minus incumbent correct picks on
   the same games and a confidence interval clustered or bootstrapped by week.
   A Wilson interval for each model is useful but does not answer whether two
   models making many identical picks differ.
5. **Lock a prospective test:** 2020–2025 has already been inspected repeatedly,
   so it is development data, not a pristine holdout. Freeze the winning rule
   before the 2026 season and report 2026 separately without retuning.
6. **Log every attempted specification, including nulls.** Reusing one history
   to search many rules makes an apparently best result likely to be chance;
   White formalizes this data-snooping problem and tests predictive superiority
   over a benchmark
   ([White, 2000](https://doi.org/10.1111/1468-0262.00152)). Here the simpler
   defense is a tiny model list, an experiment ledger, and an untouched
   prospective season.

### Acceptance gate

Adopt Candidate 1 only if all of the following hold on pooled outer-fold
COINFLIP predictions:

- more correct picks than incumbent Elo;
- positive paired improvement in at least four of the five 2021–2025 NFL test
  seasons (not one anomalous year carrying the result);
- no material worsening of Brier score or obvious calibration failure; and
- the improvement remains when the L2 penalty is chosen strictly inside each
  training fold.

Do not require an arbitrary betting-profit threshold: there is no vig in this
league and the user must pick every game. Also do not ship a one- or two-pick
historical gain as a discovery. The repository's own threshold study showed
that scale of gain is comfortably inside the paired noise floor.

## NFL and CFB should share code, not coefficients

Fit the sports separately. This is an engineering and statistical inference
from the available data, not a claim that a published paper found a universal
NFL/CFB split:

- NFL has 32 stable teams and dense nflverse play-by-play/schedule features;
- CFB has far more teams, more uneven schedules, explicit neutral sites, and a
  different ratings/data source;
- market coverage and book dispersion differ; and
- the repository currently has a completed NFL odds backtest but no equivalent
  historical CFB frozen/submission market backfill.

A pooled coefficient would assume the same relationship between a half-point
move, book agreement, and cover probability in both markets before that has
been demonstrated. Use one feature-builder and evaluator, but separate fitted
models, calibration tables, and acceptance decisions. Until historical CFB
snapshots are available with honest timestamps, run the existing CFB rule and
accumulate prospective data rather than borrowing NFL coefficients.

## Implementation plan

1. **Freeze the experiment contract.** Record feature formulas, COINFLIP scope,
   penalty grid, folds, push handling, and acceptance gate in a short spec.
2. **Create a read-only feature table.** From stored league lines, individual
   submission book lines, and final scores, emit the three features and target;
   add assertions that every source timestamp precedes kickoff.
3. **Add the walk-forward evaluator first.** It must produce incumbent and
   candidate decisions on identical game IDs, per-season paired deltas, Brier
   score/log loss, and a coarse calibration table.
4. **Run Candidate 1 once.** Save all fold predictions and the result. If it
   fails the gate, keep Elo and close the experiment as a useful null.
5. **Only then consider Candidate 2.** Give it its own frozen spec and result;
   do not turn Candidate 1 into an open-ended feature search.
6. **Prospective 2026 audit.** Lock the selected rule before using any 2026
   outcomes, monitor NFL and CFB separately, and make no midseason coefficient
   changes.

## Expected outcome

The most likely result is a small gain or another honest null: an efficient
market leaves little predictable ATS error. That is still valuable. This plan
tests the information the current median throws away, protects the part of the
strategy already working, and sets a hard stopping rule before complexity can
manufacture a backtest win.
