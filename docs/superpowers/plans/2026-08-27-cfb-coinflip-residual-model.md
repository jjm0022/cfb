# CFB COINFLIP Candidate 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and freeze the season-locked CFB market-residual ridge experiment, retain Elo on a null result, and make a passing 2021–2025 artifact immediately eligible for remaining 2026 `COINFLIP` picks.

**Architecture:** A pure deep module accepts stored games and the two market-proxy streams, owns leakage-safe row construction, ridge fitting, chronological calibration, paired evaluation, certification, and deterministic rendering, and returns one audit-ready result. The CLI is an I/O adapter. Artifact creation and live pipeline integration are conditional tasks that execute only after the frozen historical result passes every gate.

**Tech Stack:** Python 3.13, Pydantic 2, NumPy, scikit-learn `Ridge`, DuckDB, Typer, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-27-cfb-coinflip-residual-model-design.md`

## Global Constraints

- Raw ATS pick accuracy is the primary objective; do not introduce ROI, vig, bankroll, opponent-pick, contrarian, or confidence-pool logic.
- Preserve the existing divergence decisions for `STRONG` and `LEAN`; Candidate 2 may decide only CFB `COINFLIP` rows.
- Use only stored 2021–2025 games, final scores, `oddsapi:frozen`, and `oddsapi:submit` lines; make no network call and perform no paid acquisition.
- Never read a 2026 result during fitting, selection, calibration, evaluation, or artifact creation.
- Freeze the 365-day half-life, `alpha` grid `{10, 30, 100}`, empirical probability rule, zero threshold, comparators, folds, and acceptance gate before the result.
- Candidate 1's code, frozen JSONL, report, gate, and result remain byte-for-byte unchanged.
- Every model fit for an outer test season uses only earlier seasons and remains fixed throughout the test season.
- A present invalid artifact fails loudly; an absent artifact retains Elo.
- If Task 6 records `NULL`, stop the plan after updating the result and handoff. Tasks 7–9 are forbidden on a null result.

---

## File Structure

### Unconditional experiment files

- Create `src/pickem/backtest/coinflip_residual.py`: pure row building, ridge fitting, validation, metrics, certification, and deterministic rendering.
- Create `tests/test_coinflip_residual.py`: behavior tests through the experiment interface plus focused tests at private mathematical seams.
- Modify `src/pickem/cli.py`: add the CFB-only, read-only `evaluate-coinflip-residual` adapter.
- Modify `tests/test_cli.py`: command refusal, determinism, output, and no-artifact tests.
- Create `docs/research/2026-08-27-cfb-coinflip-residual-result.md`: frozen PASS or NULL report produced in Task 6.
- Modify `docs/HANDOFF.md`: record the result and exact operating rule.
- Write ignored `data/experiments/cfb-coinflip-residual-2021-2025.jsonl`: frozen prediction stream whose SHA-256 is committed in the result document.

### Conditional PASS-only files

- Create `src/pickem/edge/market_residual.py`: strict artifact schema and pure live inference.
- Create `tests/test_market_residual.py`: artifact validation and exact inference tests.
- Modify `src/pickem/backtest/coinflip_residual.py`: final 2026 artifact fit and deterministic serialization.
- Modify `src/pickem/edge/pipeline.py`: route only CFB `COINFLIP` through an accepted artifact.
- Modify `src/pickem/config.py`: define the default local artifact path.
- Modify `src/pickem/cli.py`: load and validate the artifact in `report`; perform no fit.
- Modify `tests/test_pipeline.py`, `tests/test_report.py`, and `tests/test_cli.py`: conditional routing, rationale, invalid-artifact, and no-refit coverage.
- Write ignored `data/models/cfb-coinflip-residual-v1.json`: final passing artifact trained through 2025.

---

### Task 1: Build Leakage-Safe Residual and Evaluation Rows

**Files:**
- Create: `src/pickem/backtest/coinflip_residual.py`
- Create: `tests/test_coinflip_residual.py`

**Interfaces:**
- Consumes: `Game`, `MarketLine`, `CoinflipRow`, `build_coinflip_rows`, `consensus_spread`, and `latest_by_book`.
- Produces: `ResidualTrainingRow`, `ResidualDataset`, and `build_residual_dataset(games, frozen_lines, submission_lines) -> ResidualDataset`.

- [ ] **Step 1: Write failing row-contract tests**

Create fixed CFB games and market lines and assert the continuous target, latest-book behavior, push inclusion, pre-kickoff filtering, and exact reuse of Candidate 1 membership:

```python
def test_residual_dataset_uses_market_error_and_reuses_coinflip_population():
    dataset = build_residual_dataset(GAMES, FROZEN, SUBMISSION)
    row = next(row for row in dataset.training_rows if row.game_id == GAME.game_id)
    assert row.submission_spread == -2.0
    assert row.market_error == (27 - 24) + (-2.0)
    assert {row.game_id for row in dataset.evaluation_rows} == {
        row.game_id for row in build_coinflip_rows(GAMES, FROZEN, SUBMISSION).rows
    }


def test_training_keeps_an_ats_push_as_a_continuous_market_error():
    pushed = GAME.model_copy(update={"home_score": 20, "away_score": 17})
    [row] = build_residual_dataset([pushed], FROZEN, SUBMISSION).training_rows
    assert row.market_error == 1.0


def test_training_ignores_in_play_quotes_and_reports_missing_consensus():
    in_play = SUBMISSION[0].model_copy(update={"captured_at": GAME.kickoff_utc})
    result = build_residual_dataset([GAME], FROZEN, [in_play])
    assert result.training_rows == []
    assert result.training_skipped == [
        f"{GAME.game_id}: missing submission consensus before kickoff"
    ]
