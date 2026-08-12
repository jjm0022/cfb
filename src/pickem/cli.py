"""Command-line surface. Wiring only — no strategy logic lives here."""
# ruff: noqa: B008

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from pickem import config
from pickem.backtest.runner import run_backtest
from pickem.edge.divergence import compute_edge
from pickem.edge.pipeline import apply_tiebreaks
from pickem.ingest.cbs import parse_cbs_block
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsClient, QuotaExhausted
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
    store.upsert_games(games)
    store.upsert_league_lines(parsed.lines)

    typer.echo(f"ingested {len(parsed.lines)} games for {sport.value} {season} week {week}")
    store.close()


@app.command("poll-odds")
def poll_odds(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Append a market snapshot for the active week."""
    client = OddsClient(config.odds_api_key())
    key = NFL_KEY if sport is Sport.NFL else CFB_KEY
    try:
        result = client.fetch_spreads(
            key,
            resolver=TeamResolver.default(),
            sport=sport,
            season=season,
            week=week,
            now=datetime.now(tz=UTC),
        )
    except QuotaExhausted as exc:
        typer.secho(
            f"odds quota exhausted: {exc}; reports will use cached snapshots",
            fg="yellow",
            err=True,
        )
        raise typer.Exit(code=2) from exc

    store = _store(db)
    store.append_market_lines(result.lines)
    typer.echo(f"appended {len(result.lines)} market lines")
    if result.skipped:
        typer.secho(f"skipped {len(result.skipped)} book rows with no spread:", fg="yellow")
        for skipped in result.skipped:
            typer.secho(f"  skipped: {skipped}", fg="yellow")
    store.close()


@app.command("report")
def report(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
    out: Path = typer.Option(None, help="Also write the sheet to this markdown file"),
) -> None:
    """Render the ranked pick sheet."""
    store = _store(db)
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

    # Games the market never repriced fall through to the rating, built from
    # every completed game already in the store.
    history: list[Game] = []
    for past_week in range(1, week):
        history.extend(store.games_for_week(sport, season, past_week))
    edges = apply_tiebreaks(edges, games, history)

    age = (now - newest).total_seconds() / 60 if newest else None
    sheet = render_sheet(
        edges,
        games,
        generated_at=now,
        provenance="CBS frozen league lines vs latest stored market consensus",
        snapshot_age_minutes=age,
    )
    typer.echo(sheet)
    if out:
        out.write_text(sheet)
    store.close()


@app.command("sync-results")
def sync_results(
    season: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Pull final scores into the store."""
    store = _store(db)
    games = load_nfl_games([season], resolver=TeamResolver.default())
    store.upsert_games(games)
    typer.echo(f"synced {len(games)} NFL games for {season}")
    store.close()


@app.command("backfill")
def backfill(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """One-time historical load for the backtest."""
    store = _store(db)
    resolver = TeamResolver.default()
    seasons = list(range(start, end + 1))
    games = load_nfl_games(seasons, resolver=resolver)
    closers = load_nfl_closing_lines(seasons, resolver=resolver)
    store.upsert_games(games)
    store.append_market_lines(closers.lines)
    typer.echo(f"backfilled {len(games)} games and {len(closers.lines)} closing lines")
    if closers.skipped:
        typer.secho(f"skipped {len(closers.skipped)} rows with no spread — see below", fg="yellow")
        for skipped in closers.skipped:
            typer.secho(f"  skipped: {skipped}", fg="yellow")
    typer.secho(
        "openers are still missing; run the Odds API historical backfill to complete the pair",
        fg="yellow",
    )
    store.close()


@app.command("backtest")
def backtest(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Replay history through the live edge code."""
    store = _store(db)
    games: list[Game] = []
    for season in range(start, end + 1):
        for week in range(1, 23):
            games.extend(store.games_for_week(Sport.NFL, season, week))

    openers, closers = [], []
    for game in games:
        for line in store.market_lines_for(game.game_id):
            (openers if line.book == "open" else closers).append(line)

    result = run_backtest(games, openers, closers)
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
    store.close()


if __name__ == "__main__":
    app()
