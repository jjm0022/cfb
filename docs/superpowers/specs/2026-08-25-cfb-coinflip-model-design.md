# CFB COINFLIP Model — Design

**Date:** 2026-08-25
**Status:** Approved in conversation; pending written-spec review
**Scope:** CFB-first historical acquisition, model evaluation, and conditional
deployment for forced ATS picks

## 1. Goal

Improve the number of correct CFB picks without replacing the line-divergence
signal that already works for NFL. The first experiment replaces only the Elo
tiebreak on `COINFLIP` games with a small model built from the full distribution
of bookmaker spreads.

The project optimizes raw pick accuracy. It does not model opponents' picks,
contrarian value, confidence pools, betting profit, or bankroll.

The implementation is successful if it does all of the following:

1. acquires an honest 2021–2025 CFB archive dataset within the remaining Odds
   API quota;
2. evaluates the candidate with strictly time-ordered predictions for
   2022–2025;
3. either deploys a model that clears the predeclared acceptance gate or leaves
   Elo in place without changing live picks; and
4. locks the decision before any 2026 result is used for training or selection.

## 2. Binding decisions

- CFB is delivered before NFL because its 2026 season starts first.
- NFL and CFB share interfaces and evaluation code but never fitted
  coefficients.
- `STRONG` and `LEAN` picks remain controlled by line divergence.
- The model may decide only `COINFLIP` games.
- `NO_MARKET` and an exact model probability of `0.5` fall back to Elo.
- Candidate 1 uses only three market features. EPA, injuries, weather, player
  availability, recruiting, and team ratings are excluded.
- The existing NFL archive planner retains byte-for-byte-equivalent timing
  behavior by default.
- No 2026 result may enter training, hyperparameter selection, scaling, or the
  initial ship/no-ship decision.

## 3. Why this is a targeted replacement

The existing NFL backtest is 866-762-35 overall (53.2%). Its `STRONG` tier is
174-99-2 (63.7%) and its `LEAN` tier is 244-206-12 (54.2%). The 448-457-21
`COINFLIP` result (49.5%) is the unresolved part of the sheet. Simple historical
rules—always home, always away, favorite, underdog, and Elo—range only from
49.5% to 50.5% on those same NFL games.

The design therefore preserves median line divergence and asks one narrow
question: when median movement is less than one point, does the distribution
across books contain useful direction that the median discarded?

Primary-source support and the broader methodology are recorded in
`docs/research/2026-08-25-cointoss-picks-methodology.md`.

## 4. Historical CFB acquisition

### 4.1 Range and expected cost

Acquire CFB seasons 2021–2025. The database already contains 3,943 CFB games in
that range, including 3,942 completed games. Historical odds rows do not yet
exist for them.

An exact dry run over the stored schedule produced:

- 75 weekly frozen snapshots;
- 943 batched submission snapshots;
- 1,018 total historical requests; and
- 10,180 expected credits at 10 credits per request.

The recorded balance before implementation is 10,610 credits, leaving an
expected 430-credit margin. The command must query the current balance before
execution and refuse to begin unless the balance is at least the dry-run cost
plus 100 credits. It must also refuse if the new dry-run total differs from
10,180 until the difference is reviewed.

The first live historical request is one request from the approved plan, not an
extra probe. It is committed through the normal archive ledger, inspected, and
then the same run resumes from the next incomplete request.

### 4.2 Frozen snapshot

Each season/week receives one early-week snapshot at the existing Tuesday
14:00 UTC anchor. It represents the unavailable historical CBS number. This is
a proxy, not a claim that CBS used that exact market value.

### 4.3 Batched submission snapshots

The NFL planner requests a snapshot 15 minutes before every distinct kickoff
slot. Applied unchanged to CFB, that would cost 19,320 credits.

For CFB, sort a week's distinct kickoff instants. Starting with the earliest
unassigned kickoff, request a snapshot 15 minutes before it and assign every
subsequent kickoff no more than 75 minutes later to that request. Repeat with
the next unassigned kickoff. Therefore every assigned snapshot is:

- strictly before its game's stored kickoff;
- at least 15 minutes before the earliest kickoff in its batch; and
- no more than 90 minutes before any kickoff in its batch.

Each game belongs to exactly one submission request. The request's `slate`
contains only the games assigned to that batch. The existing weekly time window
remains the independent guard against stamping an event with the wrong week.

### 4.4 Kickoff verification

CFBD supplies an ISO-8601 `startDate`, and the current adapter preserves its
offset as an aware instant. Before paid acquisition:

1. unit tests cover UTC, positive/negative offsets, and dates crossing UTC
   midnight;
2. every stored 2021–2025 kickoff is timezone-aware;
3. a dry-run assertion proves all submission requests precede every game in
   their slate by 15–90 minutes; and