```

- [ ] **Step 2: Run the row tests to verify RED**

Run: `uv run pytest tests/test_coinflip_residual.py -k 'dataset or training' -v`

Expected: collection fails because `pickem.backtest.coinflip_residual` does not exist.

- [ ] **Step 3: Implement deterministic row construction**

Add these exact models and function shape:

```python
class ResidualTrainingRow(BaseModel):
    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    home_team_id: str
    away_team_id: str
    submission_spread: float
    market_error: float


class ResidualDataset(BaseModel):
    training_rows: list[ResidualTrainingRow]
    evaluation_rows: list[CoinflipRow]
    training_skipped: list[str]
    evaluation_skipped: list[str]


def build_residual_dataset(
    games: Sequence[Game],
    frozen_lines: Sequence[MarketLine],
    submission_lines: Sequence[MarketLine],
) -> ResidualDataset:
    evaluation = build_coinflip_rows(games, frozen_lines, submission_lines)
    submission_by_game = _group_by_game(submission_lines)
    training_rows: list[ResidualTrainingRow] = []
    skipped: list[str] = []
    for game in sorted(games, key=lambda row: (row.season, row.week, row.game_id)):
        if game.sport is not Sport.CFB:
            skipped.append(f"{game.game_id}: non-CFB game is outside Candidate 2")
            continue
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        available = [
            line
            for line in submission_by_game.get(game.game_id, [])
            if line.captured_at < game.kickoff_utc and math.isfinite(line.spread_home)
        ]
        latest = latest_by_book(available)
        spread = consensus_spread(latest)
        if spread is None or not math.isfinite(spread):
            skipped.append(f"{game.game_id}: missing submission consensus before kickoff")
            continue
        training_rows.append(
            ResidualTrainingRow(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                kickoff_utc=game.kickoff_utc,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                submission_spread=spread,
                market_error=game.home_score - game.away_score + spread,
            )
        )
    return ResidualDataset(
        training_rows=training_rows,
        evaluation_rows=evaluation.rows,
        training_skipped=skipped,
        evaluation_skipped=evaluation.skipped,
    )
```

Keep grouping helpers private and sort every returned collection deterministically.

- [ ] **Step 4: Run Task 1 tests and lint**

Run: `uv run pytest tests/test_coinflip_residual.py -k 'dataset or training' -v`

Expected: all selected tests pass.

Run: `uv run ruff check src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py`

Expected: `All checks passed!`

- [ ] **Step 5: Commit Task 1**

```bash
git add src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py
git commit -m "feat: build CFB market residual rows"
```

### Task 2: Fit the Fixed Ridge Team-Effect Model

**Files:**
- Modify: `src/pickem/backtest/coinflip_residual.py`
- Modify: `tests/test_coinflip_residual.py`

**Interfaces:**
- Consumes: `ResidualTrainingRow` from Task 1.
- Produces: `ResidualFit`, `_recency_weights`, `_fit_residual_model`, and `_predict_market_error` for the evaluator in Tasks 3–4.

- [ ] **Step 1: Write failing mathematical contract tests**

```python
def test_recency_weight_has_the_frozen_365_day_half_life():
    cutoff = datetime(2025, 8, 25, tzinfo=UTC)
    rows = [training_row(kickoff=cutoff), training_row(kickoff=cutoff - timedelta(days=365))]
    assert _recency_weights(rows, cutoff).tolist() == pytest.approx([1.0, 0.5])


def test_team_encoding_is_home_plus_one_away_minus_one_and_unknown_zero():
    fit = _fit_residual_model(SYMMETRIC_ROWS, CUTOFF, alpha=30.0)
    assert fit.teams == sorted(fit.teams)
    assert _predict_market_error(fit, "KNOWN_HOME", "KNOWN_AWAY") == pytest.approx(
        fit.intercept + fit.team_effects["KNOWN_HOME"] - fit.team_effects["KNOWN_AWAY"]
    )
    assert _predict_market_error(fit, "NEW_HOME", "NEW_AWAY") == pytest.approx(fit.intercept)


def test_fit_rejects_rows_at_or_after_its_cutoff():
    with pytest.raises(ValueError, match="strictly before fit cutoff"):
        _fit_residual_model([training_row(kickoff=CUTOFF)], CUTOFF, alpha=30.0)
```

- [ ] **Step 2: Run Task 2 tests to verify RED**

Run: `uv run pytest tests/test_coinflip_residual.py -k 'recency or encoding or fit_rejects' -v`

Expected: imports fail because the fitting functions are absent.

- [ ] **Step 3: Implement ridge fitting with a sorted design matrix**

```python
ALPHA_GRID = (10.0, 30.0, 100.0)
HALF_LIFE_DAYS = 365.0


class ResidualFit(BaseModel):
    cutoff_utc: datetime
    alpha: float
    intercept: float
    team_effects: dict[str, float]
    training_rows: int
    effective_weight: float


def _recency_weights(rows: Sequence[ResidualTrainingRow], cutoff: datetime) -> np.ndarray:
    ages = np.asarray([(cutoff - row.kickoff_utc).total_seconds() / 86400 for row in rows])
    if np.any(ages <= 0):
        raise ValueError("every training row must be strictly before fit cutoff")
    return np.power(2.0, -ages / HALF_LIFE_DAYS)


