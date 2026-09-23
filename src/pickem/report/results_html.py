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

from pickem.report.charts import RateRow, Series, rate_rows, trend_chart
from pickem.report.results import (
    AGAINST_FIELD_SHARE,
    BACKTEST_TIER_RATES,
    BASELINES,
    GLOSSARY_INTRO,
    RESULT_MARKS,
    STRATEGY_DEFINITIONS,
    WEEK_STRATEGIES,
    GradedGame,
    Record,
    ResultsReport,
    Strategy,
    clv_summary,
    findings,
    record_for,
)

EASTERN = ZoneInfo("America/New_York")

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
figure{margin:12px 0}
figcaption{margin-bottom:6px}
.rate-row{display:grid;grid-template-columns:minmax(8.5rem,11rem) 1fr;gap:10px;
  align-items:center;padding:3px 0}
.rate-label{font-size:14px;line-height:1.25}
.rate-value{margin-left:6px;font-size:15px;font-variant-numeric:tabular-nums}
.rate-label small{display:block;color:var(--muted);font-size:11.5px;
  font-variant-numeric:tabular-nums}
.rate-ticks{position:relative;height:1.2em;font-size:12px;color:var(--muted)}
.rate-ticks span{position:absolute;top:0;white-space:nowrap}
.rate-ticks .t0{left:1.923%}
.rate-ticks .t50{left:50%;transform:translateX(-50%)}
.rate-ticks .t100{right:1.923%}
.rates svg,.trend svg{display:block;width:100%;height:auto}
.trend svg{max-width:560px;overflow:visible}
.track{stroke:var(--line);stroke-width:6;stroke-linecap:round}
.ref{stroke:var(--muted);stroke-dasharray:2 3;stroke-width:1}
.grid{stroke:var(--line);stroke-width:1}
.axis{fill:var(--muted);font-size:13px}
.mark{fill:var(--accent)}
.whisker,.cap{stroke:var(--ink);stroke-width:1.5}
.expected{stroke:var(--winner);stroke-width:3}
.line{fill:none;stroke-width:2.5}
.dot{stroke:var(--surface);stroke-width:1.5}
.line.s-us{stroke:var(--accent)}.dot.s-us,.key.s-us{fill:var(--accent);background:var(--accent)}
.line.s-median{stroke:var(--median)}
.dot.s-median,.key.s-median{fill:var(--median);background:var(--median)}
.line.s-winner{stroke:var(--winner)}
.dot.s-winner,.key.s-winner{fill:var(--winner);background:var(--winner)}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:4px 14px;padding:0;margin:6px 0 0;
  font-size:13px;color:var(--muted)}
.key{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
.empty{color:var(--muted);font-style:italic}
.hit{fill:transparent}
[data-tip]{cursor:pointer}
.tip{position:fixed;z-index:10;pointer-events:none;background:var(--ink);color:var(--bg);
  font-size:12.5px;line-height:1.3;padding:5px 9px;border-radius:6px;
  max-width:calc(100vw - 16px);box-shadow:0 2px 8px rgba(0,0,0,.25)}
.claims li{margin:4px 0}
dl dt{font-weight:600;margin-top:8px}
dl dd{margin:0;color:var(--muted)}
.weeks ul{list-style:none;display:flex;flex-wrap:wrap;gap:6px 14px;padding:0}
.weeks [aria-current]{font-weight:600}
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
        _trend(report),
        _tiers(report),
        _baselines(report),
        _clv_and_field(report),
        _findings(report),
        _glossary(),
        _weeks_nav(report, imported_weeks),
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(body)}</main><script>{_TIP_JS}</script></body></html>\n"
    )


# The one script on the page: a pop-up that shows an element's data-tip on
# mouse hover or on tap, kept inside the viewport. Inline, so nothing loads.
_TIP_JS = """
(function () {
  var tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);
  function target(e) { return e.target.closest ? e.target.closest("[data-tip]") : null; }
  function show(el, x, y) {
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    var box = tip.getBoundingClientRect();
    var left = Math.min(Math.max(8, x - box.width / 2), window.innerWidth - box.width - 8);
    var top = y - box.height - 12;
    if (top < 8) top = y + 16;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  document.addEventListener("pointermove", function (e) {
    if (e.pointerType !== "mouse") return;
    var el = target(e);
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  document.addEventListener("click", function (e) {
    var el = target(e);
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  window.addEventListener("scroll", function () { tip.hidden = true; }, { passive: true });
})();
"""


