"""Command-line surface. Wiring only — no strategy logic lives here."""
# ruff: noqa: B008

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from pickem import config
from pickem.backtest.archive import (
    ArchiveBackfill,
    ArchiveProgress,
    ArchiveRunInterrupted,
    CreditLimitExceeded,
    InsufficientCredits,
    MixedSports,
    NoHistoricalGames,
    PlannedCostChanged,
)
from pickem.backtest.calibration import DEFAULT_TOLERANCE, calibrate
from pickem.backtest.coinflip import (
    build_coinflip_rows,
    evaluate_coinflip,
    evaluations_are_byte_identical,
    passes_acceptance_gate,
    render_coinflip_report,
    render_predictions_jsonl,
    replay_elo_sides,
)
from pickem.backtest.runner import run_backtest, split_proxies
from pickem.edge.divergence import rank_edges
from pickem.edge.pipeline import MissingGameError, decide_edges
from pickem.ingest.cbs import CbsParseError, parse_cbs_block
from pickem.ingest.cbs_html import parse_cbs_html
from pickem.ingest.cfbd_source import CfbdConfig, default_games_fetcher, load_cfb_games
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsApiError, OddsClient, QuotaExhausted
from pickem.models import Game, Sport
from pickem.report.sheet import render_sheet
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store

app = typer.Typer(help="Rank pick'em selections by frozen-line vs market divergence.")


def _store(db: Path) -> Store:
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db)
    store.init_schema()
    return store


def _warn_skipped(header: str, skipped: list[str]) -> None:
    """Nothing a source could not give us is dropped in silence."""
    if not skipped:
        return
    typer.secho(f"{header} ({len(skipped)}):", fg="yellow")
    for row in skipped:
        typer.secho(f"  skipped: {row}", fg="yellow")


def _archive_progress(progress: ArchiveProgress) -> None:
    request = progress.request
    typer.echo(
        f"[{progress.index}/{progress.total}] {request.season} "
        f"wk{request.week:02d} {request.kind.value}: {progress.line_count} lines"
    )
    _warn_skipped(f"snapshot {progress.index} rows not stored", progress.skipped)