def _fit_residual_model(
    rows: Sequence[ResidualTrainingRow], cutoff: datetime, alpha: float
) -> ResidualFit:
    if alpha not in ALPHA_GRID:
        raise ValueError(f"alpha must be one of {ALPHA_GRID}")
    teams = sorted({team for row in rows for team in (row.home_team_id, row.away_team_id)})
    if not rows or not teams:
        raise ValueError("residual fit requires at least one training row")
    index = {team: column for column, team in enumerate(teams)}
    matrix = np.zeros((len(rows), len(teams)), dtype=float)
    for row_index, row in enumerate(rows):
        matrix[row_index, index[row.home_team_id]] = 1.0
        matrix[row_index, index[row.away_team_id]] = -1.0
    weights = _recency_weights(rows, cutoff)
    target = np.asarray([row.market_error for row in rows], dtype=float)
    model = Ridge(alpha=alpha, fit_intercept=True).fit(matrix, target, sample_weight=weights)
    return ResidualFit(
        cutoff_utc=cutoff,
        alpha=alpha,
        intercept=float(model.intercept_),
        team_effects={team: float(model.coef_[index[team]]) for team in teams},
        training_rows=len(rows),
        effective_weight=float(np.sum(weights)),
    )


def _predict_market_error(fit: ResidualFit, home: str, away: str) -> float:
    return fit.intercept + fit.team_effects.get(home, 0.0) - fit.team_effects.get(away, 0.0)
```

Assert every serialized coefficient and prediction is finite.

- [ ] **Step 4: Run Task 2 tests and the focused suite**

Run: `uv run pytest tests/test_coinflip_residual.py -v`

Expected: all tests pass.

Run: `uv run ruff check src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py`

Expected: `All checks passed!`

- [ ] **Step 5: Commit Task 2**

```bash
git add src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py
git commit -m "feat: fit ridge market residual effects"
```

### Task 3: Add Chronological Alpha Selection and Empirical Probabilities

**Files:**
- Modify: `src/pickem/backtest/coinflip_residual.py`
- Modify: `tests/test_coinflip_residual.py`

**Interfaces:**
- Consumes: `ResidualDataset`, `ResidualFit`, and fitting functions from Tasks 1–2.
- Produces: `WeightedResidual`, `InnerSelection`, `_select_alpha`, and `_probability_home` for Task 4.

- [ ] **Step 1: Write failing chronology and probability tests**

```python
def test_first_outer_fold_uses_early_2021_to_validate_late_2021():
    partitions = _inner_partitions(DATASET, test_season=2022)
    assert {(row.season, row.week) for row in partitions[0].training_rows} <= {
        (2021, week) for week in range(1, 9)
    }
    assert all(row.season == 2021 and row.week >= 9 for row in partitions[0].validation_rows)


def test_later_outer_fold_uses_only_expanding_prior_seasons():
    partitions = _inner_partitions(DATASET, test_season=2025)
    assert [partition.validation_season for partition in partitions] == [2022, 2023, 2024]
    assert all(
        row.season < partition.validation_season
        for partition in partitions
        for row in partition.training_rows
    )


def test_tied_inner_accuracy_selects_largest_alpha():
    selection = _select_alpha(TIED_DATASET, test_season=2025)
    assert selection.alpha == 100.0


def test_weighted_empirical_probability_uses_half_weight_for_ties_and_smoothing():
    residuals = [
        WeightedResidual(error=-1.0, weight=1.0),
        WeightedResidual(error=0.0, weight=1.0),
        WeightedResidual(error=2.0, weight=1.0),
    ]
    assert _probability_home(0.0, residuals) == pytest.approx((0.5 + 1.0 + 0.5) / 4.0)


def test_fewer_than_one_hundred_oof_residuals_fails_closed():
    with pytest.raises(ValueError, match="at least 100"):
        _probability_home(1.0, [WeightedResidual(error=0.0, weight=1.0)] * 99)
```

- [ ] **Step 2: Run Task 3 tests to verify RED**

Run: `uv run pytest tests/test_coinflip_residual.py -k 'outer_fold or inner_accuracy or empirical or one_hundred' -v`

Expected: imports or assertions fail because chronological selection is absent.

- [ ] **Step 3: Implement inner partitions, selection, and OOF residual state**

Add Pydantic records with deterministic list ordering:

```python
class WeightedResidual(BaseModel):
    error: float
    weight: float


class InnerSelection(BaseModel):
    alpha: float
    correct_by_alpha: dict[float, int]
    residuals: list[WeightedResidual]


def _probability_home(ats_margin: float, residuals: Sequence[WeightedResidual]) -> float:
    if len(residuals) < 100:
        raise ValueError("probability calibration requires at least 100 OOF residuals")
    threshold = -ats_margin
    total = sum(row.weight for row in residuals)
    above = sum(row.weight for row in residuals if row.error > threshold)
    tied = sum(row.weight for row in residuals if row.error == threshold)
    return (0.5 + above + 0.5 * tied) / (1.0 + total)
```

For each alpha, fit every chronological partition on all valid training-population rows and score raw accuracy only on that partition's `COINFLIP` evaluation rows. Choose the largest alpha among tied maximum correct counts. After selecting alpha, replay the same partitions and retain every validation training-population residual error, reweighted relative to the outer cutoff. Sort residuals by `(error, weight)` before returning them.

- [ ] **Step 4: Run Task 3 tests and formatting checks**

Run: `uv run pytest tests/test_coinflip_residual.py -v`

Expected: all tests pass.

Run: `uv run ruff check src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py && uv run ruff format --check src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py`

Expected: lint passes and both files are already formatted.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py
git commit -m "feat: select residual ridge chronologically"
```

