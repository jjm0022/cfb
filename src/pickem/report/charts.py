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
    return (
        '<div class="rate-row rate-axis"><div></div>'
        '<div class="rate-ticks" aria-hidden="true">'
        '<span class="t0">0%</span><span class="t50">50%</span><span class="t100">100%</span>'
        "</div></div>"
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
    """Values by pool week: a point per week, joined once there are two.

    Series are painted in reverse of the given order, so the first series is
    drawn on top when two lines share a value; the legend keeps the given
    order regardless.
    """
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
    for line in reversed(series):
        points = [
            (trend_x(i, count), trend_y(v, top)) for i, v in enumerate(line.values)
        ]
        if len(points) >= 2:
            joined = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
            parts.append(f'<polyline class="line {line.css_class}" points="{joined}"/>')
        for x, y in points:
            parts.append(
                f'<circle class="dot {line.css_class}" cx="{_n(x)}" cy="{_n(y)}" r="4.0"/>'
            )
    parts.append("</svg>")
    keys = "".join(
        f'<li><span class="key {line.css_class}"></span>{escape(line.label)}</li>'
        for line in series
    )
    parts.append(f'<ul class="legend">{keys}</ul></figure>')
    return "".join(parts)
