# Results Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render each imported pool week as a self-contained HTML dashboard, written by the Tuesday results job and served privately over Tailscale.

**Architecture:** A second renderer of the existing `ResultsReport` (`report/results_html.py`) beside the Markdown one, with the SVG geometry in `report/charts.py`. The CLI's `import-results` and `results-report` write `week-{N}.html` and `index.html` atomically into a local dashboard directory, and the results DM links to the page when `PICKEM_DASHBOARD_URL` is set. `tailscale serve` exposes the directory to the owner's tailnet.

**Tech Stack:** Python 3 standard library (`html`, `html.parser`, `zoneinfo`), Typer, loguru, pytest, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-23-results-dashboard-design.md`

## Global Constraints

- The page makes no external requests: no `<script>`, no `<link>`, no `src` attribute; the only `href`s are relative `week-{N}.html` links.
- Every interpolated value is HTML-escaped with `html.escape`.
- Every figure comes from `ResultsReport` and the functions `results.py` already exposes; no new statistic.
- A rate with n = 0 prints `—`, never 0%.
- Rates print as `str(Record)` does: `W–L (P) = xx.x% [lo–hi], n=N`.
- Backtest ticks come from `BACKTEST_TIER_RATES` (strong 0.637, lean 0.542, coinflip 0.495) and are labeled "NFL backtest".
- Dashboard directory: `config.dashboard_dir()`, env `PICKEM_DASHBOARD_DIR`, default `~/.local/share/pickem/dashboard`, local not NAS.
- `index.html` changes only when the rendered week is the latest imported week.
- A dashboard failure never loses the import or the Markdown, still sends the DM (without the link), and exits 3.
- Serve with `tailscale serve`, never `tailscale funnel`.
- Log events: `dashboard_written`, `dashboard_write_failed` (logging-standards skill; `logger.bind(event=...)`).
- Run tests with `uv run pytest`; lint with `uv run ruff check` if the project's CI does (`grep ruff pyproject.toml`).
- Commit messages end with the session attribution lines given in the system prompt.
- Where a step says "append to" a test file, put any new `import` lines in that file's import block at the top, not mid-file.

## Review Focus

1. **A trend where every value is 0** (e.g. a week where the winner scored 0 in a test, or `beat_share` all 0): the y-scale must not divide by zero. Test in Task 3.
2. **A tie for first** (`gap_to_winner == 0`): the gap tile reads "top score", not "0 back". Test in Task 4.
3. **The dashboard directory cannot be created or written** (permissions, a file where the directory should be): the DM still goes out without a link, the Markdown is kept, exit 3. Test in Task 7.
4. **A reader in Eastern time on a phone:** kickoff times display in ET, not UTC. Test in Task 4.
5. **`results-report` run while the timer writes the same week:** writes are temp-file-then-rename, so a reader never sees a truncated page; the temp file lives in the same directory so the rename is atomic. Test (no temp file left behind) in Task 7.

## File Structure

| File | Responsibility |
|---|---|
| `src/pickem/report/results.py` (modify) | Gains `STRATEGY_DEFINITIONS` and `GLOSSARY_INTRO`, shared by both renderers |
| `src/pickem/report/results_markdown.py` (modify) | Glossary reads the shared definitions |
| `src/pickem/config.py` (modify) | `dashboard_dir()`, `dashboard_url()` |
| `src/pickem/report/charts.py` (create) | Scales and HTML+SVG fragments for rate rows and trends; knows nothing of pick'em |
| `src/pickem/report/results_html.py` (create) | `render_results_dashboard`: page skeleton, CSS, every section |
| `src/pickem/notify/discord_dm.py` (modify) | Optional dashboard link field |
| `src/pickem/cli.py` (modify) | Writes the pages, orders DM before the dashboard failure exit |
| `tests/conftest.py` (modify) | Autouse: point `PICKEM_DASHBOARD_DIR` at a temp dir, clear `PICKEM_DASHBOARD_URL` |
| `tests/results_helpers.py` (modify) | `assert_well_formed(html)` |
| `tests/test_charts.py`, `tests/test_results_html.py` (create) | Renderer tests |
| `tests/test_results_markdown.py`, `tests/test_config.py`, `tests/test_discord_dm.py`, `tests/test_results_cli.py`, `tests/test_results_real_pages.py` (modify) | As each task says |
| `docs/runbooks/dashboard.md` (create), `README.md` (modify) | Setup and operation |

---

### Task 1: Share the strategy glossary

**Files:**
- Modify: `src/pickem/report/results.py` (after `BASELINES`)
- Modify: `src/pickem/report/results_markdown.py:144-182`
- Test: `tests/test_results_markdown.py`

**Interfaces:**
- Produces: `results.STRATEGY_DEFINITIONS: dict[Strategy, str]` (plain text, no Markdown markup, one entry per `Strategy`, in `Strategy` order) and `results.GLOSSARY_INTRO: str`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_results_markdown.py`:

```python
from pickem.report.results import GLOSSARY_INTRO, STRATEGY_DEFINITIONS


def test_every_strategy_has_a_plain_text_definition():
    assert list(STRATEGY_DEFINITIONS) == list(Strategy)
    for text in (GLOSSARY_INTRO, *STRATEGY_DEFINITIONS.values()):
        assert "*" not in text and "`" not in text
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_results_markdown.py::test_every_strategy_has_a_plain_text_definition -v`
Expected: FAIL with `ImportError: cannot import name 'GLOSSARY_INTRO'`

- [ ] **Step 3: Move the definitions** — in `src/pickem/report/results.py`, after the `BASELINES` tuple, add:

```python
GLOSSARY_INTRO = (
    "Each line above grades one way of choosing a side. All of them are scored "
    "against the same CBS line, so their records compare directly. A strategy "
    "with no side on a game is not graded on it — that is why some carry a "
    "smaller n."
)