### Task 4: Evaluate Outer Seasons, Comparators, Metrics, and Gate

**Files:**
- Modify: `src/pickem/backtest/coinflip_residual.py`
- Modify: `tests/test_coinflip_residual.py`

**Interfaces:**
- Consumes: all Task 1–3 records and functions plus `replay_elo_sides`.
- Produces: `ResidualPrediction`, `ResidualFold`, `ResidualEvaluation`, `evaluate_residual_candidate`, `passes_residual_gate`, `residual_evaluations_are_byte_identical`, `render_residual_predictions_jsonl`, and `render_residual_report`.

- [ ] **Step 1: Write failing outer-evaluation and fail-closed gate tests**

```python
def test_outer_models_are_season_locked_and_predict_the_exact_paired_population():
    result = evaluate_residual_candidate(GAMES_2021_2025, FROZEN, SUBMISSION)
    assert [(fold.train_through, fold.test_season) for fold in result.folds] == [
        (2021, 2022), (2022, 2023), (2023, 2024), (2024, 2025)
    ]
    assert all(fold.cutoff_utc == min(p.kickoff_utc for p in result.predictions if p.season == fold.test_season) for fold in result.folds)
    assert {p.game_id for p in result.predictions} == {
        row.game_id for row in build_coinflip_rows(GAMES_2021_2025, FROZEN, SUBMISSION).rows if row.season >= 2022
    }


def test_primary_comparator_is_frozen_line_favorite_and_zero_picks_home():
    prediction = _score_prediction(EVALUATION_ROW.model_copy(update={"frozen_spread": 0.0}), FIT, RESIDUALS, Side.AWAY)
    assert prediction.favorite_side is Side.HOME


def test_gate_requires_every_predeclared_condition():
    passing = PASSING_EVALUATION.model_copy(update={"deterministic": True})
    assert passes_residual_gate(passing)
    mutations = [
        {"accuracy_delta": 0.009},
        {"positive_seasons": 2},
        {"bootstrap_lower": 0.0},
        {"brier_score": 0.25},
        {"calibration_safe": False},
        {"leakage_safe": False},
        {"deterministic": False},
        {"uses_2026_outcomes": True},
    ]
    assert all(not passes_residual_gate(passing.model_copy(update=change)) for change in mutations)


def test_bootstrap_is_paired_by_season_week_and_deterministic():
    first = summarize_residual_predictions(MANUAL_PREDICTIONS, MANUAL_FOLDS)
    second = summarize_residual_predictions(MANUAL_PREDICTIONS, MANUAL_FOLDS)
    assert (first.bootstrap_lower, first.bootstrap_upper) == (
        second.bootstrap_lower,
        second.bootstrap_upper,
    )
```

- [ ] **Step 2: Run Task 4 tests to verify RED**

Run: `uv run pytest tests/test_coinflip_residual.py -k 'outer_models or primary_comparator or gate_requires or bootstrap' -v`

Expected: tests fail because the outer evaluation and gate do not exist.

- [ ] **Step 3: Implement the deep evaluator and deterministic report**

Define predictions with candidate, favorite, Elo, and always-home sides and correctness on the same row. Loop over test seasons 2022–2025; for each season, call `_select_alpha`, fit once at the earliest test kickoff, and reuse that fit for every test row.

Implement the gate exactly:

```python
def passes_residual_gate(result: ResidualEvaluation) -> bool:
    return (
        result.accuracy_delta is not None
        and result.accuracy_delta >= 0.01
        and result.positive_seasons >= 3
        and result.bootstrap_lower is not None
        and result.bootstrap_lower > 0.0
        and result.brier_score is not None
        and result.brier_score < 0.25
        and result.calibration_safe
        and result.leakage_safe
        and result.deterministic
        and not result.uses_2026_outcomes
    )
```

Use the existing five calibration bins. Set `calibration_safe` only when every bin with at least 100 rows has absolute mean-probability versus observed-rate error at most `0.05`. Use a NumPy generator seeded with `20260827`, 10,000 season/week cluster resamples, and candidate-minus-favorite accuracy. Certification booleans default to false and are derived from actual fold state.

Serialize sorted Pydantic JSON with `sort_keys=True`, compact separators, and one trailing newline per prediction. The Markdown report must include fold state, all comparator counts, season deltas, Brier/log loss, calibration, bootstrap, every gate row, proxy caveat, exclusions, prediction hash, and `Candidate 2: PASS` or `Candidate 2: NULL — retain Elo`.

- [ ] **Step 4: Run the full residual suite and Candidate 1 regression suite**

Run: `uv run pytest tests/test_coinflip_residual.py tests/test_coinflip.py -q`

Expected: all tests pass and Candidate 1 tests remain unchanged.

Run: `uv run ruff check src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py`

Expected: `All checks passed!`

- [ ] **Step 5: Commit Task 4**

```bash
git add src/pickem/backtest/coinflip_residual.py tests/test_coinflip_residual.py
git commit -m "feat: evaluate CFB residual candidate"
```

### Task 5: Add the Read-Only Candidate 2 CLI Adapter

**Files:**
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4's evaluator, comparison, renderer, and gate functions.
- Produces: Typer command `evaluate-coinflip-residual`; no database writes, network client, or model artifact.

- [ ] **Step 1: Write failing CLI contract tests**

