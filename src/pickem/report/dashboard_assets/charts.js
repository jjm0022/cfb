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

  P.charts = { W, legend, lineMove };
})(globalThis.Pickem = globalThis.Pickem || {});
