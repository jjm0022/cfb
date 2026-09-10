# CFB COINFLIP Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Note added 2026-09-10 — this plan is complete and frozen.** It is kept as a
> record of how the work was done, and was not rewritten. `docs/HANDOFF.md` was
> split on 2026-09-09, so the sections these tasks updated now live in
> `docs/invariants.md`, `docs/results.md`, `docs/system-map.md`,
> `docs/data-inventory.md` and `docs/deployment-history.md`. Instructions below
> that say "update `docs/HANDOFF.md`" record what was actually done at the time.
>
> The `COINFLIP` tiebreak also moved off Elo to the frozen-board favorite on
> 2026-09-09 (`src/pickem/edge/favorite.py`), so every "retain Elo" /
> "Elo remains production" statement below describes the rule in force when this
> plan ran, not current behaviour.

**Goal:** Acquire a resumable 2021–2025 CFB odds archive, evaluate a three-feature market model with strict walk-forward predictions, and use it for live CFB COINFLIP picks only if it clears the predeclared gate.

**Architecture:** Extend the pure snapshot planner with an opt-in maximum-age batching rule while preserving the NFL default. Generalize the archive runner around sport-aware keys and a transactional request ledger, then build a separate experiment module whose saved outer-fold predictions decide whether a small, validated JSON artifact may enter the pure edge pipeline.

**Tech Stack:** Python 3.12, Pydantic 2, DuckDB, Typer, scikit-learn (`>=1.7,<2`), NumPy (transitive through scikit-learn), pytest, uv, ruff

**Spec:** `docs/superpowers/specs/2026-08-25-cfb-coinflip-model-design.md`

## Global Constraints

- Optimize raw ATS pick accuracy only; do not add opponent-pick, contrarian, confidence-pool, profit, or bankroll logic.
- CFB is the first target. NFL and CFB may share code but never fitted coefficients.
- Existing STRONG and LEAN decisions remain unchanged.
- The candidate may decide only CFB COINFLIP games; NO_MARKET and exact `0.5` probability use Elo.
- Candidate 1 has exactly three inputs: `median_delta`, `mean_delta`, and `book_balance`.
- The L2 inverse-regularization grid is exactly `{0.1, 1.0, 10.0}`.
- Historical fitting and selection use 2021–2025 only; no 2026 result may enter any fit or ship decision.
- CFB archive batches must put every submission snapshot strictly before kickoff and between 15 and 90 minutes old.
- The historical Odds API operation remains dry-run-first, append-only, credit-ceiling guarded, and resumable.
- The approved full CFB plan is 1,018 requests / 10,180 credits. A changed total requires review before paid execution.
- Tests remain offline. Network access occurs only in the explicitly labeled paid-run task.
- A failed acceptance gate is a valid terminal result: commit the null report and retain Elo.

## File Structure

| File | Responsibility |
|---|---|
| `src/pickem/backtest/snapshots.py` | Pure exact/batched snapshot planning and deterministic request IDs |
| `src/pickem/store/schema.sql` | `archive_requests` ledger schema |
| `src/pickem/store/db.py` | Atomic line-plus-ledger commit and completed-request reads |
| `src/pickem/ingest/odds.py` | Free remaining-credit preflight |
| `src/pickem/backtest/archive.py` | Sport-aware planning, resume filtering, quota guards, execution |
| `src/pickem/backtest/coinflip.py` | Feature rows, fold selection, model fitting, predictions, paired metrics |
| `src/pickem/edge/market_tiebreak.py` | Validated artifact contract and pure live inference |
| `src/pickem/edge/artifacts/cfb_coinflip.json` | Created only after Candidate 1 passes |
| `src/pickem/edge/pipeline.py` | Tier/sport routing to divergence, accepted model, or Elo |
| `src/pickem/cli.py` | Generalized archive CLI, evaluation CLI, live artifact loading |
| `tests/test_snapshots.py` | Exact and 90-minute batching invariants |
| `tests/test_store.py` | Ledger transaction and resume reads |
| `tests/test_odds_adapter.py` | Quota-header parsing without network |
| `tests/test_archive.py` | Sport keys, pending cost, resume, interruption |
| `tests/test_cli.py` | Dry-run/paid flags and evaluation output |
| `tests/test_cfbd_adapter.py` | Kickoff offset preservation |
| `tests/test_coinflip.py` | Features, folds, leakage guards, metrics, gate |
| `tests/test_market_tiebreak.py` | Artifact validation and deterministic inference |
| `tests/test_pipeline.py` | Conditional CFB-only live routing |
| `tests/test_end_to_end.py` | Accepted-model CFB sheet path without network |
| `docs/research/2026-08-25-cfb-coinflip-result.md` | Committed result, pass or null |
| `docs/HANDOFF.md` | Actual credits, coverage, result, and 2026 operating rule |

---

### Task 1: Add max-age snapshot batching without changing the NFL default

**Files:**
- Modify: `src/pickem/backtest/snapshots.py`
- Modify: `tests/test_snapshots.py`

**Interfaces:**
- Produces: `plan_snapshots(games: Sequence[Game], max_submission_age: timedelta = timedelta(minutes=15)) -> list[SnapshotRequest]`
- Produces: `snapshot_request_id(sport: Sport, request: SnapshotRequest) -> str`
- Preserves: calling `plan_snapshots(games)` groups only identical kickoff instants.

- [ ] **Step 1: Write failing batching and compatibility tests**

Add CFB-aware construction and these tests to `tests/test_snapshots.py`:

```python
def cfb_game(kickoff: datetime, away: str, home: str) -> Game:
    return Game(
        game_id=make_game_id(Sport.CFB, 2024, 3, away, home),
        sport=Sport.CFB,
        season=2024,
        week=3,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
        home_score=24,
        away_score=17,
    )


def test_default_planner_keeps_distinct_nfl_slots_separate():
    games = [
        game(3, SUN_EARLY, "BUF", "MIA"),
        game(3, SUN_EARLY + timedelta(minutes=30), "NYJ", "NE"),
    ]
    submissions = [
        r for r in plan_snapshots(games) if r.kind is SnapshotKind.SUBMISSION
    ]
    assert len(submissions) == 2


def test_ninety_minute_mode_batches_a_seventy_five_minute_kickoff_span():
    games = [
        cfb_game(SUN_EARLY, "CLEM", "UGA"),
        cfb_game(SUN_EARLY + timedelta(minutes=75), "BAMA", "LSU"),
    ]
    submissions = [
        r
        for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90))
        if r.kind is SnapshotKind.SUBMISSION
    ]
    assert len(submissions) == 1
    assert submissions[0].slate == {g.game_id for g in games}


def test_ninety_minute_mode_splits_a_seventy_six_minute_kickoff_span():
    games = [
        cfb_game(SUN_EARLY, "CLEM", "UGA"),
        cfb_game(SUN_EARLY + timedelta(minutes=76), "BAMA", "LSU"),
    ]
    submissions = [
        r
        for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90))
        if r.kind is SnapshotKind.SUBMISSION
    ]
    assert len(submissions) == 2


def test_every_batched_game_has_exactly_one_submission_request_with_valid_age():
    games = [
        cfb_game(SUN_EARLY + timedelta(minutes=n), f"A{n}", f"H{n}")
        for n in (0, 30, 75, 76, 150)
    ]
    submissions = [
        r
        for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90))
        if r.kind is SnapshotKind.SUBMISSION
    ]
    kickoff = {g.game_id: g.kickoff_utc for g in games}
    assigned = [gid for request in submissions for gid in request.slate]
    assert sorted(assigned) == sorted(kickoff)
    for request in submissions:
        for gid in request.slate:
            assert timedelta(minutes=15) <= kickoff[gid] - request.at <= timedelta(minutes=90)


def test_max_submission_age_cannot_be_shorter_than_the_safety_lead():
    with pytest.raises(ValueError, match="15 minutes"):
        plan_snapshots(
            [cfb_game(SUN_EARLY, "CLEM", "UGA")],
            max_submission_age=timedelta(minutes=14),
        )
```

Also test that `snapshot_request_id` is stable across slate ordering and changes
when `sport`, `kind`, `at`, or a slate member changes.

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `uv run pytest tests/test_snapshots.py -v`

Expected: FAIL because `plan_snapshots` has no `max_submission_age` argument and
`snapshot_request_id` does not exist.

- [ ] **Step 3: Implement greedy batching and deterministic IDs**

In `snapshots.py`, validate the bound and replace the per-slot block with a
greedy grouping helper:

```python
import hashlib
import json

from pickem.models import Game, Sport


def _submission_batches(
    games: Sequence[Game], max_submission_age: timedelta
) -> list[tuple[datetime, list[Game]]]:
    if max_submission_age < _SUBMISSION_LEAD:
        raise ValueError("max submission age must be at least the 15 minutes safety lead")
    remaining = sorted(games, key=lambda game: (game.kickoff_utc, game.game_id))
    batches: list[tuple[datetime, list[Game]]] = []
    while remaining:
        first = remaining[0].kickoff_utc
        request_at = first - _SUBMISSION_LEAD
        latest = request_at + max_submission_age
        batch = [game for game in remaining if game.kickoff_utc <= latest]
        batches.append((request_at, batch))
        selected = {game.game_id for game in batch}
        remaining = [game for game in remaining if game.game_id not in selected]
    return batches


def snapshot_request_id(sport: Sport, request: SnapshotRequest) -> str:
    payload = {
        "sport": sport.value,
        "season": request.season,
        "week": request.week,
        "kind": request.kind.value,
        "at": request.at.astimezone(UTC).isoformat(),
        "slate": sorted(request.slate),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
```

Give `plan_snapshots` a default `timedelta(minutes=15)` and build submission
requests from `_submission_batches`. Do not alter frozen anchors or weekly
windows.

- [ ] **Step 4: Run snapshot tests**

Run: `uv run pytest tests/test_snapshots.py -v`

Expected: all snapshot tests PASS, including every pre-existing NFL assertion.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/snapshots.py tests/test_snapshots.py
git commit -m "feat: batch CFB archive snapshots by maximum age"
```

---

### Task 2: Add a transactional archive request ledger

**Files:**
- Modify: `src/pickem/store/schema.sql`
- Modify: `src/pickem/store/db.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Consumes: request IDs returned by `snapshot_request_id(sport, request)` from Task 1.
- Produces: `Store.completed_archive_request_ids(request_ids: Sequence[str]) -> set[str]`
- Produces: `Store.commit_archive_request(request_id: str, sport: Sport, request: SnapshotRequest, returned_at: datetime, lines: Sequence[MarketLine]) -> None`

- [ ] **Step 1: Write failing store tests**

Add tests that initialize a temporary store and use a one-game
`SnapshotRequest`:

```python
def test_archive_commit_writes_lines_and_completion_together(tmp_path):
    db = tmp_path / "ledger.duckdb"
    request = SnapshotRequest(
        kind=SnapshotKind.SUBMISSION,
        at=NOW,
        window=(NOW - timedelta(hours=1), NOW + timedelta(hours=6)),
        slate=frozenset({GID}),
        season=2024,
        week=3,
    )
    line = MarketLine(
        game_id=GID,
        source=SUBMISSION_SOURCE,
        book="pinnacle",
        spread_home=-3.0,
        captured_at=NOW - timedelta(minutes=5),
    )
    with Store(db) as store:
        store.init_schema()
        store.upsert_games([GAME])
        store.commit_archive_request("request-1", Sport.CFB, request, line.captured_at, [line])
        assert store.completed_archive_request_ids(["request-1", "missing"]) == {"request-1"}
        assert store.load_week(Sport.CFB, 2024, 3).market_lines == [line]


def test_archive_commit_is_idempotent(tmp_path):
    with Store(tmp_path / "ledger.duckdb") as store:
        store.init_schema()
        store.upsert_games([GAME])
        store.commit_archive_request("request-1", Sport.CFB, REQUEST, RETURNED, [LINE])
        store.commit_archive_request("request-1", Sport.CFB, REQUEST, RETURNED, [LINE])
        assert store.completed_archive_request_ids(["request-1"]) == {"request-1"}
        assert len(store.load_week(Sport.CFB, 2024, 3).market_lines) == 1


def test_archive_commit_rolls_back_lines_when_ledger_insert_fails(tmp_path, monkeypatch):
    with Store(tmp_path / "ledger.duckdb") as store:
        store.init_schema()
        store.upsert_games([GAME])
        monkeypatch.setattr(
            store,
            "_insert_archive_request",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger failed")),
        )
        with pytest.raises(RuntimeError, match="ledger failed"):
            store.commit_archive_request("request-1", Sport.CFB, REQUEST, RETURNED, [LINE])
        assert store.completed_archive_request_ids(["request-1"]) == set()
        assert store.load_week(Sport.CFB, 2024, 3).market_lines == []


def test_zero_line_response_is_still_completed(tmp_path):
    with Store(tmp_path / "ledger.duckdb") as store:
        store.init_schema()
        store.commit_archive_request("request-1", Sport.CFB, REQUEST, RETURNED, [])
        assert store.completed_archive_request_ids(["request-1"]) == {"request-1"}
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_store.py -k archive -v`

