// SVG charts. Text that must stay legible on a phone is HTML beside the drawing, or SVG text in a
// drawing whose width is close to a phone's, so it scales by about one.
(function (P) {
  "use strict";
  const { h, s, fmt } = P.ui;
  const W = 520;

  function legend(items) {
    return h("ul", { class: "legend" }, items.map(([key, text]) =>
      h("li", null, h("span", { class: "key s-" + key }), text)));
  }

  // The spread chart. US books over time, drawn against bands that show the pick the model makes at
  // every level the line could sit at, so each move shows what it does to the pick. Its own width is
  // close to a phone's, so its 12-unit labels stay about 12px there.
  const SC = { W: 360, T: 8, PLOT: 230, B: 26, TICK_GAP: 16, MIN_LABEL: 14, DAY: 864e5 };
  const EASTERN = { timeZone: "America/New_York" };
  const dayName = new Intl.DateTimeFormat("en-US", { ...EASTERN, weekday: "short" });
  const clock = new Intl.DateTimeFormat("en-US", { ...EASTERN, weekday: "short", hour: "numeric", minute: "2-digit" });

  // A generous guess at a label's width in 12-unit text, so the margins fit the longest one.
  function labelWidth(text) {
    return [...text].reduce((w, c) =>
      w + (/[A-Z]/.test(c) ? 8.4 : /[a-z]/.test(c) ? 6.8 : /[0-9]/.test(c) ? 7.6 : c === " " || c === "." ? 4 : 10), 0);
  }

  // Each quote holds until the next check, and the last one until the end of the axis.
  function stepPath(points, x, y, end) {
    const out = [];
    points.forEach((p, i) => {
      const at = x(Date.parse(p.at));
      if (i) out.push([at, y(points[i - 1].spread)]);
      out.push([at, y(p.spread)]);
    });
    out.push([x(end), y(points[points.length - 1].spread)]);
    return out;
  }

  // Where the "CBS line" label goes: at the right or left end of the dashed line, above it or below,
  // whichever spot no line or pick-change marker runs through (the first spot when none is clear).
  // Every path here is made of horizontal and vertical runs, so a run crosses a box exactly when
  // their extents overlap.
  function cbsLabelSpot(paths, lineY, left, right) {
    const w = labelWidth("CBS line");
    const spots = [[right, "end", -5], [left, "start", -5], [right, "end", 15], [left, "start", 15]]
      .map(([x, anchor, dy]) => {
        const x0 = anchor === "end" ? x - w : x;
        return { x, anchor, y: lineY + dy, box: [x0 - 2, x0 + w + 2, lineY + dy - 11, lineY + dy + 3] };
      });
    const crosses = (a, b, [x0, x1, y0, y1]) =>
      Math.max(a[0], b[0]) >= x0 && Math.min(a[0], b[0]) <= x1 && Math.max(a[1], b[1]) >= y0 && Math.min(a[1], b[1]) <= y1;
    const clear = (spot) => !paths.some((path) => path.some((pt, i) => i > 0 && crosses(path[i - 1], pt, spot.box)));
    return spots.find(clear) || spots[0];
  }

  // Day names at Eastern midnights; for a span under a day, its start and end times.
  function timeAxis(start, end, x, left, right, baseline, top, bottom) {
    if (end <= start) return s("text", { class: "x-tick", x: x(start), y: baseline, "text-anchor": "middle" }, clock.format(start));
    if (end - start < SC.DAY) {
      return [
        s("text", { class: "x-tick", x: left, y: baseline, "text-anchor": "start" }, clock.format(start)),
        s("text", { class: "x-tick", x: right, y: baseline, "text-anchor": "end" }, clock.format(end)),
      ];
    }
    const midnights = P.scales.etMidnights(start, end);
    const every = Math.max(1, Math.ceil(32 / (x(start + SC.DAY) - x(start))));  // keep the names apart
    return midnights.map((m, i) => [
      s("line", { class: "day-grid", x1: x(m), x2: x(m), y1: top, y2: bottom }),
      i % every === 0 && s("text", { class: "x-tick", x: x(m) + 3, y: baseline, "text-anchor": "start" }, dayName.format(m)),
    ]);
  }

  // opts: thresholds (this board's tier settings), marks (pick changes: {at, label}), and now (the
  // viewer's clock, for a game whose pick can still change; a Results game passes none).
  function lineMove(g, opts) {
    const { thresholds, marks = [], now } = opts;
    if (!g.lines.length) return h("p", { class: "empty" }, "No line history");
    const series = [
      ["us", "US books", g.lines.filter((p) => p.source === "us")],
      ["pinnacle", "Pinnacle (reference)", g.lines.filter((p) => p.source === "pinnacle")],
    ].filter(([, , points]) => points.length);
    const times = g.lines.map((p) => Date.parse(p.at)).concat(marks.map((m) => Date.parse(m.at)));
    const start = Math.min(...times);
    // To now, or to kickoff once locked; never short of the data, whatever the viewer's clock says.
    const end = Math.max(P.filters.chartEnd(g, now), ...times);
    // The CBS line ± 2.5, so both strong bands show, and every quote; widened to the edges of the
    // half-point levels, so each tick sits in the middle of its level.
    const spreads = g.lines.map((p) => p.spread);
    const lo = Math.floor(Math.min(g.line - 2.5, ...spreads) * 2) / 2 - 0.25;
    const hi = Math.ceil(Math.max(g.line + 2.5, ...spreads) * 2) / 2 + 0.25;
    const ticks = P.scales.spreadTicks(lo, hi, Math.floor(SC.PLOT / SC.TICK_GAP));
    const bands = P.scales.pickBands(g.line, thresholds, lo, hi, g.home, g.away);
    const { W: CW, T, PLOT } = SC;
    const L = Math.ceil(Math.max(...ticks.map((v) => labelWidth(fmt.homeLine(g, v))))) + 8;
    const R = Math.ceil(Math.max(...bands.map((b) => labelWidth(b.label)))) + 10;
    const bottom = T + PLOT;
    const x = P.scales.linear(start, end, L, CW - R);
    // The lowest spread is drawn at the top: the more the home team is favored, the higher its line.
    const y = P.scales.linear(lo, hi, T, bottom);
    const steps = series.map(([key, , points]) => [key, points, stepPath(points, x, y, end)]);
    const markXs = marks.map((m) => x(Date.parse(m.at)));
    const cbs = cbsLabelSpot(steps.map(([, , path]) => path).concat(markXs.map((mx) => [[mx, T], [mx, bottom]])),
      y(g.line), L + 4, CW - R - 4);
    const svg = s("svg", {
      viewBox: `0 0 ${CW} ${bottom + SC.B}`, role: "img",
      "aria-label": "US books spread over time, against the pick the model makes at each level",
    },
      bands.map((b) => s("rect", {
        class: "band band-" + b.tier, x: L, y: y(b.from), width: CW - L, height: y(b.to) - y(b.from),
      })),
      // A gap where time ends, so the shading under the band labels doesn't read as more chart.
      s("line", { class: "plot-end", x1: CW - R + 1, x2: CW - R + 1, y1: T, y2: bottom }),
      timeAxis(start, end, x, L, CW - R, bottom + 18, T, bottom),
      ticks.map((v) => s("text", { class: "y-tick", x: L - 6, y: y(v) + 4, "text-anchor": "end" }, fmt.homeLine(g, v))),
      bands.map((b) => y(b.to) - y(b.from) >= SC.MIN_LABEL &&
        s("text", { class: "band-label", x: CW - R + 6, y: (y(b.from) + y(b.to)) / 2 + 4 }, b.label)),
      s("line", { class: "ref", x1: L, x2: CW - R, y1: y(g.line), y2: y(g.line) }),
      s("text", { class: "cbs-label", x: cbs.x, y: cbs.y, "text-anchor": cbs.anchor }, "CBS line"),
      marks.map((m, i) => s("line", { class: "mark-change", x1: markXs[i], x2: markXs[i], y1: T, y2: bottom },
        s("title", null, `${fmt.time(m.at)}: ${m.label}`))),
      steps.slice().reverse().map(([key, points, path]) => [
        s("polyline", { class: "line s-" + key, points: path.map((pt) => pt.join(",")).join(" ") }),
        points.map((p) => s("circle", {
          class: "dot s-" + key, cx: x(Date.parse(p.at)), cy: y(p.spread), r: key === "us" ? 2.5 : 2,
        }, s("title", null, `${fmt.time(p.at)}: ${fmt.homeLine(g, p.spread)}`))),
      ]));
    const items = series.map(([key, label]) => [key, label]);
    items.push(["cbs", "CBS line (dashed)"]);
    if (marks.length) items.push(["change", "Pick changed (dotted)"]);
    const pinnacle = series.some(([key]) => key === "pinnacle")
      ? " Pinnacle is shown for reference; the model doesn't use it." : "";
    return h("figure", { class: "spread-chart" },
      h("figcaption", null, `US books vs the CBS line (${fmt.homeLine(g, g.line)}). Each band is the pick the ` +
        `model makes when the US books line sits there.${pinnacle}`),
      svg, legend(items));
  }

  function rateRows(rows, onPick) {
    if (!rows.some((row) => row.games.length)) return h("p", { class: "empty" }, "No games yet");
    const RH = 28, x = P.scales.linear(0, 1, 8, W - 8);
    return h("div", { class: "rates" },
      h("div", { class: "rate-axis", "aria-hidden": "true" }, h("span"),
        h("span", { class: "ticks" }, h("span", null, "0%"), h("span", null, "50%"), h("span", null, "100%"))),
      rows.map((row) => {
        const mark = P.scales.rateMark(row.record, x);
        const svg = s("svg", { viewBox: `0 0 ${W} ${RH}`, "aria-hidden": "true" },
          s("line", { class: "track", x1: x(0), x2: x(1), y1: RH / 2, y2: RH / 2 }),
          s("line", { class: "ref", x1: x(0.5), x2: x(0.5), y1: 2, y2: RH - 2 }),
          row.expected !== undefined && row.expected !== null &&
            s("line", { class: "expected", x1: x(row.expected), x2: x(row.expected), y1: 4, y2: RH - 4 }),
          mark && [
            s("line", { class: "whisker", x1: mark.lo, x2: mark.hi, y1: RH / 2, y2: RH / 2 }),
            s("line", { class: "cap", x1: mark.lo, x2: mark.lo, y1: RH / 2 - 6, y2: RH / 2 + 6 }),
            s("line", { class: "cap", x1: mark.hi, x2: mark.hi, y1: RH / 2 - 6, y2: RH / 2 + 6 }),
            s("circle", { class: "mark", cx: mark.x, cy: RH / 2, r: 7 }),
          ]);
        return h("button", {
          class: "rate-row", type: "button", disabled: !row.games.length,
          "aria-label": `${row.label}: ${P.stats.recordText(row.record)}. Show games.`,
          onclick: () => onPick(row),
        },
          h("span", { class: "rate-label" }, row.label,
            h("small", null, P.stats.recordText(row.record), row.note ? ` · ${row.note}` : "")),
          svg);
      }));
  }

  function trend(weeks, series, opts) {
    if (!weeks.length) return h("p", { class: "empty" }, "No games yet");
    const H = 190, L = 44, R = 14, T = 12, B = 30;
    const x = weeks.length === 1 ? () => (L + W - R) / 2 : P.scales.linear(weeks[0], weeks[weeks.length - 1], L, W - R);
    const max = opts.max || P.scales.niceMax(series.flatMap((se) => se.values));
    const y = P.scales.linear(0, max, H - B, T);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label },
      [0, max / 2, max].map((v) => [
        s("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }),
        s("text", { class: "axis", x: L - 8, y: y(v) + 5, "text-anchor": "end" }, opts.format(v)),
      ]),
      weeks.map((w) => s("text", { class: "axis", x: x(w), y: H - 8, "text-anchor": "middle" }, `W${w}`)),
      series.slice().reverse().map((se) => [
        weeks.length > 1 && s("polyline", {
          class: "line s-" + se.key, points: weeks.map((w, i) => `${x(w)},${y(se.values[i])}`).join(" "),
        }),
        weeks.map((w, i) => s("circle", { class: "dot s-" + se.key, cx: x(w), cy: y(se.values[i]), r: 5 })),
      ]),
      weeks.map((w, i) => s("rect", {
        class: "hit", x: x(w) - 16, y: T, width: 32, height: H - B - T, tabindex: 0, role: "button",
        "aria-label": `Week ${w}: ` + series.map((se) => `${se.label} ${opts.format(se.values[i])}`).join(", "),
        onclick: () => opts.onPick(w),
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onPick(w); } },
      }, s("title", null, `Week ${w}: ` + series.map((se) => `${se.label} ${opts.format(se.values[i])}`).join(", ")))));
    return h("figure", { class: "trend" }, h("figcaption", null, opts.caption), svg,
      legend(series.map((se) => [se.key, se.label])));
  }

  P.charts = { W, legend, lineMove, rateRows, trend };
})(globalThis.Pickem = globalThis.Pickem || {});
