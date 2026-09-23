"""Render a ResultsReport as the weekly Markdown report."""

from __future__ import annotations

from datetime import datetime

from pickem.models import Sport
from pickem.report.results import (
    AGAINST_FIELD_SHARE,
    BACKTEST_TIER_RATES,
    BASELINES,
    GLOSSARY_INTRO,
    RESULT_MARKS,
    STRATEGY_DEFINITIONS,
    WEEK_STRATEGIES,
    GradedGame,
    ResultsReport,
    Strategy,
    clv_summary,
    findings,
    record_for,
)


def render_results_report(report: ResultsReport, *, generated_at: datetime) -> str:
    week_sports = sorted({game.game.sport for game in report.week_games})
    season_sports = sorted({game.game.sport for game in report.season_games})
    lines = [
        f"# Pool week {report.pool_week} results — {report.season}",
        f"> Generated {generated_at:%Y-%m-%d %H:%M UTC} from stored CBS standings, league "
        "lines, market snapshots and recommendation history.",
        "",
        *_headline(report),
        "## This week",
        "",
    ]
    for sport in week_sports:
        games = [game for game in report.week_games if game.game.sport is sport]
        lines += [f"### {sport.value.upper()} board", "", "| Strategy | Record |", "|---|---|"]
        lines += [f"| {st.value} | {record_for(games, st)} |" for st in WEEK_STRATEGIES]
        lines += [
            "",
            "| Kickoff (UTC) | Matchup | Line | Final | Covered | Us | Model | Field home % "
            "| Close | CLV | Flags |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
            *(_game_row(game) for game in games),
            "",
        ]
    lines += _season(report, season_sports)
    lines += _findings(report)
    lines += _glossary()
    return "\n".join(lines) + "\n"


def _headline(report: ResultsReport) -> list[str]:
    week = report.current
    return [
        "## Headline",
        "",
        f"**{report.entry_name}: {week.our_points} pts, rank {week.our_rank} of "
        f"{week.entrants}** — field median {week.median_points:g}, winner "
        f"{week.winner_points} (gap {week.gap_to_winner}); beat {week.beat_share:.0%} of the "
        "field.",
        "",
        "| Board | Us | Field median | Best |",
        "|---|---|---|---|",
        *(
            f"| {b.sport.value.upper()} | {b.our_points} | {b.median_points:g} | {b.best_points} |"
            for b in week.boards
        ),
        "",
    ]


def _season(report: ResultsReport, sports: list[Sport]) -> list[str]:
    games = report.season_games
    lines = [
        "## Season to date",
        "",
        "### Model by tier",
        "",
        "| Board | Tier | Record | NFL backtest |",
        "|---|---|---|---|",
    ]
    for sport in sports:
        for tier, expected in BACKTEST_TIER_RATES.items():
            record = record_for(games, Strategy.MODEL, sport=sport, tier=tier)
            lines.append(f"| {sport.value.upper()} | {tier.value} | {record} | {expected:.1%} |")
    lines += [
        "",
        "Games with no recommendation stored before kickoff (excluded from model grading): "
        f"{report.unknown_model_games}",
        "",
        "### Us against baselines",
        "",
        "| Board | Strategy | Record |",
        "|---|---|---|",
    ]
    for sport in [None, *sports]:
        label = "All" if sport is None else sport.value.upper()
        for strategy in (Strategy.US, *BASELINES):
            record = record_for(games, strategy, sport=sport)
            lines.append(f"| {label} | {strategy.value} | {record} |")
    lines += [
        "",
        "### Closing-line value (our picks)",
        "",
        "| Board | n | Mean | 95% interval | Share > 0 |",
        "|---|---|---|---|---|",
    ]
    for sport in [None, *sports]:
        label = "All" if sport is None else sport.value.upper()
        clv = clv_summary(games, sport=sport)
        if not clv.n:
            lines.append(f"| {label} | 0 | n/a | n/a | n/a |")
            continue
        interval = (
            "n/a" if clv.interval is None else f"{clv.interval[0]:+.2f} to {clv.interval[1]:+.2f}"
        )
        lines.append(
            f"| {label} | {clv.n} | {clv.mean:+.2f} | {interval} | {clv.positive_share:.0%} |"
        )
    against = [game for game in games if game.against_field]
    lines += [
        "",
        f"### Against the field (≤{AGAINST_FIELD_SHARE:.0%} of other entrants on our side)",
        "",
        f"Record: {record_for(against, Strategy.US)}",
        "",
    ]
    return lines


def _findings(report: ResultsReport) -> list[str]:
    result = findings(report.season_games)
    lines = ["## What the data says", ""]
    lines += [f"- {claim}" for claim in result.claims] or ["- Nothing is distinguishable yet."]
    if result.not_yet:
        lines += ["", "Not distinguishable yet:", ""]
        lines += [f"- {line}" for line in result.not_yet]
    return lines


def _glossary() -> list[str]:
    """Spell out the graded strategies, so a record is read against what it measures.

    Every strategy picks a side of the same frozen CBS line, so the records are
    comparable; the ones that cannot pick a side on a game sit that game out,
    which is why their n is smaller.
    """
    return [
        "",
        "## What the terms mean",
        "",
        GLOSSARY_INTRO,
        "",
        *(
            f"- **{strategy.value}** — {text}"
            for strategy, text in STRATEGY_DEFINITIONS.items()
        ),
    ]


def _game_row(game: GradedGame) -> str:
    g = game.game
    covered = game.covered
    ours = game.picks[Strategy.US]
    us = "blank" if ours is None else f"{game.team(ours)} {RESULT_MARKS[game.result(Strategy.US)]}"
    model = (
        "—" if game.model is None
        else f"{game.team(game.model.side)} ({game.model.tier.value})"
    )
    total = game.field_home + game.field_away
    field = "—" if not total else f"{game.field_home / total:.0%}"
    close = "n/a" if game.close_spread is None else f"{game.close_spread:+.1f}"
    clv = "n/a" if game.clv is None else f"{game.clv:+.1f}"
    flags = []
    if game.model is not None and ours is not None and game.model.side is not ours:
        flags.append("differs from model")
    if game.against_field:
        flags.append("against field")
    return (
        f"| {g.kickoff_utc:%a %H:%M} | {g.away_team_id} at {g.home_team_id} | "
        f"{game.league_spread:+.1f} | {g.away_score}-{g.home_score} | {covered} | {us} | "
        f"{model} | {field} | {close} | {clv} | {', '.join(flags)} |"
    )