# Plain text: each renderer adds its own emphasis to the term.
STRATEGY_DEFINITIONS: dict[Strategy, str] = {
    Strategy.US: "the pick actually submitted on our CBS sheet. This is the only row "
    "that cost us anything; the rest are yardsticks.",
    Strategy.MODEL: "the last recommendation the model produced before kickoff. This is "
    "the advice we could still have acted on, so it is the fair measure of the model.",
    Strategy.FIRST_SHEET: "the model's earliest recommendation for that game, before any "
    "later revision. Compared against model, it shows whether reworking the sheet "
    "through the week actually helps.",
    Strategy.CLOSE_DIVERGENCE: "take whichever side the closing market rates higher than "
    "the CBS line did. CBS freezes its number early; when the market closes on a "
    "different one, this bets that the market's later number is the better one. It "
    "sits out any game where the close matches the board or no close was captured.",
    Strategy.FAVORITES: "always take the side the CBS line favors.",
    Strategy.HOME: "always take the home team.",
    Strategy.FIELD: "the side most other entrants in the pool picked. Beating "
    "it is what moves us up the standings; a tie is not graded.",
}
```

In `src/pickem/report/results_markdown.py`, add `GLOSSARY_INTRO` and `STRATEGY_DEFINITIONS` to the `from pickem.report.results import (...)` list, delete the `_DEFINITIONS` tuple, and replace the body of `_glossary` (keep its docstring) with:

```python
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
```

- [ ] **Step 4: Run the Markdown tests**

Run: `uv run pytest tests/test_results_markdown.py -v`
Expected: all PASS, including the existing `test_glossary_defines_every_strategy_the_report_grades`.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/results.py src/pickem/report/results_markdown.py tests/test_results_markdown.py
git commit -m "refactor: share the strategy glossary between report renderers"
```

---

### Task 2: Dashboard settings

**Files:**
- Modify: `src/pickem/config.py` (after `discord_owner_id`)
- Modify: `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.dashboard_dir() -> Path` (expanded, env read on each call); `config.dashboard_url() -> str | None` (stripped, always ending in `/`, `None` when unset or blank).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py` (it already imports `config`; check the top of the file and match its import style):

```python
def test_dashboard_dir_defaults_under_local_share(monkeypatch):
    monkeypatch.delenv("PICKEM_DASHBOARD_DIR", raising=False)
    assert config.dashboard_dir() == Path.home() / ".local/share/pickem/dashboard"


def test_dashboard_dir_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PICKEM_DASHBOARD_DIR", str(tmp_path / "dash"))
    assert config.dashboard_dir() == tmp_path / "dash"


def test_dashboard_url_is_none_when_unset_or_blank(monkeypatch):
    monkeypatch.delenv("PICKEM_DASHBOARD_URL", raising=False)
    assert config.dashboard_url() is None
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "  ")
    assert config.dashboard_url() is None


