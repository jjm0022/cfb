"""Command-line surface. Wiring only — no strategy logic lives here."""
# ruff: noqa: B008

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from pickem import config
from pickem.backtest.calibration import DEFAULT_TOLERANCE, calibrate
from pickem.backtest.runner import run_backtest, split_proxies
from pickem.backtest.snapshots import SnapshotKind, estimate_credits, plan_snapshots
from pickem.edge.divergence import compute_edge, rank_edges
from pickem.edge.pipeline import MissingGameError, apply_tiebreaks
from pickem.ingest.cbs import CbsParseError, parse_cbs_block
from pickem.ingest.cbs_html import parse_cbs_html
from pickem.ingest.cfbd_source import CfbdConfig, default_games_fetcher, load_cfb_games
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsApiError, OddsClient, QuotaExhausted
from pickem.models import FROZEN_SOURCE, SUBMISSION_SOURCE, Game, Sport, make_game_id
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

    typer.echo(f"parsed {len(parsed.lines)} games for {sport.value} {season} week {week}")
    for skipped in parsed.skipped:
        typer.secho(f"  skipped: {skipped!r}", fg="yellow")
    if parsed.skipped:
        raise typer.Exit(code=1)

    store = _store(db)
    games = [
        Game(
            game_id=make_game_id(sport, season, week, away, home),
            sport=sport,
            season=season,
            week=week,
            # A saved page carries the real instant; the pasted block does not,
            # and `now` stays the documented placeholder for that path.
            kickoff_utc=parsed.kickoffs.get(make_game_id(sport, season, week, away, home), now),
            home_team_id=home,
            away_team_id=away,
        )
        for away, home in parsed.matchups
    ]
    with store:
        # Insert-only: the paste carries no scores, so it must never overwrite
        # what sync-results already established.
        store.insert_games_if_absent(games)
        store.upsert_league_lines(parsed.lines)
        typer.echo(f"ingested {len(parsed.lines)} games for {sport.value} {season} week {week}")


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
        slate = [line.game_id for line in store.league_lines_for_week(sport, season, week)]
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
        league_lines = store.league_lines_for_week(sport, season, week)
        games = store.games_for_week(sport, season, week)

        edges = []
        newest: datetime | None = None
        for line in league_lines:
            market = store.market_lines_for(line.game_id)
            for snapshot in market:
                if newest is None or snapshot.captured_at > newest:
                    newest = snapshot.captured_at
            edges.append(compute_edge(line, market))

        # Games the market never repriced fall through to the rating, built
        # from every completed game already stored — prior seasons included,
        # without which every early-season rating is still the initial value.
        history = store.games_before(sport, season, week)
        untrained = not any(g.home_score is not None and g.away_score is not None for g in history)
        try:
            edges = apply_tiebreaks(edges, games, history)
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


@app.command("backfill-history")
def backfill_history(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    execute: bool = typer.Option(False, "--execute", help="Actually spend credits and write rows"),
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
    with _store(db) as store:
        games: list[Game] = []
        for season in range(start, end + 1):
            for week in range(1, 23):
                games.extend(store.games_for_week(Sport.NFL, season, week))
        if not games:
            typer.secho(
                f"no NFL games stored for {start}-{end}; run `pickem backfill` first",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=1)

        plan = plan_snapshots(games)
        cost = estimate_credits(plan)
        frozen_count = sum(1 for request in plan if request.kind is SnapshotKind.FROZEN)
        weeks = len({(request.season, request.week) for request in plan})
        typer.echo(
            f"{len(plan)} snapshots ({frozen_count} frozen, {len(plan) - frozen_count} "
            f"submission) across {weeks} weeks = {cost} credits"
        )
        if cost > max_credits:
            typer.secho(
                f"plan exceeds the {max_credits}-credit ceiling; narrow --from/--to",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=1)
        if not execute:
            typer.secho(
                "dry run — pass --execute to spend credits and write rows",
                fg="yellow",
            )
            return

        with OddsClient(config.odds_api_key()) as client:
            for index, request in enumerate(plan, start=1):
                source = FROZEN_SOURCE if request.kind is SnapshotKind.FROZEN else SUBMISSION_SOURCE
                try:
                    result = client.fetch_historical_spreads(
                        NFL_KEY,
                        resolver=TeamResolver.default(),
                        sport=Sport.NFL,
                        season=request.season,
                        week=request.week,
                        at=request.at,
                        slate=request.slate,
                        window=request.window,
                        source=source,
                    )
                except QuotaExhausted as exc:
                    # Half a backfill is fine; half a backfill nobody knows is
                    # half is not.
                    typer.secho(
                        f"stopped at snapshot {index}/{len(plan)}: {exc}", fg="red", err=True
                    )
                    raise typer.Exit(code=1) from exc
                store.append_market_lines(result.lines)
                typer.echo(
                    f"[{index}/{len(plan)}] {request.season} wk{request.week:02d} "
                    f"{request.kind.value}: {len(result.lines)} lines"
                )
                _warn_skipped(f"snapshot {index} rows not stored", result.skipped)


@app.command("backtest")
def backtest(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Replay history through the live edge code."""
    with _store(db) as store:
        games: list[Game] = []
        for season in range(start, end + 1):
            for week in range(1, 23):
                games.extend(store.games_for_week(Sport.NFL, season, week))

        stored = [line for game in games for line in store.market_lines_for(game.game_id)]
        frozen, submission, unclassified = split_proxies(stored)
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
        league_lines = []
        for week in range(from_week, to_week + 1):
            league_lines.extend(store.league_lines_for_week(sport, season, week))

        market = [line for lg in league_lines for line in store.market_lines_for(lg.game_id)]
        result = calibrate(
            league_lines,
            market,
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


if __name__ == "__main__":
    app()