Expected: FAIL because the table and Store methods do not exist.

- [ ] **Step 3: Add the ledger schema**

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS archive_requests (
    request_id       VARCHAR PRIMARY KEY,
    sport            VARCHAR NOT NULL,
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    kind             VARCHAR NOT NULL,
    requested_at     TIMESTAMPTZ NOT NULL,
    returned_at      TIMESTAMPTZ NOT NULL,
    line_count       INTEGER NOT NULL,
    completed_at     TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 4: Implement completed reads and atomic commits**

Import `UTC`, `SnapshotRequest`, and add:

```python
def completed_archive_request_ids(self, request_ids: Sequence[str]) -> set[str]:
    if not request_ids:
        return set()
    rows = self._con.execute(
        "SELECT request_id FROM archive_requests WHERE request_id IN "
        + "(" + ",".join("?" for _ in request_ids) + ")",
        list(request_ids),
    ).fetchall()
    return {row[0] for row in rows}


def commit_archive_request(
    self,
    request_id: str,
    sport: Sport,
    request: SnapshotRequest,
    returned_at: datetime,
    lines: Sequence[MarketLine],
) -> None:
    self._con.execute("BEGIN TRANSACTION")
    try:
        self.append_market_lines(lines)
        self._con.execute(
            """
            INSERT OR IGNORE INTO archive_requests
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                request_id,
                sport.value,
                request.season,
                request.week,
                request.kind.value,
                request.at,
                returned_at,
                len(lines),
                datetime.now(tz=UTC),
            ],
        )
    except Exception:
        self._con.execute("ROLLBACK")
        raise
    else:
        self._con.execute("COMMIT")
```

Use a small private insert helper if needed so the rollback test can inject a
failure without mocking DuckDB internals.

- [ ] **Step 5: Run store tests**

Run: `uv run pytest tests/test_store.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/store/schema.sql src/pickem/store/db.py tests/test_store.py
git commit -m "feat: checkpoint completed archive requests"
```

---

### Task 3: Make archive execution sport-aware, resumable, and quota-preflighted

**Files:**
- Modify: `src/pickem/ingest/odds.py`
- Modify: `src/pickem/backtest/archive.py`
- Modify: `tests/test_odds_adapter.py`
- Modify: `tests/test_archive.py`

**Interfaces:**
- Produces: `OddsClient.remaining_credits() -> int`
- Produces: `sport_key(sport: Sport) -> str`
- Changes: `ArchiveBackfill.run(games, *, max_credits, execute, on_progress=None, max_submission_age=timedelta(minutes=15), expected_credits=None, min_credit_reserve=100, max_new_requests=None) -> ArchiveRunReport`.
- Changes: `ArchiveRunReport` reports `planned_snapshots`, `completed_snapshots`, `pending_snapshots`, `pending_credits`, `executed_snapshots`, and `remaining_credits`.

- [ ] **Step 1: Write quota-preflight tests**

In `tests/test_odds_adapter.py`, return a mocked `/sports` response with header
`x-requests-remaining: 10610` and assert `remaining_credits() == 10610`. Add
tests that missing or non-integer headers raise `OddsApiError`.

- [ ] **Step 2: Write archive behavior tests**

Extend `StubHistoricalClient` with `remaining_credits`, and add:

```python
def test_cfb_execution_uses_the_ncaaf_sport_key(store, cfb_games):
    client = StubHistoricalClient(remaining=20_000)
    ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
        cfb_games,
        max_credits=20_000,
        execute=True,
        max_submission_age=timedelta(minutes=90),
    )
    assert {call["sport_key"] for call in client.calls} == {CFB_KEY}


def test_resume_buys_only_pending_requests(store, games):
    first = StubHistoricalClient(remaining=20_000)
    ArchiveBackfill(store, TeamResolver.default(), lambda: first).run(
        games, max_credits=20_000, execute=True
    )

    def explode():
        raise AssertionError("completed requests were repurchased")

    report = ArchiveBackfill(store, TeamResolver.default(), explode).run(
        games, max_credits=20_000, execute=True
    )
    assert report.pending_snapshots == 0
    assert report.pending_credits == 0


def test_balance_must_cover_pending_cost_plus_reserve(store, games):
    client = StubHistoricalClient(remaining=129)
    with pytest.raises(InsufficientCredits):
        ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
            games, max_credits=20_000, execute=True, min_credit_reserve=100
        )


def test_expected_credit_mismatch_fails_before_client_creation(store, games):
    with pytest.raises(PlannedCostChanged):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            games, max_credits=20_000, execute=True, expected_credits=999
        )


def test_max_new_requests_executes_probe_then_resume_finishes(store, games):
    # First run executes one pending request; second run executes the rest.
```

- [ ] **Step 3: Run focused tests and verify failure**

Run: `uv run pytest tests/test_odds_adapter.py tests/test_archive.py -v`

Expected: FAIL on the new interfaces.

- [ ] **Step 4: Implement remaining-credit parsing**

Add to `OddsClient`:

```python
def remaining_credits(self) -> int:
    response = self._get("/sports", params={"apiKey": self._api_key})
    if response.status_code in (401, 429):
        raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
    if response.status_code >= 400:
        raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")
    raw = response.headers.get("x-requests-remaining")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise OddsApiError(f"unreadable x-requests-remaining header {raw!r}") from exc
```

- [ ] **Step 5: Implement sport routing, resume filtering, and guards**

In `archive.py`, map `Sport.NFL` to `NFL_KEY` and `Sport.CFB` to `CFB_KEY`,
reject mixed-sport game collections, pass `max_submission_age` to the planner,
and compute deterministic IDs. Read completed IDs before cost checks. The
credit ceiling and `expected_credits` compare against pending requests only;
the report also retains total planned/completed counts.

On execute, construct one client, call `remaining_credits()`, require
`remaining >= pending_credits + min_credit_reserve`, then execute at most
`max_new_requests`. Commit each response through
`Store.commit_archive_request(request_id, sport, request, returned_at, lines)`; derive `returned_at` from the newest line
timestamp, or use `request.at` for a valid zero-line response because the
client currently returns only parsed lines. In Task 4, change the adapter result
to expose the envelope timestamp so zero-line requests store the real returned
time.

Raise explicit `MixedSports`, `InsufficientCredits`, and `PlannedCostChanged`
exceptions with values available to the CLI.

- [ ] **Step 6: Run archive and adapter tests**

Run: `uv run pytest tests/test_odds_adapter.py tests/test_archive.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/ingest/odds.py src/pickem/backtest/archive.py tests/test_odds_adapter.py tests/test_archive.py
git commit -m "feat: resume sport-aware archive backfills"
```

---

### Task 4: Generalize the archive CLI and verify CFB kickoff contracts

**Files:**
- Modify: `src/pickem/models.py`
- Modify: `src/pickem/ingest/odds.py`
- Modify: `src/pickem/backtest/archive.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_cfbd_adapter.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Changes: `MarketLinesResult` gains `snapshot_at: datetime | None = None`.
- Changes: `pickem backfill-history --sport {nfl,cfb} --from YEAR --to YEAR --max-snapshot-age-minutes N --expected-credits N [--execute] [--max-new-requests N]`.
- Preserves: live odds results may leave `snapshot_at=None`; historical results always set it.

- [ ] **Step 1: Pin snapshot-envelope and CFBD-offset behavior with tests**

Add model and adapter tests:

```python
def test_historical_result_carries_the_archive_timestamp():
    result = historical(client_returning(ENVELOPE))
    assert result.snapshot_at == SNAPSHOT_TAKEN


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-09-20T23:30:00Z", datetime(2025, 9, 20, 23, 30, tzinfo=UTC)),
        ("2025-09-20T19:30:00-04:00", datetime(2025, 9, 20, 23, 30, tzinfo=UTC)),
        ("2025-09-21T01:30:00+02:00", datetime(2025, 9, 20, 23, 30, tzinfo=UTC)),
    ],
)
def test_cfb_kickoff_preserves_the_source_instant(raw, expected, resolver):
    row = {**GAMES[0], "start_date": raw}
    [game] = load_cfb_games(2025, 3, resolver=resolver, fetcher=lambda _s, _w: [row])
    assert game.kickoff_utc == expected