@app.command("ingest-cbs")
def ingest_cbs(
    file: Path = typer.Option(None, help="File containing the pasted block; omit to read stdin"),
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    html: bool = typer.Option(False, "--html", help="Read a saved CBS page instead of pasted text"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Parse the CBS pick sheet into frozen league lines."""
    text = file.read_text() if file else sys.stdin.read()
    resolver = TeamResolver.default()
    now = datetime.now(tz=UTC)
    parser = parse_cbs_html if html else parse_cbs_block

    try:
        parsed = parser(
            text, resolver=resolver, sport=sport, season=season, week=week, posted_at=now
        )
    except UnknownTeamError as exc:
        typer.secho(f"unresolved team: {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc
    except CbsParseError as exc:
        typer.secho(f"nothing parsed from the paste: {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"parsed {len(parsed.games)} games for {sport.value} {season} week {week}")
    for skipped in parsed.skipped:
        typer.secho(f"  skipped: {skipped!r}", fg="yellow")
    if parsed.skipped:
        raise typer.Exit(code=1)

    store = _store(db)
    with store:
        # Insert-only: the paste carries no scores, so it must never overwrite
        # what sync-results already established.
        store.insert_games_if_absent([parsed_game.game for parsed_game in parsed.games])
        store.upsert_league_lines([parsed_game.league_line for parsed_game in parsed.games])
        typer.echo(f"ingested {len(parsed.games)} games for {sport.value} {season} week {week}")


@app.command("poll-odds")
def poll_odds(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
    days: int = typer.Option(7, help="Kickoff window ahead of now that this week occupies"),
) -> None:
    """Append a market snapshot for the active week."""
    key = NFL_KEY if sport is Sport.NFL else CFB_KEY
    if not db.exists():
        typer.secho(f"no database at {db}; run ingest-cbs first", fg="red", err=True)
        raise typer.Exit(code=1)
    with _store(db) as store:
        # The feed returns events across several weeks. Only the games already
        # ingested for this week may be stored, or the append-only lines table
        # takes on rows stamped with the wrong week forever.
        dataset = store.load_week(sport, season, week)
        slate = [line.game_id for line in dataset.league_lines]
        if not slate:
            typer.secho(
                f"no {sport.value} {season} week {week} games in the store; run ingest-cbs first",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=1)

        now = datetime.now(tz=UTC)
        try:
            with OddsClient(config.odds_api_key()) as client:
                result = client.fetch_spreads(
                    key,
                    resolver=TeamResolver.default(),
                    sport=sport,
                    season=season,
                    week=week,
                    now=now,
                    slate=slate,
                    window=(now - timedelta(hours=12), now + timedelta(days=days)),
                )
        except QuotaExhausted as exc:
            typer.secho(
                f"odds quota exhausted: {exc}; reports will use cached snapshots",
                fg="yellow",
                err=True,
            )
            raise typer.Exit(code=2) from exc
        except OddsApiError as exc:
            typer.secho(f"odds feed unavailable: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        except UnknownTeamError as exc:
            # Still fail loud — an in-window team we cannot name is a real gap —
            # but name it instead of raising a traceback at the user.
            typer.secho(f"unresolved team in the kickoff window: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc

        store.append_market_lines(result.lines)
        typer.echo(f"appended {len(result.lines)} market lines")
        _warn_skipped("market rows not stored", result.skipped)


@app.command("report")
def report(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
    out: Path = typer.Option(None, help="Also write the sheet to this markdown file"),
) -> None:
    """Render the ranked pick sheet."""
    with _store(db) as store:
        now = datetime.now(tz=UTC)
        dataset = store.load_week(sport, season, week)
        league_lines = dataset.league_lines
        games = dataset.games

        # Games the market never repriced fall through to the rating, built
        # from every completed game already stored — prior seasons included,
        # without which every early-season rating is still the initial value.
        market = dataset.market_lines
        newest = max((line.captured_at for line in market), default=None)
        history = store.games_before(sport, season, week)
        untrained = not any(
            game.home_score is not None and game.away_score is not None for game in history
        )
        try:
            edges = decide_edges(league_lines, market, games, history)
        except MissingGameError as exc:
            typer.secho(f"cannot resolve a tiebreak: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc

        # The caveat belongs in the artifact, not only in the terminal — a
        # written sheet has to carry the standing of its own numbers.
        provenance = "CBS frozen league lines vs latest stored market consensus"
        if untrained:
            provenance += (
                "; NO completed games stored, so every tiebreak rating is still "
                "the initial value and reduces to home field"
            )

        age = (now - newest).total_seconds() / 60 if newest else None
        sheet = render_sheet(
            edges,
            games,
            generated_at=now,
            provenance=provenance,
            snapshot_age_minutes=age,
        )
        typer.echo(sheet)
        if untrained:
            typer.secho(
                "warning: no completed games in the store, so every tiebreak "
                "rating is still the initial value and reduces to home field",
                fg="yellow",
                err=True,
            )
        if out:
            out.write_text(sheet)
        # Recorded so the live picks can be graded against the backtest later.
        store.record_picks(rank_edges(edges), season, week, now)


@app.command("sync-results")
def sync_results(
    season: int = typer.Option(...),
    sport: Sport = typer.Option(Sport.NFL),
    week: int = typer.Option(None, help="Required for CFB, which is fetched a week at a time"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Pull final scores into the store."""
    resolver = TeamResolver.default()
    with _store(db) as store:
        if sport is Sport.NFL:
            games = load_nfl_games([season], resolver=resolver)
        else:
            if week is None:
                typer.secho("--week is required for CFB results", fg="red", err=True)
                raise typer.Exit(code=1)
            cfbd_config = CfbdConfig.from_env()
            games = load_cfb_games(
                season,
                week,
                resolver=resolver,
                fetcher=default_games_fetcher(cfbd_config),
            )
        store.upsert_games(games)
        typer.echo(f"synced {len(games)} {sport.value} games for {season}")


@app.command("backfill")
def backfill(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """One-time historical load for the backtest."""
    resolver = TeamResolver.default()
    seasons = list(range(start, end + 1))
    with _store(db) as store:
        games = load_nfl_games(seasons, resolver=resolver)
        closers = load_nfl_closing_lines(seasons, resolver=resolver)
        store.upsert_games(games)
        store.append_market_lines(closers.lines)
        typer.echo(f"backfilled {len(games)} games and {len(closers.lines)} closing lines")
        _warn_skipped("rows with no spread", closers.skipped)
        typer.secho(
            "openers are still missing; run the Odds API historical backfill to complete the pair",
            fg="yellow",
        )


@app.command("backfill-cfb")
def backfill_cfb(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    weeks: int = typer.Option(15, help="Regular-season weeks to sweep per season"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Load historical CFB results, which is what trains the Elo tiebreak.

    Scores only — this touches no paid quota. CFBD is free, and the odds
    archive backfill is a separate, paid decision.
    """
    resolver = TeamResolver.default()
    fetcher = default_games_fetcher(CfbdConfig.from_env())

    total = 0
    with_scores = 0
    with _store(db) as store:
        for season in range(start, end + 1):
            season_games: list[Game] = []
            for week in range(1, weeks + 1):
                # An unknown school RAISES here, as everywhere CFBD is read: this
                # is a curated FBS-vs-FBS feed, not the odds firehose, so a name
                # we cannot place means the alias table is wrong.
                season_games.extend(
                    load_cfb_games(season, week, resolver=resolver, fetcher=fetcher)
                )
            store.upsert_games(season_games)
            scored = sum(1 for g in season_games if g.home_score is not None)
            total += len(season_games)
            with_scores += scored
            typer.echo(f"  {season}: {len(season_games)} games, {scored} with final scores")

    # Counted separately: a cancelled game is still a real fixture and is
    # stored, but it contributes nothing to the rating.
    typer.echo(f"loaded {total} games, {with_scores} with final scores")
    if total and not with_scores:
        typer.secho(
            "no final scores loaded, so the tiebreak rating is still untrained",
            fg="yellow",
        )


@app.command("backfill-history")
def backfill_history(
    sport: Sport = typer.Option(...),
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    execute: bool = typer.Option(False, "--execute", help="Actually spend credits and write rows"),
    max_snapshot_age_minutes: int | None = typer.Option(
        None,
        "--max-snapshot-age-minutes",
        help="Maximum age of a shared pre-kickoff snapshot (15 NFL, 90 CFB by default)",
    ),
    expected_credits: int | None = typer.Option(
        None,
        "--expected-credits",
        help="Required with --execute; must equal the pending archive cost",
    ),
    max_new_requests: int | None = typer.Option(
        None,
        "--max-new-requests",
        help="Buy at most this many pending snapshots (use 1 to probe safely)",
    ),
    max_credits: int = typer.Option(
        20_000, help="Refuse to start if the planned run costs more than this"
    ),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Backfill both backtest proxies from the Odds API archive.

    A dry run by default. Spending is opt-in because neither half of a mistake
    can be taken back: the `lines` table is append-only, so wrong game ids stay
    forever, and credits are not refundable.
    """
    snapshot_age_minutes = max_snapshot_age_minutes
    if snapshot_age_minutes is None:
        snapshot_age_minutes = 90 if sport is Sport.CFB else 15
    if execute and sport is Sport.CFB and snapshot_age_minutes != 90:
        typer.secho(
            "CFB paid archive backfills require --max-snapshot-age-minutes 90",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1)
    if execute and expected_credits is None:
        typer.secho("--execute requires --expected-credits N", fg="red", err=True)
        raise typer.Exit(code=1)

    with _store(db) as store:
        dataset = store.load_seasons(sport, start, end)

        def client_factory() -> OddsClient:
            return OddsClient(config.odds_api_key())

        archive = ArchiveBackfill(store, TeamResolver.default(), client_factory)
        try:
            report = archive.run(
                dataset.games,
                max_credits=max_credits,
                execute=execute,
                on_progress=_archive_progress,
                max_submission_age=timedelta(minutes=snapshot_age_minutes),
                expected_credits=expected_credits,
                max_new_requests=max_new_requests,
            )
        except NoHistoricalGames as exc:
            typer.secho(f"{exc}; load {sport.value} games first", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        except (CreditLimitExceeded, InsufficientCredits, MixedSports, PlannedCostChanged) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=1) from exc
        except ArchiveRunInterrupted as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=1) from exc
        except (OddsApiError, ValueError) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"{report.planned_snapshots} planned, {report.completed_snapshots} complete, "
            f"{report.pending_snapshots} pending = {report.pending_credits} credits"
        )
        if execute and report.remaining_credits is not None:
            typer.echo(f"balance: {report.remaining_credits} credits")
        if not execute:
            typer.secho(
                f"dry run — pass --execute --expected-credits {report.pending_credits} "
                "to purchase pending requests",
                fg="yellow",
            )


@app.command("backtest")
def backtest(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Replay history through the live edge code."""
    with _store(db) as store:
        dataset = store.load_seasons(Sport.NFL, start, end)
        games = dataset.games
        frozen, submission, unclassified = split_proxies(dataset.market_lines)
        result = run_backtest(games, frozen, submission)
        typer.echo(
            f"overall: {result.overall.wins}-{result.overall.losses}-{result.overall.pushes} "
            f"({result.overall.hit_rate:.1%}, 95% CI "
            f"{result.overall.ci_low:.1%}–{result.overall.ci_high:.1%})"
        )
        for record in result.by_tier:
            typer.echo(
                f"  {record.tier.value:<10} {record.wins}-{record.losses}-{record.pushes} "
                f"({record.hit_rate:.1%}, CI {record.ci_low:.1%}–{record.ci_high:.1%})"
            )
        typer.echo("\nAssumptions:")
        for assumption in result.assumptions:
            typer.echo(f"  - {assumption}")
        # A backtest that hides its exclusions reports 0-0-0 without saying why.
        _warn_skipped("games not graded", result.skipped)
        _warn_skipped("stored lines that are neither proxy", unclassified)


@app.command("calibrate")
def calibrate_cmd(
    sport: Sport = typer.Option(Sport.NFL),
    season: int = typer.Option(...),
    from_week: int = typer.Option(1, "--from-week"),
    to_week: int = typer.Option(22, "--to-week"),
    source: str = typer.Option(None, help="Restrict the market end to one source label"),
    tolerance_minutes: int = typer.Option(
        int(DEFAULT_TOLERANCE.total_seconds() // 60),
        "--tolerance-minutes",
        help="How long after the paste a market snapshot may still be compared",
    ),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Measure how closely the frozen CBS line tracks the market behind it.

    The backtest substitutes an early-week market snapshot for the CBS number.
    This is the check on that substitution.
    """
    with _store(db) as store:
        dataset = store.load_weeks(sport, season, from_week, to_week)
        result = calibrate(
            dataset.league_lines,
            dataset.market_lines,
            source=source,
            tolerance=timedelta(minutes=tolerance_minutes),
        )

    if result.compared == 0:
        # A zero here is the expected state until a real paste is ingested, and
        # saying so beats printing a bias of 0.0 that reads as perfect agreement.
        typer.secho(
            "no CBS line could be compared — ingest a paste with `ingest-cbs`, then "
            "run `poll-odds` in the same sitting",
            fg="yellow",
        )
        _warn_skipped("league lines not calibrated", result.skipped)
        raise typer.Exit(0)

    noun = "line" if result.compared == 1 else "lines"
    typer.echo(f"calibrated {result.compared} CBS {noun} against the market consensus behind them")
    typer.echo(f"  bias (mean residual):         {result.mean_residual:+.2f} pts")
    typer.echo(f"  dispersion (mean |residual|):  {result.mean_abs_residual:.2f} pts")
    typer.echo(
        f"  agreement: {result.share_exact:.1%} exact, "
        f"{result.share_within_half:.1%} within 0.5, "
        f"{result.share_within_one:.1%} within 1.0"
    )
    typer.echo("\n  positive bias = the league line sits above the market, which tilts")
    typer.echo("  picks toward the home side.")

    worst = sorted(result.residuals, key=lambda r: abs(r.residual), reverse=True)[:5]
    if worst:
        typer.echo("\nLargest residuals:")
        for r in worst:
            typer.echo(
                f"  {r.game_id}: CBS {r.league_spread:+.1f} vs market "
                f"{r.market_spread:+.1f} ({r.residual:+.1f})"
            )

    typer.echo("\nAssumptions:")
    for assumption in result.assumptions:
        typer.echo(f"  - {assumption}")
    _warn_skipped("league lines not calibrated", result.skipped)


@app.command("evaluate-coinflip")
def evaluate_coinflip_cmd(
    sport: Sport = typer.Option(...),
    start: int = typer.Option(2021, "--from"),
    end: int = typer.Option(2025, "--to"),
    predictions: Path = typer.Option(..., help="Write deterministic prediction JSONL here"),
    report: Path = typer.Option(..., help="Write the deterministic Markdown report here"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Run the fixed 2021–2025 CFB Candidate 1 experiment once."""
    if sport is not Sport.CFB:
        typer.secho("evaluate-coinflip is an approved CFB-only experiment", fg="red", err=True)
        raise typer.Exit(code=1)
    if (start, end) != (2021, 2025):
        typer.secho(
            "evaluate-coinflip only permits the approved --from 2021 --to 2025 range",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1)
    with _store(db) as store:
        stored = store.load_seasons(Sport.CFB, start, end)
    frozen, submission, unclassified = split_proxies(stored.market_lines)
    dataset = build_coinflip_rows(stored.games, frozen, submission)
    replayed_sides = replay_elo_sides(stored.games, frozen, submission)
    first_season = min(row.season for row in dataset.rows)
    elo_sides = {
        game_id: side
        for game_id, side in replayed_sides.items()
        if next(row.season for row in dataset.rows if row.game_id == game_id) != first_season
    }
    evaluation = evaluate_coinflip(dataset.rows, elo_sides)
    repeated_evaluation = evaluate_coinflip(dataset.rows, elo_sides)
    if not evaluations_are_byte_identical(evaluation, repeated_evaluation):
        raise RuntimeError("coinflip evaluation is not deterministic across independent runs")
    evaluation = evaluation.model_copy(update={"deterministic": True})

    predictions.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    prediction_bytes = render_predictions_jsonl(evaluation).encode()
    predictions.write_bytes(prediction_bytes)
    report.write_text(
        render_coinflip_report(
            evaluation,
            prediction_sha256=hashlib.sha256(prediction_bytes).hexdigest(),
            feature_rows=len(dataset.rows),
            feature_skipped=dataset.skipped,
            proxy_skipped=unclassified,
        )
    )

    typer.echo(f"eligible COINFLIP feature rows: {len(dataset.rows)}")
    for fold in evaluation.folds:
        typer.echo(
            f"  train {fold.train_from}–{fold.train_through}, test {fold.test_season}, "
            f"C={fold.selected_c:g}"
        )
    _warn_skipped("games excluded from COINFLIP coverage", dataset.skipped)
    _warn_skipped("stored lines outside proxy coverage", unclassified)
    decision = "PASS" if passes_acceptance_gate(evaluation) else "NULL — retain Elo"
    typer.echo(f"Candidate 1: {decision}")


if __name__ == "__main__":
    app()
