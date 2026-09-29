// SVG charts. Text that must stay legible on a phone is HTML beside the drawing.
(function (P) {
  "use strict";
  const { h, s, fmt } = P.ui;
  const W = 520;

  function legend(items) {
    return h("ul", { class: "legend" }, items.map(([key, text]) =>
      h("li", null, h("span", { class: "key s-" + key }), text)));
  }

  function lineMove(g) {
    if (!g.lines.length) return h("p", { class: "empty" }, "No line history");
    const series = [
      ["us", "US books", g.lines.filter((p) => p.source === "us")],
      ["pinnacle", "Pinnacle", g.lines.filter((p) => p.source === "pinnacle")],
    ].filter(([, , points]) => points.length);
    const H = 170, L = 10, R = 10, T = 12, B = 12;
    const times = g.lines.map((p) => Date.parse(p.at));
    const [lo, hi] = P.scales.extent(g.lines.map((p) => p.spread).concat([g.line]), 0.5);
    const x = P.scales.linear(Math.min(...times), Math.max(...times), L, W - R);
    const y = P.scales.linear(lo, hi, T, H - B);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Line movement" },
      s("line", { class: "ref", x1: L, x2: W - R, y1: y(g.line), y2: y(g.line) }),
      series.map(([key, , points]) => [
        points.length > 1 && s("polyline", {
          class: "line s-" + key,
          points: points.map((p) => `${x(Date.parse(p.at))},${y(p.spread)}`).join(" "),
        }),
        points.map((p) => s("circle", { class: "dot s-" + key, cx: x(Date.parse(p.at)), cy: y(p.spread), r: 4.5 },
          s("title", null, `${fmt.time(p.at)}: ${fmt.homeLine(g, p.spread)}`))),
      ]));
    const items = series.map(([key, label, points]) => [key,
      `${label}: opened ${fmt.spread(points[0].spread)}, last ${fmt.spread(points[points.length - 1].spread)}`]);
    items.push(["cbs", `CBS line ${fmt.spread(g.line)} (dashed)`]);
    return h("figure", null,
      h("figcaption", null, `Spreads for ${g.home}; lower means ${g.home} more favored.`),
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