```

- [ ] **Step 2: Write CLI tests**

Add tests that:

- CFB dry run uses a 90-minute planner and never reads the API key;
- CFB `--execute` rejects a maximum age other than 90;
- paid execution requires `--expected-credits`;
- a mismatched expected cost exits 1 without a client;
- `--max-new-requests 1` reaches the runner;
- reports print planned, completed, pending, pending credits, and balance; and
- NFL defaults still produce the existing three-request fixture.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `uv run pytest tests/test_models.py tests/test_cfbd_adapter.py tests/test_cli.py -k 'snapshot or kickoff or backfill_history' -v`

Expected: FAIL on missing fields and CLI options.

- [ ] **Step 4: Carry the real archive timestamp through the adapter**

Add `snapshot_at` to `MarketLinesResult`. Set it to `captured_at` in
`fetch_historical_spreads`; leave live and CFBD constructors on the default.
Require `result.snapshot_at` in the archive runner and pass it to the atomic
ledger commit even when `result.lines` is empty.

- [ ] **Step 5: Generalize `backfill-history`**

Add required `--sport`, make `--max-snapshot-age-minutes` default to 15 for NFL
and 90 for CFB when omitted, and require exactly 90 for paid CFB execution.
Load `store.load_seasons(sport, start, end)`. Translate all new archive
exceptions into concise non-zero CLI output. Require `--expected-credits` when
`--execute` is present. Keep dry run from reading `ODDS_API_KEY`.

The summary format must include:

```text
1018 planned, 0 complete, 1018 pending = 10180 credits
dry run — pass --execute --expected-credits 10180 to purchase pending requests
```

- [ ] **Step 6: Run focused and regression tests**

Run: `uv run pytest tests/test_models.py tests/test_cfbd_adapter.py tests/test_cli.py tests/test_archive.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/models.py src/pickem/ingest/odds.py src/pickem/backtest/archive.py src/pickem/cli.py tests/test_models.py tests/test_cfbd_adapter.py tests/test_cli.py
git commit -m "feat: expose guarded CFB archive backfill"
```

---

### Task 5: Verify and acquire the CFB archive

**Files:**
- Create locally, gitignored: `data/backups/pickem-2026-08-25-pre-cfb.duckdb`
- Modify after the run: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: the dry-run and resumable paid CLI completed in Tasks 1–4.
- Produces: `oddsapi:frozen` and `oddsapi:submit` CFB rows for 2021–2025.

- [ ] **Step 1: Run all offline safety checks**

Run:

```bash
uv run pytest tests/test_snapshots.py tests/test_store.py tests/test_odds_adapter.py tests/test_archive.py tests/test_cli.py tests/test_cfbd_adapter.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: all PASS before any credit is spent.

- [ ] **Step 2: Back up the irreplaceable database and record hashes**

Run:

```bash
mkdir -p data/backups
cp data/pickem.duckdb data/backups/pickem-2026-08-25-pre-cfb.duckdb
shasum -a 256 data/pickem.duckdb data/backups/pickem-2026-08-25-pre-cfb.duckdb
```

Expected: two identical SHA-256 hashes. If they differ, stop before network use.

- [ ] **Step 3: Run the full no-cost plan**

Run:

```bash
uv run pickem backfill-history --sport cfb --from 2021 --to 2025 --max-snapshot-age-minutes 90
```

Expected exactly: 1,018 planned, 0 complete, 1,018 pending, 10,180 credits.
Any difference stops the paid task for review.

- [ ] **Step 4: Purchase one planned request**

Run the 2021 dry run first and copy its exact pending-credit count into:

```bash
uv run pickem backfill-history --sport cfb --from 2021 --to 2021 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2021 --to 2021 --max-snapshot-age-minutes 90 --execute --expected-credits 1990 --max-new-requests 1
```

Expected: the dry run prints 199 requests / 1,990 credits; the quota preflight
reports at least pending credits plus 100; one request completes and returns
matched lines. If it returns zero lines, has mismatched game IDs, or exposes a
kickoff outside the guarded week, stop.

- [ ] **Step 5: Verify the first ledger transaction**

Use DuckDB through the project environment:

```bash
uv run python -c "import duckdb; c=duckdb.connect('data/pickem.duckdb'); print(c.execute(\"select sport,season,week,kind,line_count,requested_at,returned_at from archive_requests order by completed_at limit 1\").fetchall())"
```