```python
def test_evaluate_coinflip_residual_writes_byte_identical_audit_artifacts(tmp_path):
    db = tmp_path / "residual.duckdb"
    _seed_residual_experiment(db)
    command = [
        "evaluate-coinflip-residual", "--sport", "cfb", "--from", "2021", "--to", "2025", "--db", str(db)
    ]
    first = runner.invoke(app, [*command, "--predictions", str(tmp_path / "a.jsonl"), "--report", str(tmp_path / "a.md")])
    second = runner.invoke(app, [*command, "--predictions", str(tmp_path / "b.jsonl"), "--report", str(tmp_path / "b.md")])
    assert first.exit_code == second.exit_code == 0
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    assert (tmp_path / "a.md").read_bytes() == (tmp_path / "b.md").read_bytes()
    assert not (tmp_path / "cfb-coinflip-residual-v1.json").exists()


def test_evaluate_coinflip_residual_refuses_nfl_and_other_ranges(tmp_path):
    nfl = runner.invoke(app, ["evaluate-coinflip-residual", "--sport", "nfl", "--predictions", str(tmp_path / "p"), "--report", str(tmp_path / "r")])
    changed = runner.invoke(app, ["evaluate-coinflip-residual", "--sport", "cfb", "--from", "2020", "--to", "2025", "--predictions", str(tmp_path / "p"), "--report", str(tmp_path / "r")])
    assert nfl.exit_code != 0 and "CFB-only" in nfl.output
    assert changed.exit_code != 0 and "only permits" in changed.output


def test_evaluate_coinflip_residual_runs_twice_before_certifying(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "evaluate_residual_candidate", mismatching_second_run)
    result = runner.invoke(app, APPROVED_RESIDUAL_COMMAND)
    assert result.exit_code != 0
    assert "not deterministic" in str(result.exception)


def test_evaluate_coinflip_residual_does_not_mutate_the_database(tmp_path):
    db = tmp_path / "residual.duckdb"
    _seed_residual_experiment(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = runner.invoke(app, APPROVED_RESIDUAL_COMMAND)
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
```

- [ ] **Step 2: Run CLI tests to verify RED**

Run: `uv run pytest tests/test_cli.py -k 'coinflip_residual' -v`

Expected: tests fail because the command is not registered.

- [ ] **Step 3: Implement the CLI adapter**

Add imports from `pickem.backtest.coinflip_residual` and this command shape:

```python
@app.command("evaluate-coinflip-residual")
def evaluate_coinflip_residual_cmd(
    sport: Sport = typer.Option(...),
    start: int = typer.Option(2021, "--from"),
    end: int = typer.Option(2025, "--to"),
    predictions: Path = typer.Option(...),
    report: Path = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    if sport is not Sport.CFB:
        typer.secho("evaluate-coinflip-residual is an approved CFB-only experiment", fg="red", err=True)
        raise typer.Exit(code=1)
    if (start, end) != (2021, 2025):
        typer.secho("evaluate-coinflip-residual only permits --from 2021 --to 2025", fg="red", err=True)
        raise typer.Exit(code=1)
    with Store(db) as store:
        stored = store.load_seasons(Sport.CFB, start, end)
    frozen, submission, proxy_skipped = split_proxies(stored.market_lines)
    first = evaluate_residual_candidate(stored.games, frozen, submission)
    second = evaluate_residual_candidate(stored.games, frozen, submission)
    if not residual_evaluations_are_byte_identical(first, second):
        raise RuntimeError("Candidate 2 evaluation is not deterministic")
    result = first.model_copy(update={"deterministic": True})
    prediction_bytes = render_residual_predictions_jsonl(result).encode()
    predictions.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    predictions.write_bytes(prediction_bytes)
    report.write_text(
        render_residual_report(
            result,
            prediction_sha256=hashlib.sha256(prediction_bytes).hexdigest(),
            proxy_skipped=proxy_skipped,
        )
    )
    typer.echo(f"Candidate 2: {'PASS' if passes_residual_gate(result) else 'NULL — retain Elo'}")
```

Use `Store(db)` directly rather than `_store(db)`, because `_store` runs schema initialization. Confirm the adapter executes no write method, leaves the database hash unchanged, and constructs no API client.

- [ ] **Step 4: Run focused and full pre-experiment checks**

Run: `uv run pytest tests/test_cli.py -k 'coinflip or help' -q`

Expected: all selected tests pass and help lists both Candidate 1 and Candidate 2 commands.

Run: `uv run pytest tests/test_coinflip_residual.py tests/test_coinflip.py tests/test_cli.py -q`

Expected: all tests pass.

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

Expected: lint passes and all Python files are formatted.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/pickem/cli.py tests/test_cli.py
git commit -m "feat: add CFB residual evaluation command"
```

### Task 6: Freeze the Historical Result and Enforce the Stop Gate

**Files:**
- Create: `docs/research/2026-08-27-cfb-coinflip-residual-result.md`
- Modify: `docs/HANDOFF.md`
- Write ignored: `data/experiments/cfb-coinflip-residual-2021-2025.jsonl`

**Interfaces:**
- Consumes: the approved command from Task 5 and the operational database at `/Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb`.
- Produces: one frozen PASS or NULL decision, committed result documentation, and the sole authorization gate for Tasks 7–9.

- [ ] **Step 1: Run all verification before exposing the frozen result**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
test "$(shasum -a 256 data/experiments/cfb-coinflip-2021-2025.jsonl | cut -d' ' -f1)" = "5b2a3cff74d7009b3982a71ecbbe1bd49fbd4d3da5bf9b00f98a7bc7cf6b6d5c"
```

Expected: zero test failures, `All checks passed!`, and all Python files formatted.

- [ ] **Step 2: Evaluate twice, compare, and write the frozen outputs once**

Run:

```bash
test ! -e data/experiments/cfb-coinflip-residual-2021-2025.jsonl
test ! -e docs/research/2026-08-27-cfb-coinflip-residual-result.md
residual_eval_tmp=$(mktemp -d /private/tmp/cfb-residual-eval.XXXXXX)
uv run pickem evaluate-coinflip-residual --sport cfb --from 2021 --to 2025 --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb --predictions data/experiments/cfb-coinflip-residual-2021-2025.jsonl --report docs/research/2026-08-27-cfb-coinflip-residual-result.md
uv run pickem evaluate-coinflip-residual --sport cfb --from 2021 --to 2025 --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb --predictions "$residual_eval_tmp/second.jsonl" --report "$residual_eval_tmp/second.md"
cmp data/experiments/cfb-coinflip-residual-2021-2025.jsonl "$residual_eval_tmp/second.jsonl"
cmp docs/research/2026-08-27-cfb-coinflip-residual-result.md "$residual_eval_tmp/second.md"
shasum -a 256 data/experiments/cfb-coinflip-residual-2021-2025.jsonl docs/research/2026-08-27-cfb-coinflip-residual-result.md
```

Expected: both `cmp` commands exit zero. Do not rerun with a changed contract if the result is disappointing.

- [ ] **Step 3: Verify the frozen population and absence of 2026 outcomes**

Run:

```bash
.venv/bin/python - data/experiments/cfb-coinflip-residual-2021-2025.jsonl <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1])]
assert len(rows) == 1577
assert sum(not row["is_push"] for row in rows) == 1549
assert {row["season"] for row in rows} == {2022, 2023, 2024, 2025}
assert all(row["season"] != 2026 for row in rows)
print("verified 1577 paired predictions, 1549 decided, zero 2026 rows")
PY
```

Expected: the exact verification line prints. Any mismatch stops the run as an implementation defect, not a model null.

- [ ] **Step 4: Document exactly one result**

Treat `data/experiments/cfb-coinflip-residual-2021-2025.jsonl` and `docs/research/2026-08-27-cfb-coinflip-residual-result.md` as frozen. Record the prediction SHA-256, comparator counts, season deltas, Brier/log loss, bootstrap interval, calibration, every gate, exclusions, and `PASS` or `NULL` in `docs/HANDOFF.md`.

If the report says `NULL — retain Elo`, write these binding statements in the handoff:

```text
Candidate 2 is NULL. Elo remains the CFB COINFLIP decider. No Candidate 2 model artifact exists. The team-residual family is closed; Tasks 7–9 were not started.
```

If the report says `PASS`, write:

```text
Candidate 2 passed every frozen historical gate and is authorized for final 2021–2025 fitting and immediate 2026 COINFLIP integration under the approved immutable-artifact contract.
```

- [ ] **Step 5: Commit the frozen result, then obey the branch**

```bash
git add docs/research/2026-08-27-cfb-coinflip-residual-result.md docs/HANDOFF.md
git commit -m "docs: freeze CFB residual candidate result"
```

If NULL: stop this plan and perform no task below.

If PASS: continue to Task 7 without changing any model choice or historical output.

### Task 7: Create the Strict 2026 Artifact and Pure Inference — PASS ONLY

**Files:**
- Create: `src/pickem/edge/market_residual.py`
- Create: `tests/test_market_residual.py`
- Modify: `src/pickem/backtest/coinflip_residual.py`
- Modify: `tests/test_coinflip_residual.py`
- Modify: `src/pickem/store/db.py`
- Modify: `tests/test_store.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_cli.py`
- Write ignored: `data/models/cfb-coinflip-residual-v1.json`

**Interfaces:**
- Consumes: a PASS result from Task 6 and the frozen Task 1–4 fit/calibration functions.
- Produces: `ResidualArtifact`, `ResidualDecision`, `decide_residual`, `fit_residual_artifact`, `Store.first_kickoff`, guarded CLI command `fit-coinflip-residual-artifact`, and deterministic artifact JSON.

- [ ] **Step 1: Assert Task 6 passed before writing a test**

Run: `rg -n '^Candidate 2: PASS|Candidate 2 passed every frozen' docs/research/2026-08-27-cfb-coinflip-residual-result.md docs/HANDOFF.md`

Expected: both files explicitly record PASS. If not, stop.

- [ ] **Step 2: Write failing artifact and inference tests**

```python
def test_artifact_rejects_wrong_sport_duplicate_teams_and_nonfinite_state():
    with pytest.raises(ValidationError):
        ResidualArtifact.model_validate({**VALID_ARTIFACT, "sport": "nfl"})
    with pytest.raises(ValidationError):
        ResidualArtifact.model_validate({**VALID_ARTIFACT, "team_effects": [["MIA", 1.0], ["MIA", 2.0]]})
    with pytest.raises(ValidationError):
        ResidualArtifact.model_validate({**VALID_ARTIFACT, "intercept": float("nan")})


def test_inference_uses_market_correction_and_unknown_team_zero():
    artifact = ResidualArtifact.model_validate(VALID_ARTIFACT)
    decision = decide_residual(artifact, "NEW_HOME", "NEW_AWAY", frozen_spread=-3.0, submission_spread=-4.0)
    assert decision.predicted_market_error == pytest.approx(artifact.intercept)
    assert decision.predicted_ats_margin == pytest.approx(1.0 + artifact.intercept)


def test_final_artifact_uses_only_2021_2025_and_matches_in_memory_fit():
    artifact = fit_residual_artifact(GAMES_2021_2025, FROZEN, SUBMISSION, cutoff_2026=CUTOFF_2026, hashes=HASHES)
    assert artifact.training_from == 2021
    assert artifact.training_through == 2025
    assert artifact.cutoff_utc == CUTOFF_2026
    assert serialize_residual_artifact(artifact) == serialize_residual_artifact(artifact)


def test_first_kickoff_reads_schedule_metadata_without_a_score_interface(store):
    store.upsert_games([CFB_2026_LATE, CFB_2026_EARLY])
    assert store.first_kickoff(Sport.CFB, 2026) == CFB_2026_EARLY.kickoff_utc


def test_artifact_command_refuses_a_null_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "evaluate_residual_candidate", lambda *args: NULL_EVALUATION)
    result = runner.invoke(app, FIT_ARTIFACT_COMMAND)
    assert result.exit_code != 0
    assert "historical gate is NULL" in result.output
    assert not (tmp_path / "artifact.json").exists()
```