def describe_record(record: Record) -> tuple[str, str]:
    """The hit rate, and the record in words: wins–losses, the likely range, games, pushes.

    The page's own reading of ``Record``: the Markdown keeps ``str(Record)``. The
    likely range is the 95% Wilson interval; a dash stands in for a rate with no
    decided games.
    """
    pushes = (
        [f"{record.pushes} push" + ("es" if record.pushes > 1 else "")] if record.pushes else []
    )
    if not record.decided:
        return "—", " · ".join(["no decided games", *pushes])
    low, high = record.interval
    games = f"{record.decided} game" + ("s" if record.decided > 1 else "")
    parts = [
        f"{record.wins}–{record.losses}",
        f"likely {low * 100:.0f}–{high:.0%}",
        games,
        *pushes,
    ]
    return f"{record.rate:.0%}", " · ".join(parts)


def _record_row(label: str, record: Record, *, expected: float | None = None) -> RateRow:
    value, detail = describe_record(record)
    if expected is not None:
        detail += f" · NFL backtest {expected:.1%}"
    return RateRow(label, detail, record.rate, record.interval, expected, value=value)


def _record_text(record: Record) -> str:
    value, detail = describe_record(record)
    return detail if not record.decided else f"{value} · {detail}"


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
        f"<li><strong>{escape(board.sport.value.upper())}</strong> "
        f"{escape(str(board.our_points))} pts · "
        f"median {escape(f'{board.median_points:g}')} · "
        f"best {escape(str(board.best_points))}</li>"
        for board in week.boards
    )
    return (
        f'<section id="headline"><div class="tiles">{tiles}</div>'
        f'<ul class="boards">{boards}</ul>'
        f'<p class="note">Beat {escape(f"{week.beat_share:.0%}")} of the field.</p></section>'
    )


def _this_week(report: ResultsReport) -> str:
    parts = ['<section id="this-week"><h2>This week</h2>']
    for sport in sorted({game.game.sport for game in report.week_games}):
        games = [game for game in report.week_games if game.game.sport is sport]
        records = rate_rows(
            [_record_row(st.value, record_for(games, st)) for st in WEEK_STRATEGIES],
            mark="bar",
            caption="Hit rate this week. Whisker: the likely range (95% interval); "
            "dashed line: 50%.",
        )
        parts.append(
            f"<h3>{escape(sport.value.upper())} board</h3>"
            f"{records}"
            f'<details class="games"><summary>All {len(games)} games</summary>'
            '<div class="scroll"><table class="game-table"><thead><tr>'
            "<th>Kickoff (ET)</th><th>Matchup</th><th>Line (home)</th><th>Final</th>"
            "<th>Covered</th><th>Us</th><th>Model</th><th>Field</th><th>Flags</th>"
            f"</tr></thead><tbody>{''.join(_game_row(game) for game in games)}</tbody>"
            "</table></div></details>"
        )
    parts.append("</section>")
    return "".join(parts)