Expected: one CFB row with positive `line_count` and `returned_at <= requested_at`.

- [ ] **Step 6: Complete each season chronologically**

Run these commands one season at a time, performing Step 7 after each execute:

```bash
uv run pickem backfill-history --sport cfb --from 2021 --to 2021 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2021 --to 2021 --max-snapshot-age-minutes 90 --execute --expected-credits 1980
uv run pickem backfill-history --sport cfb --from 2022 --to 2022 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2022 --to 2022 --max-snapshot-age-minutes 90 --execute --expected-credits 2020
uv run pickem backfill-history --sport cfb --from 2023 --to 2023 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2023 --to 2023 --max-snapshot-age-minutes 90 --execute --expected-credits 1990
uv run pickem backfill-history --sport cfb --from 2024 --to 2024 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2024 --to 2024 --max-snapshot-age-minutes 90 --execute --expected-credits 2060
uv run pickem backfill-history --sport cfb --from 2025 --to 2025 --max-snapshot-age-minutes 90
uv run pickem backfill-history --sport cfb --from 2025 --to 2025 --max-snapshot-age-minutes 90 --execute --expected-credits 2120
```

The 2021 value is 1,980 because the 10-credit probe completed one of that
season's 199 requests. Before every execute, the immediately preceding dry run
must print the exact expected value shown above. If any value differs, do not
execute that command. After each season, rerun its dry run and require zero
pending before advancing.

- [ ] **Step 7: Verify integrity and coverage after every season**

Run a read-only DuckDB query that reports:

```sql
SELECT count(*) FROM lines l LEFT JOIN games g USING (game_id)
WHERE g.game_id IS NULL;

SELECT count(*) FROM lines l JOIN games g USING (game_id)
WHERE g.sport = 'cfb' AND l.source = 'oddsapi:submit'
  AND l.captured_at >= g.kickoff_utc;

SELECT g.season, l.source, count(*) rows, count(DISTINCT l.game_id) games,
       count(DISTINCT l.book) books
FROM lines l JOIN games g USING (game_id)
WHERE g.sport = 'cfb' AND g.season BETWEEN 2021 AND 2025
GROUP BY 1, 2 ORDER BY 1, 2;
```

Expected: zero orphans, zero at/after-kickoff submission rows, and nonzero
coverage for both proxy sources in every completed season.

- [ ] **Step 8: Record actual acquisition facts and commit**

Update `docs/HANDOFF.md` with the backup path/hash, actual credits, request
counts, game coverage, missing-game counts, books per game, timestamp-age
range, and zero-orphan/zero-in-play assertions.

```bash
git add docs/HANDOFF.md
git commit -m "docs: record CFB archive acquisition"
```

---

### Task 6: Build deterministic COINFLIP feature rows

**Files:**
- Create: `src/pickem/backtest/coinflip.py`
- Create: `tests/test_coinflip.py`

**Interfaces:**
- Produces: `CoinflipRow`, `CoinflipDataset`, and `build_coinflip_rows(games, frozen, submission) -> CoinflipDataset`.
- `CoinflipDataset.rows` contains valid rows; `.skipped` contains every excluded game and reason.

- [ ] **Step 1: Write failing feature tests**

Define fixtures with one completed CFB game, frozen spread `-3.0`, and latest
submission book spreads `[-3.5, -3.0, -2.5]`. Test:

```python
def test_market_distribution_becomes_the_three_declared_features():
    dataset = build_coinflip_rows([GAME], FROZEN, SUBMISSION)
    [row] = dataset.rows
    assert row.median_delta == 0.0
    assert row.mean_delta == 0.0
    assert row.book_balance == 0.0


def test_latest_snapshot_per_book_gets_one_vote():
    older = SUBMISSION[0].model_copy(
        update={"spread_home": -20.0, "captured_at": SUBMISSION[0].captured_at - timedelta(hours=1)}
    )
    baseline = build_coinflip_rows([GAME], FROZEN, SUBMISSION).rows[0]
    with_history = build_coinflip_rows([GAME], FROZEN, [older, *SUBMISSION]).rows[0]
    assert with_history == baseline


def test_non_coinflip_median_delta_is_excluded_with_reason():
    moved = [line.model_copy(update={"spread_home": -5.0}) for line in SUBMISSION]
    dataset = build_coinflip_rows([GAME], FROZEN, moved)
    assert dataset.rows == []
    assert GAME.game_id in dataset.skipped[0]
    assert "not coinflip" in dataset.skipped[0].lower()


def test_push_is_retained_but_has_no_binary_target():
    pushed = GAME.model_copy(update={"home_score": 20, "away_score": 17})
    [row] = build_coinflip_rows([pushed], FROZEN, SUBMISSION).rows
    assert row.is_push
    assert row.target_home_cover is None


def test_snapshot_at_or_after_kickoff_is_rejected():
    in_play = [line.model_copy(update={"captured_at": GAME.kickoff_utc}) for line in SUBMISSION]
    dataset = build_coinflip_rows([GAME], FROZEN, in_play)
    assert dataset.rows == []
    assert "kickoff" in dataset.skipped[0].lower()


def test_missing_frozen_or_submission_books_are_reported():
    no_frozen = build_coinflip_rows([GAME], [], SUBMISSION)
    no_submission = build_coinflip_rows([GAME], FROZEN, [])
    assert GAME.game_id in no_frozen.skipped[0]
    assert "frozen" in no_frozen.skipped[0].lower()
    assert GAME.game_id in no_submission.skipped[0]
    assert "submission" in no_submission.skipped[0].lower()
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_coinflip.py -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement Pydantic row contracts and feature construction**

Use these public shapes:

```python
class CoinflipRow(BaseModel):
    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    frozen_spread: float
    median_delta: float
    mean_delta: float
    book_balance: float
    target_home_cover: int | None
    is_push: bool


class CoinflipDataset(BaseModel):
    rows: list[CoinflipRow]
    skipped: list[str]
```

Collapse each proxy to the latest row per book before computing median/mean.
Use `consensus_spread` for the median so the experiment and shipped strategy
cannot disagree about tier membership. Sort output by `(season, week, game_id)`.
Reject non-finite values with a named skip.

- [ ] **Step 4: Run feature tests**

Run: `uv run pytest tests/test_coinflip.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/coinflip.py tests/test_coinflip.py
git commit -m "feat: build auditable coinflip model rows"
```

---

### Task 7: Add the walk-forward model evaluator and acceptance gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/pickem/backtest/coinflip.py`
- Modify: `tests/test_coinflip.py`