- [ ] **Step 3: Run PASS-only artifact tests to verify RED**

Run: `uv run pytest tests/test_market_residual.py tests/test_coinflip_residual.py tests/test_store.py tests/test_cli.py -k 'artifact or inference or first_kickoff' -v`

Expected: imports fail because the artifact module is absent.

- [ ] **Step 4: Implement strict schema, final fit, inference, and artifact write**

Use strict Pydantic models with format version `1`, sport fixed to `cfb`, sorted unique `(team_id, effect)` pairs, finite-number validation, contract identifier `cfb-market-residual-ridge-v1`, and required SHA-256 fields for canonical training inputs, frozen predictions, and the approved spec.

```python
class ResidualDecision(BaseModel):
    predicted_market_error: float
    predicted_ats_margin: float
    probability_home: float
    side: Side | None


def decide_residual(
    artifact: ResidualArtifact,
    home_team_id: str,
    away_team_id: str,
    frozen_spread: float,
    submission_spread: float,
) -> ResidualDecision:
    effects = dict(artifact.team_effects)
    error = artifact.intercept + effects.get(home_team_id, 0.0) - effects.get(away_team_id, 0.0)
    margin = frozen_spread - submission_spread + error
    probability = empirical_probability(margin, artifact.residuals)
    side = Side.HOME if margin > 0 else Side.AWAY if margin < 0 else None
    return ResidualDecision(predicted_market_error=error, predicted_ats_margin=margin, probability_home=probability, side=side)
```

Add a narrow store read that selects only schedule metadata:

```python
def first_kickoff(self, sport: Sport, season: int) -> datetime | None:
    row = self._con.execute(
        "SELECT MIN(kickoff_utc) FROM games WHERE sport = ? AND season = ?",
        [sport.value, season],
    ).fetchone()
    return row[0] if row and row[0] is not None else None
```

Add `fit-coinflip-residual-artifact`, restricted to CFB, training range 2021–2025, and deployment season 2026. It loads only 2021–2025 training data, obtains the deployment cutoff through `Store.first_kickoff`, independently repeats the frozen evaluation, requires byte identity and `passes_residual_gate`, hashes the frozen predictions and approved spec, fits once, validates serialize/parse parity, and writes the explicit `--out` path. It must refuse a missing 2026 cutoff, a NULL evaluation, changed prediction hash, other sport/range, or an existing output path.

Fit the final artifact through 2025 at the kickoff-only 2026 cutoff. Write canonical compact sorted JSON with a trailing newline and verify parse/serialize parity before saving `data/models/cfb-coinflip-residual-v1.json`.

- [ ] **Step 5: Verify and commit PASS-only artifact code**

Run: `uv run pytest tests/test_market_residual.py tests/test_coinflip_residual.py tests/test_store.py tests/test_cli.py -q`

Expected: all tests pass.

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all checks pass.

Run the guarded creator exactly once:

```bash
uv run pickem fit-coinflip-residual-artifact --sport cfb --from 2021 --to 2025 --deployment-season 2026 --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb --predictions data/experiments/cfb-coinflip-residual-2021-2025.jsonl --spec docs/superpowers/specs/2026-08-27-cfb-coinflip-residual-model-design.md --out data/models/cfb-coinflip-residual-v1.json
```

Expected: the command reports the selected alpha, 2026 cutoff, hashes, and artifact path; a second identical command refuses to overwrite the artifact.

```bash
git add src/pickem/edge/market_residual.py src/pickem/backtest/coinflip_residual.py src/pickem/store/db.py src/pickem/cli.py tests/test_market_residual.py tests/test_coinflip_residual.py tests/test_store.py tests/test_cli.py
git commit -m "feat: freeze CFB residual model artifact"
```

### Task 8: Route Passing CFB COINFLIPs Through the Artifact — PASS ONLY

**Files:**
- Modify: `src/pickem/edge/pipeline.py`
- Modify: `src/pickem/config.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_report.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ResidualArtifact` and `decide_residual` from Task 7.
- Produces: optional `cfb_coinflip_artifact` parameter on `decide_edges`; report-time strict artifact loading; unchanged callers when the artifact is absent.

- [ ] **Step 1: Write failing routing tests**

