"""Command-line surface. Wiring only — no strategy logic lives here."""
# ruff: noqa: B008

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from pickem import config
from pickem.backtest.runner import run_backtest, split_proxies
from pickem.edge.divergence import compute_edge, rank_edges
from pickem.edge.pipeline import MissingGameError, apply_tiebreaks
from pickem.ingest.cbs import CbsParseError, parse_cbs_block
from pickem.ingest.cfbd_source import CfbdConfig, default_games_fetcher, load_cfb_games
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsApiError, OddsClient, QuotaExhausted
from pickem.models import Game, Sport, make_game_id
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
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Parse the CBS pick sheet paste into frozen league lines."""
    text = file.read_text() if file else sys.stdin.read()
    resolver = TeamResolver.default()
    now = datetime.now(tz=UTC)

    try:
        parsed = parse_cbs_block(
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
            kickoff_utc=now,
            home_team_id=home,
            away_team_id=away,
        )
        for away, home in parsed.matchups
    ]
    with store:
        # Insert-only: the paste carries a placeholder kickoff and no scores, so
        # it must never overwrite what sync-results already established.
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


if __name__ == "__main__":
    app()