**Interfaces:**
- Produces: `evaluate_coinflip(rows, elo_sides, c_grid=(0.1, 1.0, 10.0)) -> CoinflipEvaluation`.
- Produces: `passes_acceptance_gate(evaluation: CoinflipEvaluation) -> bool`.
- Produces: deterministic `CoinflipPrediction` rows and fold summaries.

- [ ] **Step 1: Add the trusted modeling dependency**

Run: `uv add 'scikit-learn>=1.7,<2'`

Expected: `pyproject.toml` and `uv.lock` change; importing
`LogisticRegression` and `StandardScaler` succeeds under `uv run python`.

- [ ] **Step 2: Write fold/leakage tests before model code**

Create small synthetic rows for seasons 2021–2025 and assert:

```python
def test_outer_folds_never_train_on_the_test_or_future_season():
    result = evaluate_coinflip(ROWS, ELO_SIDES)
    assert [(f.train_through, f.test_season) for f in result.folds] == [
        (2021, 2022), (2022, 2023), (2023, 2024), (2024, 2025)
    ]


def test_selected_c_always_comes_from_the_fixed_grid():
    result = evaluate_coinflip(ROWS, ELO_SIDES)
    assert {fold.selected_c for fold in result.folds} <= {0.1, 1.0, 10.0}


def test_tied_inner_accuracy_selects_stronger_regularization():
    result = evaluate_coinflip(TIED_INNER_ROWS, TIED_INNER_ELO)
    assert all(fold.selected_c == 0.1 for fold in result.folds)


def test_scaler_means_are_learned_only_from_outer_training_rows():
    result = evaluate_coinflip(ROWS_WITH_EXTREME_2025, ELO_SIDES)
    fold = next(item for item in result.folds if item.test_season == 2025)
    training = [row for row in ROWS_WITH_EXTREME_2025 if row.season <= 2024 and not row.is_push]
    assert fold.means[0] == pytest.approx(
        sum(row.median_delta for row in training) / len(training)
    )


def test_pushes_are_reported_but_not_fitted_or_scored_as_decisions():
    result = evaluate_coinflip([*ROWS, PUSH_ROW], {**ELO_SIDES, PUSH_ROW.game_id: Side.HOME})
    assert result.pushes >= 1
    pushed = next(prediction for prediction in result.predictions if prediction.game_id == PUSH_ROW.game_id)
    assert pushed.is_push
    assert pushed.candidate_correct is None
    assert pushed.elo_correct is None


def test_predictions_are_paired_to_the_same_elo_game_ids():
    result = evaluate_coinflip(ROWS, ELO_SIDES)
    assert {prediction.game_id for prediction in result.predictions} == set(ELO_SIDES)


def test_repeated_evaluation_is_byte_identical():
    first = evaluate_coinflip(ROWS, ELO_SIDES).model_dump_json()
    second = evaluate_coinflip(ROWS, ELO_SIDES).model_dump_json()
    assert first == second
```

- [ ] **Step 3: Run new evaluator tests and verify failure**

Run: `uv run pytest tests/test_coinflip.py -k 'fold or grid or scaler or paired or identical' -v`

Expected: FAIL on missing evaluator types/functions.

- [ ] **Step 4: Implement nested time-ordered fitting**

Use this exact estimator constructor:

```python
Pipeline(
    [
        ("scale", StandardScaler()),
        (
            "model",
            LogisticRegression(
                penalty="l2",
                C=selected_c,
                solver="lbfgs",
                class_weight=None,
                max_iter=1000,
                random_state=0,
            ),
        ),
    ]
)
```

For the 2022 outer fold, inner-train on 2021 weeks 1–8 and
validate on weeks 9–15. For later outer folds, score each `C` on expanding
inner season predictions. Select highest total inner accuracy, then lowest `C`.

Store the scaler means/scales, coefficient/intercept, training range, selected
`C`, and predictions for every outer fold. Fail loudly if a training or
validation partition has fewer than both target classes.

- [ ] **Step 5: Write metric and gate tests**

Add exact hand-calculated tests for wins/losses/pushes, Brier score, log loss,
five calibration bins, per-season paired deltas, and a fixed-seed bootstrap.
Then assert:

```python
def test_gate_requires_one_percentage_point_three_seasons_and_brier_below_quarter():
    assert passes_acceptance_gate(passing_evaluation())
    assert not passes_acceptance_gate(with_delta(0.0099))
    assert not passes_acceptance_gate(with_positive_seasons(2))
    assert not passes_acceptance_gate(with_brier(0.25))
```

- [ ] **Step 6: Implement metrics and the exact gate**

Use a NumPy random generator seeded with `20260825`. Resample unique
`(season, week)` clusters 10,000 times and recompute candidate-minus-Elo
accuracy on the sampled games. Calibration bins are `[0,.2)`, `[.2,.4)`,
`[.4,.6)`, `[.6,.8)`, and `[.8,1]`.

- [ ] **Step 7: Run tests and quality checks**

Run:

```bash
uv run pytest tests/test_coinflip.py -v
uv run ruff check src/pickem/backtest/coinflip.py tests/test_coinflip.py
uv run ruff format --check src/pickem/backtest/coinflip.py tests/test_coinflip.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/pickem/backtest/coinflip.py tests/test_coinflip.py
git commit -m "feat: evaluate coinflip models walk-forward"
```

---

### Task 8: Expose and persist the one-shot CFB experiment

**Files:**
- Modify: `src/pickem/backtest/coinflip.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_coinflip.py`

**Interfaces:**
- Produces: `replay_elo_sides(games, frozen, submission) -> dict[str, Side]`, using the shipped pipeline and only prior-week history.
- Produces CLI: `pickem evaluate-coinflip --sport cfb --from 2021 --to 2025 --predictions PATH --report PATH`.

- [ ] **Step 1: Test the incumbent replay contract**

Add a fixture with two seasons and assert `replay_elo_sides`:

- returns only games classified `COINFLIP` by the shipped thresholds;
- calls the same `decide_edges` behavior as live reporting;
- never includes the test week's result in Elo history; and
- produces sides for exactly the feature-row game IDs.

- [ ] **Step 2: Write CLI tests**

Seed a temporary DuckDB with synthetic 2021–2025 CFB proxy rows. Invoke the
command twice and assert:

- predictions are newline-delimited JSON sorted by season/week/game ID;
- the Markdown report includes fold train/test ranges, chosen `C`, paired
  deltas, Brier/log loss, calibration counts, bootstrap interval, every gate
  condition, and final `PASS` or `NULL`;
- both runs are byte-identical; and
- `--sport nfl` exits nonzero because this approved experiment is CFB-only.

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/test_coinflip.py tests/test_cli.py -k 'elo_sides or evaluate_coinflip' -v`

Expected: FAIL because replay and command do not exist.

- [ ] **Step 4: Implement incumbent replay and deterministic serializers**

Replay weeks chronologically exactly as `run_backtest` does: build frozen
`LeagueLine` proxies, call `decide_edges` without a market model, save only
`COINFLIP` sides, then extend history after the week. Add JSONL and Markdown
renderers that end with a newline and never include wall-clock timestamps.

- [ ] **Step 5: Implement the CLI command**

Load CFB seasons 2021–2025, split stored proxies, build rows, replay Elo,
evaluate, and write both requested paths. Refuse any range other than the
approved one for the one-shot initial decision. Print all skipped coverage
reasons and a final `Candidate 1: PASS` or `Candidate 1: NULL — retain Elo`.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_coinflip.py tests/test_cli.py -v
git add src/pickem/backtest/coinflip.py src/pickem/cli.py tests/test_coinflip.py tests/test_cli.py
git commit -m "feat: run the CFB coinflip experiment"
```

---

### Task 9: Run Candidate 1 once and freeze the result

**Files:**
- Create locally, gitignored: `data/experiments/cfb-coinflip-2021-2025.jsonl`
- Create: `docs/research/2026-08-25-cfb-coinflip-result.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: verified archive rows and the frozen evaluator.
- Produces: the binding PASS/NULL decision for Candidate 1.

- [ ] **Step 1: Run the complete offline suite before the one-shot experiment**

Run: `uv run pytest -q`

Expected: all tests PASS.

- [ ] **Step 2: Run the experiment exactly once**

Run:

```bash
mkdir -p data/experiments
uv run pickem evaluate-coinflip --sport cfb --from 2021 --to 2025 --predictions data/experiments/cfb-coinflip-2021-2025.jsonl --report docs/research/2026-08-25-cfb-coinflip-result.md
```

Expected: the command completes with a visible PASS or NULL, plus all fold and
coverage metrics. Do not change features, grid, folds, or thresholds afterward.

- [ ] **Step 3: Prove determinism without overwriting the first result**

Run again to temporary paths, compare hashes/bytes to the originals, then move
the temporary files to trash after equality is confirmed. Expected: exact
equality for both predictions and report.

- [ ] **Step 4: Audit the generated report against the gate**

Manually verify the reported pooled percentage-point delta, four seasonal
deltas, Brier score, fold training ranges, and final decision match the spec.
Add the prediction-file SHA-256 to the Markdown report if the renderer does not
already include it, then update the renderer/test so a fresh run produces the
same field; do not hand-edit only the result.

- [ ] **Step 5: Update the handoff and commit the immutable result**

Record PASS or NULL, coverage, and the rule that no 2026 result may cause a
midseason refit.

```bash
git add docs/research/2026-08-25-cfb-coinflip-result.md docs/HANDOFF.md
git commit -m "docs: freeze CFB coinflip experiment result"
```

- [ ] **Step 6: Apply the decision gate**

If the report says NULL, stop this plan here: Elo remains live, Tasks 10–11 are
not authorized by the spec, and Task 12 still runs final verification. If the
report says PASS, continue to Task 10 without changing the experiment.

---

### Task 10: Create validated model inference and the prospective artifact (PASS only)

**Files:**
- Create: `src/pickem/edge/market_tiebreak.py`
- Create only on PASS: `src/pickem/edge/artifacts/cfb_coinflip.json`
- Create: `tests/test_market_tiebreak.py`
- Modify: `src/pickem/backtest/coinflip.py`
- Modify: `tests/test_coinflip.py`

**Interfaces:**
- Produces: `MarketTiebreakArtifact` Pydantic contract.
- Produces: `market_probability(artifact, league_spread, market_lines) -> float`.
- Produces: `fit_prospective_artifact(rows) -> MarketTiebreakArtifact` using all eligible 2021–2025 rows and the same inner selection rule.

- [ ] **Step 1: Write artifact validation and inference tests**

Use this contract:

```python
class MarketTiebreakArtifact(BaseModel):
    format_version: Literal[1]
    sport: Literal["cfb"]
    trained_from: Literal[2021]
    trained_through: Literal[2025]
    feature_names: tuple[Literal["median_delta"], Literal["mean_delta"], Literal["book_balance"]]
    means: tuple[float, float, float]
    scales: tuple[float, float, float]
    intercept: float
    coefficients: tuple[float, float, float]
    selected_c: Literal[0.1, 1.0, 10.0]
    decision_threshold: Literal[0.5]
```

Test rejection of wrong sport/version/order, zero scale, non-finite numbers,
and missing fields. Hand-calculate standardized features, logit, and sigmoid
for a known artifact and require probability within `1e-12`. Test latest-book
collapse matches the experiment feature builder.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_market_tiebreak.py -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement pure artifact inference**

Do not import scikit-learn in `edge/`. Validate with Pydantic and calculate:

```python
standardized = [
    (value - mean) / scale
    for value, mean, scale in zip(values, artifact.means, artifact.scales, strict=True)
]
z = sum(
    coefficient * value
    for coefficient, value in zip(artifact.coefficients, standardized, strict=True)
)
logit = artifact.intercept + z
probability = 1.0 / (1.0 + math.exp(-logit))
```

Use a numerically stable sigmoid branch for large positive/negative logits.
Reuse a pure latest-per-book feature helper shared with `backtest/coinflip.py`
so training and inference cannot drift.

- [ ] **Step 4: Implement and test the final prospective fit**

Fit all non-push 2021–2025 rows. Select `C` through expanding-season inner
predictions with the same tie-break, then refit scaler and logistic regression
on the complete set. Serialize floats and feature order deterministically.
Test that the saved coefficients reproduce scikit-learn probabilities on the
same rows within `1e-12`.

- [ ] **Step 5: Generate and validate the artifact**

Run the artifact-generation entry point against the frozen experiment data,
write `src/pickem/edge/artifacts/cfb_coinflip.json`, load it back through
`MarketTiebreakArtifact`, and compare inference to the fitted pipeline.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/edge/market_tiebreak.py src/pickem/edge/artifacts/cfb_coinflip.json src/pickem/backtest/coinflip.py tests/test_market_tiebreak.py tests/test_coinflip.py
git commit -m "feat: freeze the accepted CFB coinflip model"
```