```python
def test_valid_artifact_decides_only_cfb_coinflips():
    [coinflip] = decide_edges([CFB_LEAGUE], CFB_MARKET, [CFB_GAME], HISTORY, cfb_coinflip_artifact=ARTIFACT)
    assert coinflip.tier is Tier.COINFLIP
    assert coinflip.side is Side.AWAY
    assert "market-residual model" in coinflip.rationale

    [strong] = decide_edges([CFB_LEAGUE], MOVED_MARKET, [CFB_GAME], HISTORY, cfb_coinflip_artifact=ARTIFACT)
    assert strong.tier is Tier.STRONG
    assert strong.side is Side.HOME
    assert "market-residual" not in strong.rationale


def test_no_market_and_exact_zero_still_use_elo():
    [no_market] = decide_edges([CFB_LEAGUE], [], [CFB_GAME], HISTORY, cfb_coinflip_artifact=ARTIFACT)
    [zero] = decide_edges([CFB_LEAGUE], CFB_MARKET, [CFB_GAME], HISTORY, cfb_coinflip_artifact=ZERO_ARTIFACT)
    assert "Elo rating projects" in no_market.rationale
    assert "Elo rating projects" in zero.rationale


def test_report_rejects_an_invalid_present_artifact(tmp_path):
    artifact = tmp_path / "bad.json"
    artifact.write_text("{}")
    result = runner.invoke(app, REPORT_COMMAND + ["--coinflip-model", str(artifact)])
    assert result.exit_code != 0
    assert "invalid CFB coinflip artifact" in result.output
```

- [ ] **Step 2: Run routing tests to verify RED**

Run: `uv run pytest tests/test_pipeline.py tests/test_report.py tests/test_cli.py -k 'artifact or residual or exact_zero' -v`

Expected: tests fail because the pipeline and report command do not accept an artifact.

- [ ] **Step 3: Implement the narrow optional seam**

Extend the existing function without changing default callers:

```python
def decide_edges(
    league_lines: Sequence[LeagueLine],
    market_lines: Sequence[MarketLine],
    games: Sequence[Game],
    history: Sequence[Game],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
    cfb_coinflip_artifact: ResidualArtifact | None = None,
) -> list[Edge]:
```

Inside `_apply_tiebreaks`, call `decide_residual` only when the edge is `COINFLIP`, the matching game is CFB, the edge has a submission consensus, and the artifact is present. Preserve `STRONG`, `LEAN`, `NO_MARKET`, NFL, absent-artifact, and exact-zero Elo behavior.

Add `DEFAULT_CFB_COINFLIP_MODEL = Path("data/models/cfb-coinflip-residual-v1.json")` to config. Add `--coinflip-model: Path | None` to `report`. For CFB, `None` checks the default path and retains Elo when that default file is absent; an explicitly supplied missing path is an operator error. Any present path requires strict `ResidualArtifact.model_validate_json`. The CLI passes the validated object into `decide_edges` and never fits.

- [ ] **Step 4: Run regression, full suite, and rendering checks**

Run: `uv run pytest tests/test_pipeline.py tests/test_report.py tests/test_cli.py tests/test_coinflip.py tests/test_coinflip_residual.py tests/test_market_residual.py -q`

Expected: all tests pass, including Candidate 1 and NFL defaults.

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: zero failures and all checks pass.

- [ ] **Step 5: Commit Task 8**

```bash
git add src/pickem/edge/pipeline.py src/pickem/config.py src/pickem/cli.py tests/test_pipeline.py tests/test_report.py tests/test_cli.py
git commit -m "feat: use residual model for CFB coinflips"
```

### Task 9: Final Operating Verification and Handoff — PASS ONLY

**Files:**
- Modify: `docs/HANDOFF.md`
- Create: `.superpowers/sdd/2026-08-27-cfb-coinflip-residual-model/final-report.md`

**Interfaces:**
- Consumes: the passing artifact and integrated report path from Tasks 7–8.
- Produces: verified immediate 2026 operating instructions without polling, syncing, grading, or refitting.

- [ ] **Step 1: Verify the artifact and code from a clean process**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
sha256sum data/models/cfb-coinflip-residual-v1.json data/experiments/cfb-coinflip-residual-2021-2025.jsonl
```

Expected: zero failures, clean lint/format, and stable hashes recorded in the final report.

- [ ] **Step 2: Prove the artifact contains no 2026 outcome and cannot refit**

Run a read-only artifact inspection that asserts `training_through == 2025`, the cutoff equals the stored first 2026 kickoff, and all hashes match. Run the CLI regression test that monkeypatches `fit_residual_artifact` to raise and proves `report` never calls it. Search `src/pickem/edge/pipeline.py` and `src/pickem/edge/market_residual.py` for `Ridge`, `_fit_residual_model`, or `fit_residual_artifact`; the search must return no match.

- [ ] **Step 3: Render the stored 2026 week-1 sheet without external mutation**

Run only:

```bash
uv run pickem report --sport cfb --season 2026 --week 1 --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb --coinflip-model data/models/cfb-coinflip-residual-v1.json --out /private/tmp/cfb-2026-week1-residual.md
```

Do not run `poll-odds`, `sync-results`, any archive command, or an evaluator. Verify `STRONG`/`LEAN` rationales remain divergence-based and every eligible `COINFLIP` rationale names the market-residual model unless its exact score is zero.

- [ ] **Step 4: Update the operating handoff and final report**

Record the artifact and prediction hashes, test counts, selected alpha, training cutoff, immediate deployment rule, exact-zero fallback, invalid-artifact failure behavior, and immutable-through-2026 rule. Record that the report render appends a prospective pick-audit batch and state its before/after pick counts.

- [ ] **Step 5: Commit final verification**

```bash
git add docs/HANDOFF.md
git add -f .superpowers/sdd/2026-08-27-cfb-coinflip-residual-model/final-report.md
git commit -m "docs: certify CFB residual coinflip model"
```

Run `git status --short --branch` and require a clean worktree before handoff.