def _game_row(game: GradedGame) -> str:
    g = game.game
    covered = game.covered
    ours = game.picks[Strategy.US]
    us = "blank" if ours is None else f"{game.team(ours)} {RESULT_MARKS[game.result(Strategy.US)]}"
    if game.model is None:
        model = "—"
    else:
        model = (
            f"{game.team(game.model.side)} ({game.model.tier.value}) "
            f"{RESULT_MARKS[game.result(Strategy.MODEL)]}"
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


def _section(section_id: str, heading: str, inner: str) -> str:
    return f'<section id="{section_id}"><h2>{escape(heading)}</h2>{inner}</section>'


def _trend(report: ResultsReport) -> str:
    weeks = [standing.pool_week for standing in report.standings]
    points = trend_chart(
        weeks,
        [
            Series("us", "s-us", tuple(s.our_points for s in report.standings)),
            Series("field median", "s-median", tuple(s.median_points for s in report.standings)),
            Series("winner", "s-winner", tuple(s.winner_points for s in report.standings)),
        ],
        y_max=max(s.winner_points for s in report.standings),
        fmt=lambda value: f"{value:g}",
        caption="Points by pool week",
    )
    beaten = trend_chart(
        weeks,
        [Series("us", "s-us", tuple(s.beat_share for s in report.standings))],
        y_max=1.0,
        fmt=lambda value: f"{value:.0%}",
        caption="Share of the field we beat",
    )
    return _section("trend", "Season trend", points + beaten)


def _tiers(report: ResultsReport) -> str:
    rows = []
    for sport in sorted({game.game.sport for game in report.season_games}):
        for tier, expected in BACKTEST_TIER_RATES.items():
            record = record_for(report.season_games, Strategy.MODEL, sport=sport, tier=tier)
            rows.append(
                _record_row(f"{sport.value.upper()} {tier.value}", record, expected=expected)
            )
    chart = rate_rows(
        rows, mark="bar",
        caption="Model hit rate by tier, season to date. Whisker: 95% interval. "
        "Tick: NFL backtest, 2020–2025.",
    )
    note = (
        '<p class="note">Games with no recommendation stored before kickoff (excluded from '
        f"model grading): {escape(str(report.unknown_model_games))}</p>"
    )
    return _section("tiers", "Model by tier", chart + note)


def _baselines(report: ResultsReport) -> str:
    rows = []
    for strategy in (Strategy.US, *BASELINES):
        record = record_for(report.season_games, strategy)
        rows.append(_record_row(strategy.value, record))
    chart = rate_rows(
        rows, mark="dot",
        caption="Every strategy on the same CBS lines, all boards, season to date. "
        "Whisker: 95% interval.",
    )
    return _section("baselines", "Us against baselines", chart)


def _clv_and_field(report: ResultsReport) -> str:
    clv = clv_summary(report.season_games)
    mean = "—" if clv.mean is None else f"{clv.mean:+.2f} pts"
    interval = "—" if clv.interval is None else f"{clv.interval[0]:+.2f} to {clv.interval[1]:+.2f}"
    share = "—" if clv.positive_share is None else f"{clv.positive_share:.0%}"
    tiles = "".join([
        _tile(mean, "mean CLV"),
        _tile(interval, "95% interval"),
        _tile(share, "picks with CLV > 0"),
        _tile(str(clv.n), "picks with a close"),
    ])
    against = record_for([g for g in report.season_games if g.against_field], Strategy.US)
    field = (
        f"<p>Against the field (≤{escape(f'{AGAINST_FIELD_SHARE:.0%}')} of other entrants on "
        f"our side): {escape(_record_text(against))}</p>"
    )
    return _section(
        "clv", "Closing-line value and the field", f'<div class="tiles">{tiles}</div>{field}'
    )


def _findings(report: ResultsReport) -> str:
    result = findings(report.season_games)
    claims = "".join(f"<li>{escape(claim)}</li>" for claim in result.claims)
    inner = f'<ul class="claims">{claims or "<li>Nothing is distinguishable yet.</li>"}</ul>'
    if result.not_yet:
        lines = "".join(f"<li>{escape(line)}</li>" for line in result.not_yet)
        inner += (
            "<details><summary>Not distinguishable yet "
            f"({escape(str(len(result.not_yet)))})</summary>"
            f"<ul>{lines}</ul></details>"
        )
    return _section("findings", "What the data says", inner)


def _glossary() -> str:
    terms = "".join(
        f"<dt>{escape(strategy.value)}</dt><dd>{escape(text)}</dd>"
        for strategy, text in STRATEGY_DEFINITIONS.items()
    )
    return (
        '<section id="glossary"><details><summary>What the terms mean</summary>'
        f"<p>{escape(GLOSSARY_INTRO)}</p><dl>{terms}</dl></details></section>"
    )


def _weeks_nav(report: ResultsReport, imported_weeks: Sequence[int]) -> str:
    items = "".join(
        f'<li><span aria-current="page">Week {escape(str(week))}</span></li>'
        if week == report.pool_week
        else f'<li><a href="week-{escape(str(week))}.html">Week {escape(str(week))}</a></li>'
        for week in imported_weeks
    )
    return f'<nav class="weeks" aria-label="Pool weeks"><h2>Other weeks</h2><ul>{items}</ul></nav>'