def test_dashboard_url_always_ends_in_a_slash(monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem")
    assert config.dashboard_url() == "https://sandbox.tail750bff.ts.net/pickem/"
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem/")
    assert config.dashboard_url() == "https://sandbox.tail750bff.ts.net/pickem/"
```

Add `from pathlib import Path` to the test file's imports if absent.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_config.py -k dashboard -v`
Expected: FAIL with `AttributeError: module 'pickem.config' has no attribute 'dashboard_dir'`

- [ ] **Step 3: Implement** — append to `src/pickem/config.py`:

```python
def dashboard_dir() -> Path:
    """Where the results dashboard pages are written.

    Local, not on the NAS, so the page stays reachable when the share is
    offline. Read on each call so tests and one-off runs can repoint it.
    """
    value = os.environ.get("PICKEM_DASHBOARD_DIR") or "~/.local/share/pickem/dashboard"
    return Path(value).expanduser()


def dashboard_url() -> str | None:
    """The dashboard's base address for the results DM, or ``None`` to omit the link."""
    value = (os.environ.get("PICKEM_DASHBOARD_URL") or "").strip()
    if not value:
        return None
    return value if value.endswith("/") else value + "/"
```

- [ ] **Step 4: Keep every test off the real directory** — in `tests/conftest.py`, add two lines inside `isolate_cli_logging`, after the existing `setenv` calls:

```python
    monkeypatch.setenv("PICKEM_DASHBOARD_DIR", str(tmp_path / "dashboard"))
    monkeypatch.delenv("PICKEM_DASHBOARD_URL", raising=False)
```

and change its docstring to `"""Keep CLI log sinks and dashboard pages inside each test's temporary directory."""`. The `delenv` matters because `.env` may set the URL once Task 8 is done, and `load_dotenv` runs at import.

- [ ] **Step 5: Run the config tests and the whole suite**

Run: `uv run pytest tests/test_config.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: add dashboard directory and URL settings"
```

---

### Task 3: Chart geometry

**Files:**
- Create: `src/pickem/report/charts.py`
- Test: `tests/test_charts.py`

**Interfaces:**
- Produces:
  - `EMPTY: str` — `'<p class="empty">No games yet</p>'`
  - `rate_x(rate: float) -> float` — `8.0 + rate * 400.0`
  - `@dataclass(frozen=True) RateRow(label: str, detail: str, rate: float | None, interval: tuple[float, float] | None, expected: float | None = None)`
  - `rate_rows(rows: Sequence[RateRow], *, mark: str, caption: str) -> str` — `mark` is `"bar"` or `"dot"`; returns a `<figure class="rates">` or `EMPTY`
  - `trend_x(index: int, count: int) -> float`, `trend_y(value: float, y_max: float) -> float`
  - `@dataclass(frozen=True) Series(label: str, css_class: str, values: tuple[float, ...])`
  - `trend_chart(weeks: Sequence[int], series: Sequence[Series], *, y_max: float, fmt: Callable[[float], str], caption: str) -> str` — returns a `<figure class="trend">` or `EMPTY`
- All coordinates print with one decimal (`f"{v:.1f}"`), so tests can match them literally.

- [ ] **Step 1: Write the failing tests** — create `tests/test_charts.py`:

```python
import re

from pickem.report.charts import (
    EMPTY,
    RateRow,
    Series,
    rate_rows,
    rate_x,
    trend_chart,
    trend_x,
    trend_y,
)


def test_rate_scale_maps_zero_half_and_one():
    assert (rate_x(0.0), rate_x(0.5), rate_x(1.0)) == (8.0, 208.0, 408.0)


def test_dot_mark_whiskers_and_expected_tick_sit_on_the_scale():
    html = rate_rows(
        [RateRow("CFB strong", "3–1 (0)", 0.75, (0.25, 0.75), expected=0.637)],
        mark="dot", caption="c",
    )
    assert 'class="mark" cx="308.0"' in html
    assert 'class="whisker" x1="108.0"' in html and 'x2="308.0"' in html
    assert 'class="expected" x1="262.8"' in html


def test_bar_mark_runs_from_zero_to_the_rate():
    html = rate_rows([RateRow("us", "d", 0.5, (0.4, 0.6))], mark="bar", caption="c")
    assert '<rect class="mark" x="8.0"' in html and 'width="200.0"' in html


def test_a_row_without_a_rate_draws_no_mark_but_keeps_the_tick():
    html = rate_rows(
        [RateRow("NFL lean", "0–0 (0) = —, n=0", None, None, expected=0.542)],
        mark="bar", caption="c",
    )
    assert 'class="mark"' not in html
    assert 'class="expected" x1="224.8"' in html
    assert "n=0" in html


def test_rate_labels_are_escaped_html_not_svg_text():
    html = rate_rows([RateRow("<b>", "a&b", 0.5, None)], mark="dot", caption="<c>")
    assert "&lt;b&gt;" in html and "a&amp;b" in html and "&lt;c&gt;" in html
    assert "<b>" not in html


def test_no_rows_and_no_weeks_say_no_games_yet():
    assert rate_rows([], mark="dot", caption="c") == EMPTY
    assert trend_chart([], [], y_max=1, fmt=str, caption="c") == EMPTY


def test_trend_scale():
    assert trend_x(0, 1) == 212.0
    assert (trend_x(0, 3), trend_x(2, 3)) == (36.0, 388.0)
    assert (trend_y(0, 10), trend_y(10, 10)) == (150.0, 12.0)


def test_one_week_draws_points_without_a_line():
    html = trend_chart([2], [Series("us", "s-us", (16,))], y_max=20, fmt=str, caption="c")
    assert "<polyline" not in html
    assert 'cx="212.0" cy="39.6"' in html  # 150 - 16/20 * 138


def test_several_weeks_draw_a_line_through_every_point():
    html = trend_chart(
        [1, 2, 3], [Series("us", "s-us", (8, 16, 12))], y_max=16, fmt=str, caption="c"
    )
    assert 'points="36.0,81.0 212.0,12.0 388.0,46.5"' in html
    assert html.count('<circle class="dot s-us"') == 3


def test_an_all_zero_trend_does_not_divide_by_zero():
    html = trend_chart([1, 2], [Series("us", "s-us", (0, 0))], y_max=0, fmt=str, caption="c")
    assert html.count('cy="150.0"') == 2


def test_trend_has_a_legend_and_week_labels():
    html = trend_chart(
        [1, 2], [Series("us", "s-us", (1, 2)), Series("winner", "s-winner", (3, 4))],
        y_max=4, fmt=lambda v: f"{v:g}", caption="Points",
    )
    assert re.search(r'class="key s-winner".*winner', html)
    assert ">Wk 1<" in html and ">Wk 2<" in html
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_charts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.report.charts'`

- [ ] **Step 3: Implement** — create `src/pickem/report/charts.py`:

```python
"""Inline SVG charts for the results dashboard.

One linear scale per chart places every mark, whisker and tick, so a number
on the page and its mark cannot disagree. Labels are HTML beside each small
SVG rather than SVG text, so they stay readable when a phone scales the
drawing down. Colors come from CSS classes the page styles through its theme
tokens; nothing here names a color.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

EMPTY = '<p class="empty">No games yet</p>'

# Rate rows: a 0–1 rate across a 400-unit plot, padded so end caps are not clipped.
_PAD = 8.0
_PLOT_W = 400.0
_VIEW_W = _PLOT_W + 2 * _PAD
_ROW_H = 24.0

# Trends: weeks across, values up.
_TREND_W = 424.0
_TREND_H = 180.0
_X0, _X1 = 36.0, 388.0
_Y0, _Y1 = 12.0, 150.0


def _n(value: float) -> str:
    return f"{value:.1f}"


def rate_x(rate: float) -> float:
    """Horizontal position of a 0–1 rate on every rate row."""
    return _PAD + rate * _PLOT_W


@dataclass(frozen=True)
class RateRow:
    label: str
    detail: str
    rate: float | None
    interval: tuple[float, float] | None
    expected: float | None = None


def rate_rows(rows: Sequence[RateRow], *, mark: str, caption: str) -> str:
    """One labelled row per rate: a bar or dot, its interval whisker, an expectation tick."""
    if not rows:
        return EMPTY
    parts = [f'<figure class="rates"><figcaption>{escape(caption)}</figcaption>', _rate_axis()]
    for row in rows:
        parts.append(
            '<div class="rate-row">'
            f'<div class="rate-label">{escape(row.label)}<small>{escape(row.detail)}</small></div>'
            f"{_rate_svg(row, mark)}</div>"
        )
    parts.append("</figure>")
    return "".join(parts)


def _rate_axis() -> str:
    ticks = (
        f'<text x="{_n(rate_x(0))}" y="14" text-anchor="start">0%</text>'
        f'<text x="{_n(rate_x(0.5))}" y="14" text-anchor="middle">50%</text>'
        f'<text x="{_n(rate_x(1))}" y="14" text-anchor="end">100%</text>'
    )
    return (
        '<div class="rate-row rate-axis"><div></div>'
        f'<svg viewBox="0 0 {_n(_VIEW_W)} 20" aria-hidden="true">{ticks}</svg></div>'
    )


def _rate_svg(row: RateRow, mark: str) -> str:
    mid = _n(_ROW_H / 2)
    aria = f"{row.label}: " + ("no decided games" if row.rate is None else f"{row.rate:.1%}")
    parts = [
        f'<svg viewBox="0 0 {_n(_VIEW_W)} {_n(_ROW_H)}" role="img" aria-label="{escape(aria)}">',
        f'<line class="track" x1="{_n(rate_x(0))}" y1="{mid}" x2="{_n(rate_x(1))}" y2="{mid}"/>',
        f'<line class="ref" x1="{_n(rate_x(0.5))}" y1="2.0" x2="{_n(rate_x(0.5))}" '
        f'y2="{_n(_ROW_H - 2)}"/>',
    ]
    if row.rate is not None:
        if row.interval is not None:
            low, high = (_n(rate_x(v)) for v in row.interval)
            parts.append(f'<line class="whisker" x1="{low}" y1="{mid}" x2="{high}" y2="{mid}"/>')
            for x in (low, high):
                parts.append(f'<line class="cap" x1="{x}" y1="7.0" x2="{x}" y2="17.0"/>')
        x = rate_x(row.rate)
        if mark == "bar":
            parts.append(
                f'<rect class="mark" x="{_n(_PAD)}" y="7.0" width="{_n(x - _PAD)}" height="10.0"/>'
            )
        else:
            parts.append(f'<circle class="mark" cx="{_n(x)}" cy="{mid}" r="5.0"/>')
    if row.expected is not None:
        x = _n(rate_x(row.expected))
        parts.append(f'<line class="expected" x1="{x}" y1="3.0" x2="{x}" y2="21.0"/>')
    parts.append("</svg>")
    return "".join(parts)


def trend_x(index: int, count: int) -> float:
    """A lone week sits in the middle; otherwise weeks spread edge to edge."""
    if count == 1:
        return (_X0 + _X1) / 2
    return _X0 + index * (_X1 - _X0) / (count - 1)


def trend_y(value: float, y_max: float) -> float:
    top = y_max if y_max > 0 else 1.0
    return _Y1 - value / top * (_Y1 - _Y0)


@dataclass(frozen=True)
class Series:
    label: str
    css_class: str
    values: tuple[float, ...]


def trend_chart(
    weeks: Sequence[int],
    series: Sequence[Series],
    *,
    y_max: float,
    fmt: Callable[[float], str],
    caption: str,
) -> str:
    """Values by pool week: a point per week, joined once there are two."""
    if not weeks:
        return EMPTY
    count = len(weeks)
    top = y_max if y_max > 0 else 1.0
    parts = [
        f'<figure class="trend"><figcaption>{escape(caption)}</figcaption>',
        f'<svg viewBox="0 0 {_n(_TREND_W)} {_n(_TREND_H)}" role="img" '
        f'aria-label="{escape(caption)}">',
    ]
    for value in (0.0, top / 2, top):
        y = _n(trend_y(value, top))
        parts.append(f'<line class="grid" x1="{_n(_X0)}" y1="{y}" x2="{_n(_X1)}" y2="{y}"/>')
        parts.append(
            f'<text class="axis" x="{_n(_X0 - 6)}" y="{y}" text-anchor="end" '
            f'dominant-baseline="middle">{escape(fmt(value))}</text>'
        )
    for index, week in enumerate(weeks):
        parts.append(
            f'<text class="axis" x="{_n(trend_x(index, count))}" y="{_n(_TREND_H - 8)}" '
            f'text-anchor="middle">Wk {week}</text>'
        )
    for line in series:
        points = [
            (trend_x(i, count), trend_y(v, top)) for i, v in enumerate(line.values)
        ]
        if len(points) >= 2:
            joined = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
            parts.append(f'<polyline class="line {line.css_class}" points="{joined}"/>')
        for x, y in points:
            parts.append(f'<circle class="dot {line.css_class}" cx="{_n(x)}" cy="{_n(y)}" r="4.0"/>')
    parts.append("</svg>")
    keys = "".join(
        f'<li><span class="key {line.css_class}"></span>{escape(line.label)}</li>'
        for line in series
    )
    parts.append(f'<ul class="legend">{keys}</ul></figure>')
    return "".join(parts)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_charts.py -v`
Expected: all PASS. If a coordinate assertion fails, recompute it by hand from the constants above before changing either side; the test values are derived from them (e.g. `trend_y(16, 16) = 12.0`, `trend_y(8, 16) = 81.0`, `trend_y(12, 16) = 46.5`).

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/charts.py tests/test_charts.py
git commit -m "feat: add scale-true SVG rate rows and trend charts"
```

---

### Task 4: Dashboard page — skeleton, headline, this week

**Files:**
- Create: `src/pickem/report/results_html.py`
- Modify: `tests/results_helpers.py` (add `assert_well_formed`)
- Test: `tests/test_results_html.py`

**Interfaces:**
- Consumes: `ResultsReport`, `GradedGame`, `Record`, `Strategy`, `record_for` from `pickem.report.results`.
- Produces: `render_results_dashboard(report: ResultsReport, *, generated_at: datetime, imported_weeks: Sequence[int]) -> str`. Task 5 adds sections to it; Task 7 calls it. `tests/results_helpers.assert_well_formed(text: str) -> None`.

- [ ] **Step 1: Add the well-formedness helper** — append to `tests/results_helpers.py`:

```python
from html.parser import HTMLParser

_VOID = {"meta", "br", "hr", "img", "input", "link", "wbr"}


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
        else:
            self.stack.pop()


def assert_well_formed(text: str) -> None:
    """Every element the page opens is closed, in order (SVG uses self-closing tags)."""
    checker = _Balance()
    checker.feed(text)
    checker.close()
    assert not checker.errors, checker.errors[:5]
    assert not checker.stack, f"unclosed: {checker.stack}"
```

(Move the `from html.parser import HTMLParser` line up to the file's import block.)

- [ ] **Step 2: Write the failing tests** — create `tests/test_results_html.py`:

```python
import re
from datetime import UTC, datetime, timedelta

import pytest
from results_helpers import KICK, assert_well_formed, imported_store

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import (
    BoardStanding,
    ResultsReport,
    WeekStanding,
    build_results_report,
    grade_game,
)
from pickem.report.results_html import render_results_dashboard

GENERATED = datetime(2026, 9, 15, 13, tzinfo=UTC)


@pytest.fixture
def report():
    store = imported_store()
    store.append_recommendation_history([
        RecommendationRecord(
            game_id="cfb-2026-02-PSU-at-TEM", sport=Sport.CFB, season=2026, week=2,
            side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.5,
            generated_at=KICK - timedelta(hours=1), source=HISTORY_MONITOR,
        )
    ])
    try:
        yield build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def render(report, weeks=(2,)):
    return render_results_dashboard(report, generated_at=GENERATED, imported_weeks=weeks)


def synthetic_game(index, *, home="H", away="A", our_side=Side.HOME, field=(0, 0),
                   model_side=None):
    game = Game(
        game_id=f"cfb-2026-02-a{index}-at-h{index}", sport=Sport.CFB, season=2026, week=2,
        kickoff_utc=KICK, home_team_id=f"{home}{index}", away_team_id=f"{away}{index}",
        home_score=20, away_score=10,
    )
    history = [] if model_side is None else [
        RecommendationRecord(
            game_id=game.game_id, sport=Sport.CFB, season=2026, week=2, side=model_side,
            tier=Tier.STRONG, edge_points=2.5, generated_at=KICK - timedelta(hours=1),
            source=HISTORY_MONITOR,
        )
    ]
    return grade_game(game=game, pool_week=2, league_spread=3.5, close_spread=None,
                      our_side=our_side, field_home=field[0], field_away=field[1],
                      history=history)


def synthetic_report(games, *, our_points=1, winner_points=2):
    standing = WeekStanding(
        pool_week=2, entrants=3, our_rank=1, our_points=our_points, median_points=1,
        winner_points=winner_points, beat_share=1.0,
        boards=(BoardStanding(Sport.CFB, our_points, 1, winner_points),),
    )
    return ResultsReport(season=2026, pool_week=2, entry_name="Jota", standings=(standing,),
                         week_games=tuple(games), season_games=tuple(games))


def row(text, needle):
    return next(r for r in re.findall(r"<tr>.*?</tr>", text, re.S) if needle in r)


def test_page_is_a_complete_well_formed_document(report):
    text = render(report)
    assert text.startswith("<!doctype html>")
    assert "<title>Pool week 2 · 2026</title>" in text
    assert '<meta name="viewport"' in text
    assert_well_formed(text)


def test_page_is_self_contained(report):
    text = render(report, weeks=(1, 2, 3))
    assert "<script" not in text and "<link" not in text and " src=" not in text
    assert set(re.findall(r'href="([^"]*)"', text)) == {"week-1.html", "week-3.html"}


def test_headline_tiles_carry_this_weeks_standing(report):
    text = render(report)
    headline = text.split('id="this-week"', 1)[0]
    assert '<span class="value">16</span><span class="label">points of 4 games' in headline
    assert '<span class="value">17 of 4</span>' in headline
    assert "CFB</strong> 1 pts · median 0.5 · best 2" in headline


def test_a_tie_for_first_reads_top_score():
    text = render(synthetic_report([synthetic_game(0)], our_points=2, winner_points=2))
    assert '<span class="value">top score</span>' in text


def test_each_board_has_its_records_and_folded_game_table(report):
    text = render(report)
    for board in ("CFB board", "NFL board"):
        assert f"<h3>{board}</h3>" in text
    assert text.count("<details class=\"games\">") == 2
    psu = row(text, "PSU at TEM")
    assert "PSU ✗" in psu
    assert "TEM (coinflip)" in psu
    assert "chip-model" in psu


def test_only_boards_with_games_this_week_appear():
    text = render(synthetic_report([synthetic_game(0)]))
    assert "<h3>CFB board</h3>" in text and "NFL board" not in text


def test_flags_mark_exactly_the_games_that_qualify():
    games = [
        synthetic_game(0, field=(1, 9)),                    # 10% on our side: against
        synthetic_game(1, field=(5, 5)),                    # 50%: not against
        synthetic_game(2, model_side=Side.AWAY),            # model disagrees
        synthetic_game(3, model_side=Side.HOME),            # model agrees
    ]
    text = render(synthetic_report(games))
    assert "chip-field" in row(text, "A0 at H0") and "chip-model" not in row(text, "A0 at H0")
    assert "chip-field" not in row(text, "A1 at H1")
    assert "chip-model" in row(text, "A2 at H2")
    assert "chip-model" not in row(text, "A3 at H3")


def test_kickoffs_show_in_eastern_time(report):
    # KICK is 16:00 UTC on Saturday 2026-09-12, noon EDT.
    assert "Sat 12:00 PM" in row(render(report), "PSU at TEM")


def test_a_board_with_no_decided_model_games_prints_a_dash():
    text = render(synthetic_report([synthetic_game(0)]))
    records = text.split("<h3>CFB board</h3>", 1)[1].split("</table>", 1)[0]
    assert "model</th><td>0–0 (0) = —, n=0</td>" in records


def test_text_from_the_data_is_escaped():
    text = render(synthetic_report([synthetic_game(0, home="<H&", away="A\"")]))
    assert "&lt;H&amp;0" in text
    assert "<H&" not in text
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_results_html.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.report.results_html'`

- [ ] **Step 4: Implement** — create `src/pickem/report/results_html.py`:

```python
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
```

Note the week nav (`imported_weeks`) is added in Task 5; until then `test_page_is_self_contained` fails on its `href` assertion — that is expected, and Step 5 below runs every other test.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_results_html.py -v --deselect tests/test_results_html.py::test_page_is_self_contained`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/report/results_html.py tests/results_helpers.py tests/test_results_html.py
git commit -m "feat: render the dashboard headline and this week's boards"
```

---

### Task 5: Dashboard page — season sections, findings, glossary, week links

**Files:**
- Modify: `src/pickem/report/results_html.py`
- Test: `tests/test_results_html.py`

**Interfaces:**
- Consumes: `charts.RateRow`, `charts.rate_rows`, `charts.Series`, `charts.trend_chart` (Task 3); `results.BACKTEST_TIER_RATES`, `BASELINES`, `AGAINST_FIELD_SHARE`, `GLOSSARY_INTRO`, `STRATEGY_DEFINITIONS`, `clv_summary`, `findings`.
- Produces: the finished `render_results_dashboard` (same signature as Task 4).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_results_html.py`:

```python
from html import escape as html_escape

from pickem.report.results import BASELINES, STRATEGY_DEFINITIONS, Strategy, findings


def test_every_season_section_is_present(report):
    text = render(report)
    for heading in (
        "<h2>Season trend</h2>",
        "<h2>Model by tier</h2>",
        "<h2>Us against baselines</h2>",
        "<h2>Closing-line value and the field</h2>",
        "<h2>What the data says</h2>",
        "<summary>What the terms mean</summary>",
    ):
        assert heading in text
    assert_well_formed(text)


def test_tier_rows_carry_the_nfl_backtest_ticks(report):
    tiers = render(report).split("<h2>Model by tier</h2>", 1)[1].split("</section>", 1)[0]
    for x in ('x1="262.8"', 'x1="224.8"', 'x1="206.0"'):  # 63.7%, 54.2%, 49.5%
        assert f'class="expected" {x}' in tiers
    assert "CFB coinflip" in tiers and "NFL strong" in tiers
    assert "NFL backtest 63.7%" in tiers
    assert "Games with no recommendation stored before kickoff" in tiers


def test_baselines_chart_has_a_row_for_every_strategy(report):
    section = render(report).split("<h2>Us against baselines</h2>", 1)[1].split("</section>")[0]
    for strategy in (Strategy.US, *BASELINES):
        assert f'<div class="rate-label">{html_escape(strategy.value)}<small>' in section


def test_trend_uses_every_imported_week(report):
    trend = render(report).split("<h2>Season trend</h2>", 1)[1].split("</section>", 1)[0]
    assert trend.count("<figure") == 2
    assert ">Wk 2<" in trend
    assert "<polyline" not in trend  # a single week draws points only


def test_findings_are_the_shared_findings_text(report):
    text = render(report)
    result = findings(report.season_games)
    for line in (*result.claims, *result.not_yet):
        assert html_escape(line) in text


def test_glossary_defines_every_strategy(report):
    glossary = render(report).split("<summary>What the terms mean</summary>", 1)[1]
    for strategy, definition in STRATEGY_DEFINITIONS.items():
        assert f"<dt>{html_escape(strategy.value)}</dt><dd>{html_escape(definition)}</dd>" in glossary


def test_week_links_skip_the_page_being_shown(report):
    text = render(report, weeks=(1, 2, 3))
    nav = text.split('<nav class="weeks"', 1)[1].split("</nav>", 1)[0]
    assert '<a href="week-1.html">Week 1</a>' in nav
    assert '<span aria-current="page">Week 2</span>' in nav
    assert '<a href="week-3.html">Week 3</a>' in nav


def test_clv_without_closes_prints_dashes():
    text = render(synthetic_report([synthetic_game(0)]))
    section = text.split("<h2>Closing-line value and the field</h2>", 1)[1].split("</section>")[0]
    assert '<span class="value">—</span><span class="label">mean CLV</span>' in section
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_results_html.py -v`
Expected: the new tests and `test_page_is_self_contained` FAIL (sections and nav not rendered yet).

- [ ] **Step 3: Implement** — in `src/pickem/report/results_html.py`:

Replace the `from pickem.report.results import (...)` block with:

```python
from pickem.report.charts import RateRow, Series, rate_rows, trend_chart
from pickem.report.results import (
    AGAINST_FIELD_SHARE,
    BACKTEST_TIER_RATES,
    BASELINES,
    GLOSSARY_INTRO,
    STRATEGY_DEFINITIONS,
    GradedGame,
    Record,
    ResultsReport,
    Strategy,
    clv_summary,
    findings,
    record_for,
)
```

In `render_results_dashboard`, extend `body` after `_this_week(report),`:

```python
        _trend(report),
        _tiers(report),
        _baselines(report),
        _clv_and_field(report),
        _findings(report),
        _glossary(),
        _weeks_nav(report, imported_weeks),
```

Append to `_CSS` (inside the triple-quoted string, before its closing `"""`):

```css
figure{margin:12px 0}
figcaption{margin-bottom:6px}
.rate-row{display:grid;grid-template-columns:minmax(8.5rem,11rem) 1fr;gap:10px;
  align-items:center;padding:3px 0}
.rate-label{font-size:14px;line-height:1.25}
.rate-label small{display:block;color:var(--muted);font-size:11.5px;
  font-variant-numeric:tabular-nums}
.rate-axis svg text{font-size:13px;fill:var(--muted)}
.rates svg,.trend svg{display:block;width:100%;height:auto}
.trend svg{max-width:560px}
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
.claims li{margin:4px 0}
dl dt{font-weight:600;margin-top:8px}
dl dd{margin:0;color:var(--muted)}
.weeks ul{list-style:none;display:flex;flex-wrap:wrap;gap:6px 14px;padding:0}
.weeks [aria-current]{font-weight:600}
```

Append these functions at the end of the module:

```python
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
            rows.append(RateRow(
                f"{sport.value.upper()} {tier.value}",
                f"{_record(record)} · NFL backtest {expected:.1%}",
                record.rate,
                record.interval,
                expected,
            ))
    chart = rate_rows(
        rows, mark="bar",
        caption="Model hit rate by tier, season to date. Whisker: 95% interval. "
        "Tick: NFL backtest, 2020–2025.",
    )
    note = (
        '<p class="note">Games with no recommendation stored before kickoff (excluded from '
        f"model grading): {report.unknown_model_games}</p>"
    )
    return _section("tiers", "Model by tier", chart + note)


def _baselines(report: ResultsReport) -> str:
    rows = []
    for strategy in (Strategy.US, *BASELINES):
        record = record_for(report.season_games, strategy)
        rows.append(RateRow(strategy.value, _record(record), record.rate, record.interval))
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
        f"<p>Against the field (≤{AGAINST_FIELD_SHARE:.0%} of other entrants on our side): "
        f"{escape(_record(against))}</p>"
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
            f"<details><summary>Not distinguishable yet ({len(result.not_yet)})</summary>"
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
        f'<li><span aria-current="page">Week {week}</span></li>'
        if week == report.pool_week
        else f'<li><a href="week-{week}.html">Week {week}</a></li>'
        for week in imported_weeks
    )
    return f'<nav class="weeks" aria-label="Pool weeks"><h2>Other weeks</h2><ul>{items}</ul></nav>'
```

- [ ] **Step 4: Run the renderer tests**

Run: `uv run pytest tests/test_results_html.py tests/test_charts.py -v`
Expected: all PASS, including `test_page_is_self_contained`.

- [ ] **Step 5: Look at it once** — render the fixture page and open it in a browser at phone width and laptop width, light and dark:

```bash
uv run python - <<'EOF'
from datetime import UTC, datetime
import sys; sys.path.insert(0, "tests")
from results_helpers import imported_store
from pickem.report.results import build_results_report
from pickem.report.results_html import render_results_dashboard
s = imported_store()
r = build_results_report(s, season=2026, pool_week=2, entry_name="Jota")
open("/tmp/claude-dashboard-preview.html", "w").write(
    render_results_dashboard(r, generated_at=datetime.now(tz=UTC), imported_weeks=[1, 2]))
EOF
```

Check: no horizontal page scroll at 400 px (only the game table scrolls); chart labels readable; whiskers and ticks visible in both themes. Fix anything visibly broken in `_CSS` only, re-run the tests, then continue.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/report/results_html.py tests/test_results_html.py
git commit -m "feat: render the dashboard's season charts, findings and week links"
```

---

### Task 6: Dashboard link in the results DM

**Files:**
- Modify: `src/pickem/notify/discord_dm.py:45-83`
- Test: `tests/test_discord_dm.py`

**Interfaces:**
- Produces: `build_results_embed(report: ResultsReport, report_path: Path, *, dashboard_url: str | None = None) -> discord.Embed`. `dashboard_url` is a base ending in `/` (as `config.dashboard_url()` returns).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_discord_dm.py`:

```python
def test_embed_links_the_weeks_dashboard_page_when_given_a_url(report):
    embed = build_results_embed(
        report, Path("r.md"), dashboard_url="https://sandbox.tail750bff.ts.net/pickem/"
    )
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Dashboard"] == "https://sandbox.tail750bff.ts.net/pickem/week-2.html"
    assert embed.fields[-1].name == "Full report"


def test_embed_has_no_dashboard_field_without_a_url(report):
    embed = build_results_embed(report, Path("r.md"))
    assert "Dashboard" not in [field.name for field in embed.fields]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_discord_dm.py -v`
Expected: the first new test FAILS with `TypeError: build_results_embed() got an unexpected keyword argument 'dashboard_url'`.

- [ ] **Step 3: Implement** — change the signature to:

```python
def build_results_embed(
    report: ResultsReport, report_path: Path, *, dashboard_url: str | None = None
) -> discord.Embed:
```

and, just before the existing `embed.add_field(name="Full report", ...)` line, add:

```python
    if dashboard_url is not None:
        embed.add_field(
            name="Dashboard",
            value=_cap(f"{dashboard_url}week-{report.pool_week}.html"),
            inline=False,
        )
```

- [ ] **Step 4: Run the DM tests**

Run: `uv run pytest tests/test_discord_dm.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/notify/discord_dm.py tests/test_discord_dm.py
git commit -m "feat: link the week's dashboard page from the results DM"
```

---

### Task 7: Write the pages from the results commands

**Files:**
- Modify: `src/pickem/cli.py` (imports; `_write_results_report` neighbourhood ~line 456; `_notify_results` ~477; `import_results_cmd` ~502; `results_report_cmd` ~555)
- Test: `tests/test_results_cli.py`

**Interfaces:**
- Consumes: `render_results_dashboard` (Tasks 4–5), `config.dashboard_dir()`, `config.dashboard_url()` (Task 2), `build_results_embed(..., dashboard_url=...)` (Task 6), `Store.pool_weeks(season) -> list[int]` (existing, ascending).
- Produces: `--dashboard-dir PATH` on `import-results` and `results-report`; exit code 3 when the dashboard is not written.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_results_cli.py`:

```python
@pytest.fixture
def embeds(monkeypatch):
    captured = []

    async def fake_send(embed, *, token, owner_id):
        captured.append(embed)

    monkeypatch.setattr("pickem.cli.send_owner_dm", fake_send)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    return captured


def test_import_writes_the_week_page_and_the_index(workspace, tmp_path):
    db, results = workspace
    dash = tmp_path / "dash"
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash))
    assert result.exit_code == 0, result.output
    page = (dash / "week-2.html").read_text()
    assert page.startswith("<!doctype html>")
    assert (dash / "index.html").read_text() == page
    assert not list(dash.glob(".*.tmp"))
    assert "dashboard written to" in result.output


def test_dashboard_defaults_to_the_configured_directory(workspace, tmp_path):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    assert (tmp_path / "dashboard" / "week-2.html").exists()  # conftest's PICKEM_DASHBOARD_DIR


def test_rebuilding_an_older_week_leaves_the_index_alone(workspace, tmp_path, monkeypatch):
    db, results = workspace
    dash = tmp_path / "dash"
    assert invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash)).exit_code == 0
    (dash / "index.html").write_text("latest week")
    monkeypatch.setattr(Store, "pool_weeks", lambda self, season: [2, 3])
    result = runner.invoke(app, [
        "results-report", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--out-dir", str(results), "--dashboard-dir", str(dash),
    ])
    assert result.exit_code == 0, result.output
    assert (dash / "index.html").read_text() == "latest week"
    assert 'href="week-3.html"' in (dash / "week-2.html").read_text()


def test_dm_links_the_dashboard_when_the_url_is_set(workspace, embeds, monkeypatch, tmp_path):
    db, results = workspace
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem")
    assert invoke_import(db, results).exit_code == 0
    fields = {field.name: field.value for field in embeds[0].fields}
    assert fields["Dashboard"] == "https://sandbox.tail750bff.ts.net/pickem/week-2.html"


def test_dm_has_no_link_when_the_url_is_unset(workspace, embeds):
    db, results = workspace
    assert invoke_import(db, results).exit_code == 0
    assert "Dashboard" not in [field.name for field in embeds[0].fields]


def test_a_render_failure_keeps_the_report_sends_the_dm_and_exits_3(
    workspace, embeds, monkeypatch
):
    db, results = workspace
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem/")

    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.cli.render_results_dashboard", boom)
    result = invoke_import(db, results)
    assert result.exit_code == 3, result.output
    assert "dashboard not written: render broke" in result.output
    assert (results / "week2-report.md").exists()
    assert len(embeds) == 1
    assert "Dashboard" not in [field.name for field in embeds[0].fields]
    with Store(db) as store:
        assert store.pool_weeks(2026) == [2]


def test_an_unwritable_dashboard_dir_exits_3_and_keeps_the_report(workspace, tmp_path):
    db, results = workspace
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the directory should be")
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(blocker))
    assert result.exit_code == 3, result.output
    assert "dashboard not written" in result.output
    assert (results / "week2-report.md").exists()


def test_dashboard_write_is_logged(workspace, tmp_path):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    logger.complete()
    rows = [
        json.loads(line)
        for line in (tmp_path / "pickem-logs" / "pickem.jsonl").read_text().splitlines()
        if line.strip()
    ]
    written = [row for row in rows if row["event"] == "dashboard_written"]
    assert len(written) == 1
    assert written[0]["pool_week"] == 2
    assert written[0]["index_updated"] is True
    assert not any(row["event"] == "dashboard_write_failed" for row in rows)
```

Add `from loguru import logger` to the test file's imports (`json` is already imported). The log directory is the one `tests/conftest.py` sets. If the JSONL nests bound fields rather than putting them at the top level, open one line of the file and adjust the two field lookups to match; the `event` key is top level, as `tests/test_calibration.py` shows.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_results_cli.py -v`
Expected: the new tests FAIL (`No such option: --dashboard-dir`, missing files).

- [ ] **Step 3: Implement** — in `src/pickem/cli.py`:

Add the import beside the Markdown renderer import:

```python
from pickem.report.results_html import render_results_dashboard
```

Add after `_write_results_report`:

```python
def _write_atomic(path: Path, text: str) -> None:
    """Write beside the target, then rename, so a reader never sees half a page."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_dashboard(store: Store, report: ResultsReport, dashboard_dir: Path) -> Path | None:
    """Write week-N.html, and index.html when N is the latest week; ``None`` on failure.

    The import and the Markdown are already saved, so a failure here is logged
    and reported, never raised: the DM still goes out before the command exits 3.
    """
    try:
        weeks = store.pool_weeks(report.season)
        page = render_results_dashboard(
            report, generated_at=datetime.now(tz=UTC), imported_weeks=weeks
        )
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        path = dashboard_dir / f"week-{report.pool_week}.html"
        _write_atomic(path, page)
        index_updated = report.pool_week == weeks[-1]
        if index_updated:
            _write_atomic(dashboard_dir / "index.html", page)
    except Exception as exc:  # a render bug or a filesystem error alike must not stop the DM
        logger.bind(
            event="dashboard_write_failed",
            pool_week=report.pool_week,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        ).error(f"dashboard not written: {exc}")
        typer.secho(f"dashboard not written: {exc}", fg="red", err=True)
        return None
    logger.bind(
        event="dashboard_written",
        pool_week=report.pool_week,
        path=str(path),
        bytes=len(page.encode("utf-8")),
        index_updated=index_updated,
    ).info(f"dashboard written to {path}")
    typer.echo(f"dashboard written to {path}")
    return path
```

Change `_notify_results` to take whether the page exists, and pass the link:

```python
def _notify_results(report: ResultsReport, path: Path, *, dashboard_written: bool) -> None:
    """Send the DM; on failure keep everything and exit 2 so the miss is visible."""
    try:
        asyncio.run(
            send_owner_dm(
                build_results_embed(
                    report,
                    path,
                    dashboard_url=config.dashboard_url() if dashboard_written else None,
                ),
                token=config.discord_bot_token(),
                owner_id=config.discord_owner_id(),
            )
        )
```

(the rest of `_notify_results` is unchanged).

In **both** `import_results_cmd` and `results_report_cmd`, add the option after `out_dir`:

```python
    dashboard_dir: Path = typer.Option(
        None, help="Default: $PICKEM_DASHBOARD_DIR or ~/.local/share/pickem/dashboard"
    ),
```

In `import_results_cmd`, replace the tail from `report, path = _write_results_report(...)` with:

```python
            report, path = _write_results_report(store, season, pool_week, entry_name, out_dir)
            dashboard = _write_dashboard(store, report, dashboard_dir or config.dashboard_dir())
        if notify:
            _notify_results(report, path, dashboard_written=dashboard is not None)
        if dashboard is None:
            raise typer.Exit(code=3)
```

In `results_report_cmd`, replace its tail the same way:

```python
            report, path = _write_results_report(store, season, week, entry_name, out_dir)
            dashboard = _write_dashboard(store, report, dashboard_dir or config.dashboard_dir())
        if notify:
            _notify_results(report, path, dashboard_written=dashboard is not None)
        if dashboard is None:
            raise typer.Exit(code=3)
```

- [ ] **Step 4: Run the CLI tests and the whole suite**

Run: `uv run pytest tests/test_results_cli.py -v && uv run pytest -q`
Expected: all PASS. Existing tests (e.g. `test_import_results_sends_the_dm_by_default`) still pass because conftest points the dashboard at a temp directory.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/cli.py tests/test_results_cli.py
git commit -m "feat: write the results dashboard from import-results and results-report"
```

---

### Task 8: Real weeks, runbook, and going live

**Files:**
- Modify: `tests/test_results_real_pages.py`
- Create: `docs/runbooks/dashboard.md`
- Modify: `README.md` (results section, near line 254)

**Interfaces:**
- Consumes: everything above; `Store(path, read_only=True)`.

- [ ] **Step 1: Write the real-data test** — append to `tests/test_results_real_pages.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from results_helpers import assert_well_formed

from pickem.config import DEFAULT_ENTRY_NAME
from pickem.report.results import build_results_report
from pickem.report.results_html import render_results_dashboard
from pickem.store.db import Store

LIVE_DB = Path("data/pickem.duckdb")


def test_every_imported_real_week_renders_a_complete_page():
    if not LIVE_DB.exists():
        pytest.skip("live database is local-only")
    try:
        store = Store(LIVE_DB, read_only=True)
    except duckdb.Error as exc:
        pytest.skip(f"live database is busy: {exc}")
    with store:
        weeks = store.pool_weeks(2026)
        assert weeks, "no imported pool weeks in the live database"
        for week in weeks:
            report = build_results_report(
                store, season=2026, pool_week=week, entry_name=DEFAULT_ENTRY_NAME
            )
            page = render_results_dashboard(
                report, generated_at=datetime.now(tz=UTC), imported_weeks=weeks
            )
            assert_well_formed(page)
            for heading in ("This week", "Season trend", "Model by tier", "What the data says"):
                assert heading in page, (week, heading)
```

Merge the new imports into the file's existing import block.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_results_real_pages.py -v`
Expected: PASS here (the NAS pages and live DB exist), or SKIP with a stated reason. A skip for "busy" means a writer holds the DB; re-run when the bot is idle rather than accepting the skip.

- [ ] **Step 3: Write the runbook** — create `docs/runbooks/dashboard.md`:

````markdown
# Results dashboard

The Tuesday results job writes one page per pool week to the dashboard
directory (`$PICKEM_DASHBOARD_DIR`, default `~/.local/share/pickem/dashboard`):
`week-N.html` for each week and `index.html` for the latest. Tailscale serves
that directory to the owner's own devices only.

Address: `https://sandbox.tail750bff.ts.net/pickem/`

## Add a device

Install Tailscale on it and sign in with the same account as this machine.
Nothing else is needed; the page is not reachable from devices outside the
tailnet.

## Turn serving on (once)

```
tailscale serve --bg --set-path /pickem ~/.local/share/pickem/dashboard
tailscale serve status
```

Tailscale keeps this across reboots. Never use `tailscale funnel` for this
directory: Funnel publishes to the internet, and the page carries every
entrant's results.

## Link the page from the Tuesday DM

Add to `.env`:

```
PICKEM_DASHBOARD_URL=https://sandbox.tail750bff.ts.net/pickem/
```

Without it the DM is sent as before, with no link.

## Rebuild a page

```
uv run pickem results-report --season 2026 --pool-week N
```

This rewrites `week-N.html` (and the Markdown), and `index.html` only when N
is the latest imported week. Use it after a `dashboard not written` failure:
the Tuesday job exits 3 in that case, and its Wednesday retry does not redo an
imported week.

## Turn serving off

```
tailscale serve --set-path /pickem off
```

## When the page does not load

- This machine is off or asleep, or Tailscale is disconnected on it or on the
  viewing device (`tailscale status` on each).
- `tailscale serve status` shows no `/pickem` entry: turn serving on again.
- The page is stale: check the results job's log for `dashboard_write_failed`,
  then rebuild the page.

The DM and the Markdown report on the NAS stay available either way.
````

- [ ] **Step 4: Point the README at it** — in `README.md`'s results section (the paragraph that introduces `/mnt/nas/Betting/pickem/results/week<N>.html`, near line 254), add after that paragraph:

```markdown
Each import also writes a visual dashboard page, served privately to the
owner's devices over Tailscale at `https://sandbox.tail750bff.ts.net/pickem/`.
Setup, rebuilding and troubleshooting are in `docs/runbooks/dashboard.md`.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_results_real_pages.py docs/runbooks/dashboard.md README.md
git commit -m "docs: add the results dashboard runbook and test real weeks"
```

- [ ] **Step 6: Go live — ask the owner before each of these, since they change running configuration**

1. Build the pages for every imported week (ascending, so the last write owns `index.html`):
   ```bash
   weeks=$(uv run python -c "from pickem.store.db import Store; print(*Store('data/pickem.duckdb', read_only=True).pool_weeks(2026))")
   for week in $weeks; do
       uv run pickem results-report --season 2026 --pool-week "$week"
   done
   ls -la ~/.local/share/pickem/dashboard
   ```
2. Serve it: `tailscale serve --bg --set-path /pickem ~/.local/share/pickem/dashboard`, then `tailscale serve status` and `curl -sI https://sandbox.tail750bff.ts.net/pickem/ | head -1` (expect `HTTP/2 200`).
3. Append `PICKEM_DASHBOARD_URL=https://sandbox.tail750bff.ts.net/pickem/` to `.env` (the owner's file: show the line and get a yes first).
4. Ask the owner to open the address on the laptop and phone, in light and dark mode, and confirm it reads well. That is the spec's manual acceptance; do not claim it on their behalf.