4. the first returned Odds API payload is checked so its event
   `commence_time` agrees with the stored CFBD kickoff closely enough to pass
   the existing guarded window and canonical game-id join.

Any failed check stops acquisition before the next paid request.

### 4.5 Resumability and idempotence

Add an `archive_requests` table keyed by a deterministic request fingerprint
over sport, season, week, kind, requested timestamp, and sorted slate IDs. It
records the requested time, returned snapshot time, line count, and completion
time.

Before an archive call, the runner skips fingerprints already marked complete.
After a successful response, inserting its append-only line rows and marking
the request complete occur in one DuckDB transaction. A rerun plans the same
1,018 requests, reports complete versus pending counts, and charges only for
pending work.

A process failure after the remote service charges for a response but before
the local transaction commits is the one unavoidable duplicate-charge window.
No local design can prove what an interrupted remote request returned. All
other ordinary interruption and restart paths avoid repurchasing completed
requests.

Execution runs one season at a time in chronological order. After each season,
the operator verifies completed requests, stored games, orphan counts, and that
every returned submission timestamp precedes every matched kickoff.

## 5. Model experiment

### 5.1 Population and target

Build one row for each completed CFB game that has both frozen and submission
snapshots and whose median absolute divergence is less than `1.0` point—the
same `COINFLIP` definition used by the live pipeline.

For frozen home spread `L` and the latest submission spread from each available
book `M_i`:

```text
median_delta = L - median(M_i)
mean_delta   = L - mean(M_i)
book_balance = (count(L - M_i > 0) - count(L - M_i < 0)) / count(M_i)
target       = 1 when home_score - away_score + L > 0, else 0
```

Pushes, where `home_score - away_score + L == 0`, are excluded from fitting and
accuracy denominators but remain counted in experiment reports.

A row is invalid and reported rather than modeled if it has no books, a
snapshot at or after kickoff, a non-finite feature, or inconsistent game and
line identity.

### 5.2 Candidate

Use scikit-learn L2-regularized logistic regression with an intercept, no class
weighting, and no interactions. Standardize all three features using means and
scales learned only from the current training fold. The only hyperparameter is
inverse regularization strength `C`, selected from the fixed grid
`{0.1, 1.0, 10.0}`.

At inference, probability greater than `0.5` picks home and probability less
than `0.5` picks away. Exactly `0.5` delegates to Elo.

### 5.3 Walk-forward protocol

Generate four untouched outer folds:

| Training seasons | Test season |
|---|---|
| 2021 | 2022 |
| 2021–2022 | 2023 |
| 2021–2023 | 2024 |
| 2021–2024 | 2025 |

For the first outer fold, choose `C` with a chronological split inside 2021:
train on weeks 1–8 and validate on weeks 9–15. For later folds, choose `C` by
inner expanding-season validation over the available training seasons. Feature
scaling is refit inside every inner and outer training fold.

The evaluator saves one record per outer-fold prediction: game ID, season,
week, frozen line, three raw features, model probability, candidate side, Elo
side, actual result, and whether the two methods agree. It also records the
chosen `C` and training range for each fold.

### 5.4 Metrics

The primary comparison is paired accuracy on identical `COINFLIP` games:

- candidate and Elo wins, losses, and pushes;
- candidate minus Elo correct picks and percentage points overall;
- the paired difference for each test season; and
- a 95% confidence interval from a deterministic week-clustered bootstrap.

Secondary diagnostics are Brier score, log loss, and a five-bin calibration
table with sample counts. These do not replace pick accuracy as the objective.

### 5.5 Acceptance gate

Candidate 1 ships only if all conditions hold on pooled 2022–2025 outer-fold
predictions:

1. candidate accuracy exceeds Elo accuracy by at least 1.0 percentage point on
   identical decided games;
2. candidate has more correct picks than Elo in at least three of the four test
   seasons;
3. candidate Brier score is below `0.25`, the constant-50% forecast baseline;
4. no fold contains future-trained scaling, coefficients, or hyperparameter
   decisions; and
5. rerunning the evaluator produces byte-identical predictions and metrics.

The confidence interval is reported but is not a separate pass/fail condition;
the prospective 2026 season is the untouched confirmation. A gain smaller than
one percentage point is treated as insufficient to justify changing the live
method even if it amounts to one or two more historical wins.

If the candidate fails any condition, Elo remains the CFB `COINFLIP` method and
Candidate 1 is recorded as a null result. Candidate 2 is a separate future
design decision, not an automatic feature-search continuation.

## 6. Live integration

The experiment runs before the live pipeline changes. If Candidate 1 passes,
commit a small versioned JSON artifact containing:

- sport (`cfb`);
- training range (`2021–2025` for the final prospective fit);
- feature names and order;
- training means and scales;
- intercept and coefficients;
- selected `C`;
- decision threshold (`0.5`); and
- artifact format version.