---

### Task 11: Route accepted CFB COINFLIP picks through the artifact (PASS only)

**Files:**
- Modify: `src/pickem/edge/pipeline.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Changes: `decide_edges(league_lines, market_lines, games, history, thresholds=None, elo_config=None, market_model=None) -> list[Edge]`.
- Preserves: callers passing no artifact retain all current behavior.
- Live CLI loads the packaged artifact for `Sport.CFB` only.

- [ ] **Step 1: Write routing tests before changing the pipeline**

Add tests proving:

```python
def test_accepted_model_decides_only_cfb_coinflip():
    [edge] = decide_edges(
        [CFB_LEAGUE], CFB_MARKET, [CFB_GAME], HISTORY, market_model=HOME_ARTIFACT
    )
    assert edge.tier is Tier.COINFLIP
    assert edge.side is Side.HOME
    assert "cover probability" in edge.rationale


def test_model_never_overrides_strong_or_lean():
    [edge] = decide_edges(
        [CFB_LEAGUE.model_copy(update={"spread_home": -3.0})],
        [line.model_copy(update={"spread_home": -6.0}) for line in CFB_MARKET],
        [CFB_GAME],
        HISTORY,
        market_model=AWAY_ARTIFACT,
    )
    assert edge.tier is Tier.STRONG
    assert edge.side is Side.HOME
    assert "cover probability" not in edge.rationale


def test_no_market_still_uses_elo():
    [edge] = decide_edges([CFB_LEAGUE], [], [CFB_GAME], HISTORY, market_model=HOME_ARTIFACT)
    assert edge.tier is Tier.NO_MARKET
    assert "rating projects" in edge.rationale


def test_exact_half_probability_falls_back_to_elo():
    [edge] = decide_edges(
        [CFB_LEAGUE], CFB_MARKET, [CFB_GAME], HISTORY, market_model=HALF_ARTIFACT
    )
    assert "rating projects" in edge.rationale


def test_nfl_ignores_a_cfb_artifact():
    [edge] = decide_edges(
        [NFL_LEAGUE], NFL_MARKET, [NFL_GAME], HISTORY, market_model=HOME_ARTIFACT
    )
    assert "rating projects" in edge.rationale


def test_no_artifact_is_byte_equivalent_to_current_pipeline():
    args = ([CFB_LEAGUE], CFB_MARKET, [CFB_GAME], HISTORY)
    assert decide_edges(*args) == decide_edges(*args, market_model=None)
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_pipeline.py -k market_model -v`

Expected: FAIL because the argument and routing do not exist.

- [ ] **Step 3: Route only eligible edges**

Pass the per-game market rows into `_apply_tiebreaks`. For a `COINFLIP` edge,
look up its `Game`; when it is CFB and an artifact exists, calculate
probability. Use home above `0.5`, away below, and Elo exactly at `0.5`. Keep
the current missing-game failure. Rationale format:

```text
league -3.0 vs market -3.0: 0.0 pts toward away; CFB market tiebreak gives home 53.2% cover probability
```

Do not modify `Edge` fields or tier names.

- [ ] **Step 4: Load the artifact only at the CLI I/O boundary**

Add a helper using `importlib.resources` to read the packaged JSON for CFB.
`report` passes it to `decide_edges` only for `sport is Sport.CFB`. A malformed
or missing artifact after a PASS is a visible fatal error; never silently fall
back because that would ship a method different from the approved one.

- [ ] **Step 5: Add a CFB end-to-end fixture**

Extend `tests/test_end_to_end.py` with a CFB CBS paste and multi-book live odds
whose median delta is under one but book balance makes the accepted fixture
artifact choose a known side. Assert the sheet names `coinflip`, the model
probability, and the chosen side. Also assert NFL output remains unchanged.

- [ ] **Step 6: Run focused and full tests**

```bash
uv run pytest tests/test_market_tiebreak.py tests/test_pipeline.py tests/test_report.py tests/test_end_to_end.py -v
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/edge/pipeline.py src/pickem/cli.py tests/test_pipeline.py tests/test_end_to_end.py tests/test_report.py
git commit -m "feat: use accepted market model for CFB coinflips"
```

---

### Task 12: Final verification and operating handoff

**Files:**
- Modify: `docs/HANDOFF.md`
- Modify if user-facing commands changed: `README.md`

**Interfaces:**
- Produces: a verified, documented CFB weekly operating path before 2026 results are graded.

- [ ] **Step 1: Run complete verification from a clean process**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run pickem --help
uv run pickem backfill-history --sport cfb --from 2021 --to 2025 --max-snapshot-age-minutes 90
```

Expected: tests/lint/format PASS; help lists the generalized archive and
evaluation commands; the archive dry run reports all 1,018 requests complete
and zero pending credits.

- [ ] **Step 2: Verify stored-data invariants one final time**

Report and record:

- zero orphaned CFB lines;
- zero CFB submission rows captured at/after kickoff;
- every completed archive request has a ledger row;
- actual frozen/submission game coverage by season;
- minimum/median/maximum books per modeled game;
- minimum/median/maximum submission snapshot age; and
- no 2026 scored game in any training/prediction artifact.

- [ ] **Step 3: Verify the live 2026 sheet without grading outcomes**

Run `report` against the already stored 2026 CFB week after a fresh `poll-odds`.
Do not sync or use results. Confirm every game has a side, provenance, snapshot
age, and a rationale naming divergence, Elo, or the accepted market tiebreak.

- [ ] **Step 4: Update documentation**

In `docs/HANDOFF.md`, record actual commands, data counts, credits remaining,
PASS/NULL result, the artifact hash if present, and the rule against 2026
midseason refitting. Update `README.md` only where CLI usage has changed.

- [ ] **Step 5: Review the final diff and commit**

Run: `git diff --check` and `git status --short`.

Then:

```bash
git add docs/HANDOFF.md README.md
git commit -m "docs: hand off the CFB coinflip workflow"
```

If `README.md` did not change, omit it from `git add`.

- [ ] **Step 6: Record terminal state**

- PASS path: the accepted artifact is live only for CFB COINFLIP picks, and
  prospective 2026 predictions are the next evidence.
- NULL path: Elo remains live, the null result is committed, and no model code
  is loaded by reporting.

In either path, do not begin Candidate 2 without a new design approval.
