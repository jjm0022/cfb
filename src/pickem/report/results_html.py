"""Render a ResultsReport as the self-contained dashboard page.

One HTML document with inline CSS and inline SVG: no scripts, no fonts and no
requests to any host, so it renders the same offline and on every load. Every
figure comes from ResultsReport and the functions results.py already exposes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from pickem.backtest.stats import Result
from pickem.models import Side
from pickem.report.results import (
    GradedGame,
    Record,
    ResultsReport,
    Strategy,
    record_for,
)

EASTERN = ZoneInfo("America/New_York")
_MARKS = {Result.WIN: "✓", Result.LOSS: "✗", Result.PUSH: "push"}
_WEEK_STRATEGIES = (Strategy.US, Strategy.MODEL, Strategy.FIELD, Strategy.CLOSE_DIVERGENCE)

_CSS = """
:root{
  --bg:#f4f6f3;--surface:#ffffff;--ink:#17211c;--muted:#5a675f;--line:#d3dbd5;
  --accent:#2f7a55;--median:#3b6fb6;--winner:#a8741f;--good:#2f7a55;--bad:#b4432f;
  --chip-model:#f3e3c4;--chip-field:#dbe6f6;color-scheme:light;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#101613;--surface:#18201c;--ink:#e3eae5;--muted:#97a59d;--line:#2d3933;
    --accent:#6cc497;--median:#86aee8;--winner:#e0b25c;--good:#6cc497;--bad:#e7826d;
    --chip-model:#4a3a1c;--chip-field:#1f3350;color-scheme:dark;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding-inline:16px;padding-block:20px 48px}
header .eyebrow{margin:0;color:var(--muted);font-size:13px;letter-spacing:.04em;
  text-transform:uppercase}
h1{margin:2px 0 4px;font-size:26px}
h2{margin:32px 0 10px;font-size:19px;text-wrap:balance}
h3{margin:18px 0 6px;font-size:16px}
.stamp,.note,figcaption,.boards{color:var(--muted);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;
  margin:14px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:10px 12px;display:flex;flex-direction:column}
.tile .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.tile .label{color:var(--muted);font-size:12px}
.boards{list-style:none;padding:0;margin:0}
.boards strong{color:var(--ink)}
table{border-collapse:collapse;font-variant-numeric:tabular-nums}
.records th{text-align:left;font-weight:500;padding:2px 14px 2px 0;color:var(--muted)}
.records td{padding:2px 0}
details{margin:8px 0}
summary{cursor:pointer;color:var(--accent);font-weight:500}
summary:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.game-table{font-size:13px;white-space:nowrap;margin-top:6px}
.game-table th,.game-table td{padding:5px 10px 5px 0;border-bottom:1px solid var(--line);
  text-align:left}
.game-table th{color:var(--muted);font-weight:500}
.chip{display:inline-block;border-radius:999px;padding:0 8px;margin-right:4px;font-size:12px;
  color:var(--ink)}
.chip-model{background:var(--chip-model)}
.chip-field{background:var(--chip-field)}
a{color:var(--accent)}
"""


def render_results_dashboard(
    report: ResultsReport, *, generated_at: datetime, imported_weeks: Sequence[int]
) -> str:
    title = f"Pool week {report.pool_week} · {report.season}"
    body = [
        '<header><p class="eyebrow">'
        f"{escape(str(report.season))} pick'em · {escape(report.entry_name)}</p>"
        f"<h1>Pool week {report.pool_week}</h1>"
        f'<p class="stamp">Generated {generated_at.astimezone(EASTERN):%a %b %-d, %-I:%M %p} ET'
        "</p></header>",
        _headline(report),
        _this_week(report),
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )


def _record(record: Record) -> str:
    """Record text as the Markdown prints it, with a dash when nothing was decided."""
    if not record.decided:
        return f"{record.wins}–{record.losses} ({record.pushes}) = —, n=0"
    return str(record)


def _tile(value: str, label: str) -> str:
    return (
        f'<div class="tile"><span class="value">{escape(value)}</span>'
        f'<span class="label">{escape(label)}</span></div>'
    )


def _headline(report: ResultsReport) -> str:
    week = report.current
    gap = "top score" if week.gap_to_winner == 0 else f"{week.gap_to_winner} back"
    tiles = "".join([
        _tile(str(week.our_points), f"points of {len(report.week_games)} games"),
        _tile(f"{week.our_rank} of {week.entrants}", "rank"),
        _tile(f"{week.median_points:g}", "field median"),
        _tile(str(week.winner_points), "winner"),
        _tile(gap, "gap to winner"),
    ])
    boards = "".join(
        f"<li><strong>{board.sport.value.upper()}</strong> {board.our_points} pts · "
        f"median {board.median_points:g} · best {board.best_points}</li>"
        for board in week.boards
    )
    return (
        f'<section id="headline"><div class="tiles">{tiles}</div>'
        f'<ul class="boards">{boards}</ul>'
        f'<p class="note">Beat {week.beat_share:.0%} of the field.</p></section>'
    )


def _this_week(report: ResultsReport) -> str:
    parts = ['<section id="this-week"><h2>This week</h2>']
    for sport in sorted({game.game.sport for game in report.week_games}):
        games = [game for game in report.week_games if game.game.sport is sport]
        records = "".join(
            f"<tr><th>{escape(st.value)}</th><td>{escape(_record(record_for(games, st)))}</td></tr>"
            for st in _WEEK_STRATEGIES
        )
        parts.append(
            f"<h3>{sport.value.upper()} board</h3>"
            f'<table class="records">{records}</table>'
            f'<details class="games"><summary>All {len(games)} games</summary>'
            '<div class="scroll"><table class="game-table"><thead><tr>'
            "<th>Kickoff (ET)</th><th>Matchup</th><th>Line (home)</th><th>Final</th>"
            "<th>Covered</th><th>Us</th><th>Model</th><th>Field</th><th>Flags</th>"
            f"</tr></thead><tbody>{''.join(_game_row(game) for game in games)}</tbody>"
            "</table></div></details>"
        )
    parts.append("</section>")
    return "".join(parts)


def _team(game: GradedGame, side: Side | None) -> str:
    if side is None:
        return "—"
    return game.game.home_team_id if side is Side.HOME else game.game.away_team_id


def _game_row(game: GradedGame) -> str:
    g = game.game
    home_result = game.result(Strategy.HOME)
    covered = "push" if home_result is Result.PUSH else _team(
        game, Side.HOME if home_result is Result.WIN else Side.AWAY
    )
    ours = game.picks[Strategy.US]
    us = "blank" if ours is None else f"{_team(game, ours)} {_MARKS[game.result(Strategy.US)]}"
    if game.model is None:
        model = "—"
    else:
        model = (
            f"{_team(game, game.model.side)} ({game.model.tier.value}) "
            f"{_MARKS[game.result(Strategy.MODEL)]}"
        )
    total = game.field_home + game.field_away
    field = "—" if not total else f"{game.field_home / total:.0%} home"
    chips = []
    if game.model is not None and ours is not None and game.model.side is not ours:
        chips.append('<span class="chip chip-model">differs from model</span>')
    if game.against_field:
        chips.append('<span class="chip chip-field">against field</span>')
    cells = [
        f"{g.kickoff_utc.astimezone(EASTERN):%a %-I:%M %p}",
        f"{g.away_team_id} at {g.home_team_id}",
        f"{game.league_spread:+.1f}",
        f"{g.away_score}–{g.home_score}",
        covered,
        us,
        model,
        field,
    ]
    return (
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells)
        + f"<td>{''.join(chips)}</td></tr>"
    )