After the walk-forward decision, fit the prospective artifact once on all
eligible 2021–2025 CFB rows using the same inner selection rule. Validate the
artifact before use; missing keys, wrong sport, unexpected feature order,
non-finite values, or an unknown version fail loudly.

`decide_edges` continues to measure and finalize every pick. Its behavior is:

```text
STRONG / LEAN -> existing divergence side
COINFLIP + valid accepted CFB artifact -> market-model side
COINFLIP + exact 0.5 -> Elo side
NO_MARKET -> Elo side
NFL -> existing behavior until its own model is separately approved
```

The rationale names the deciding method and probability. The rendered sheet
therefore remains auditable without opening the database.

If Candidate 1 fails, no artifact is created and no live decision code is
enabled for CFB.

## 7. Module boundaries

| Module | Responsibility |
|---|---|
| `backtest/snapshots.py` | Pure exact and max-age-batched request planning |
| `backtest/archive.py` | Sport-aware, resumable execution of an approved plan |
| `store/schema.sql`, `store/db.py` | Transactional request ledger and append-only line storage |
| `backtest/coinflip.py` | Pure feature rows, fold construction, paired metrics, and reports |
| `edge/market_tiebreak.py` | Artifact validation and pure probability/side inference |
| `edge/pipeline.py` | Chooses the allowed decider by tier and sport |
| `cli.py` | Dry-run, acquisition, evaluation, and human-readable summaries |

Data acquisition, experiment fitting, and live inference remain separate. The
edge package performs no database, filesystem, or network I/O.

## 8. Error handling and paid-operation safety

- Default archive behavior is always a dry run.
- Paid execution requires `--execute`, an explicit sport, season range,
  maximum snapshot age, and credit ceiling.
- CFB execution refuses a maximum snapshot age other than the approved
  90 minutes unless a new dry-run cost and design change are reviewed.
- Insufficient balance, changed planned cost, unknown teams in the stored CFB
  schedule, invalid kickoff timing, an archive 4xx/5xx, or quota exhaustion
  stops the run and reports the exact request.
- Rows skipped by the nationwide NCAAF feed remain visible, following the
  existing firehose policy. A planned game missing from a valid snapshot is a
  visible coverage gap, not a fabricated row or a reason to repurchase the same
  snapshot. A valid zero-match response is marked complete with zero lines and
  included in the coverage report. The initial 10-credit verification request
  is deliberately chosen from a covered marquee week and must return matched
  lines before bulk execution continues.
- No partial response is marked complete.
- The database is backed up before the first paid request. The backup path and
  checksum are printed; the original remains the acquisition target.
- The `lines` table remains append-only.

## 9. Testing and verification

### Offline tests

- exact planner default reproduces all existing NFL requests;
- batching assigns every game exactly once and enforces the 15–90 minute age
  bound at boundary values;
- frozen anchors remain one per season/week;
- credit estimates equal ten times pending requests;
- NFL and CFB select the correct Odds API sport key;
- dry runs create no client and write nothing;
- completed ledger rows are skipped on resume;
- line insertion and ledger completion commit or roll back together;
- missing planned games abort the request;
- feature formulas use the latest row per book and exclude pushes;
- every outer prediction is trained only on earlier seasons;
- the 2021 week split and later inner folds select only from the fixed `C` grid;
- paired metrics compare identical game IDs;
- artifact validation rejects every malformed contract field;
- accepted artifacts affect only CFB `COINFLIP` games;
- `STRONG`, `LEAN`, `NO_MARKET`, and all NFL behavior remain unchanged; and
- the full evaluator is deterministic.

### Paid-run checkpoints

1. back up `data/pickem.duckdb` and record its checksum;
2. verify the free quota preflight and exact 10,180-credit pending plan;
3. purchase and inspect the first planned request;
4. complete and verify 2021, then each later season in order;
5. assert zero orphaned lines and zero submission snapshots at or after
   kickoff;
6. report actual credits, matched games, missing games, books per game, and
   snapshot-age distribution; and
7. run the walk-forward evaluation once and commit its complete result,
   including a null result.

## 10. Out of scope

- NFL model training or deployment
- CFB features beyond the three market summaries
- automated CBS submission
- opponent-pick or contrarian strategy
- confidence or bankroll optimization
- changes to `STRONG` or `LEAN` thresholds
- in-season refitting during 2026
- Candidate 2 unless Candidate 1 is first frozen and reported as a null

## 11. Deliverables

1. CFB-capable, resumable, dry-run-first archive acquisition
2. verified 2021–2025 frozen and submission proxy rows
3. deterministic CFB walk-forward experiment and saved predictions
4. committed experiment report
5. a live CFB model artifact and pipeline integration only if the acceptance
   gate passes
6. a prospective 2026 audit trail regardless of the historical result
