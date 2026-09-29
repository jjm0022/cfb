// Season tab: how we have stood against the field, week by week, through the selected week.
(function (P) {
  "use strict";
  const { h, card, fmt } = P.ui;

  P.tabs.season = function (view) {
    const standings = view.data.standings.filter((s) => s.week <= view.state.week);
    const weeks = standings.map((s) => s.week);
    const pick = (week) => P.app.set({ week, tab: "week" });
    return [
      card("Points by week", P.charts.trend(weeks, [
        { key: "us", label: "Us", values: standings.map((s) => s.points) },
        { key: "median", label: "Field median", values: standings.map((s) => s.median) },
        { key: "winner", label: "Winner", values: standings.map((s) => s.winner) },
      ], { caption: "Tap a week to open it.", label: "Points by week", format: (v) => String(Math.round(v)), onPick: pick })),
      card("Share of the field we beat", P.charts.trend(weeks, [
        { key: "us", label: "Beaten", values: standings.map((s) => s.beat_share) },
      ], { caption: "50% is the middle of the pool.", label: "Share of the field beaten", max: 1,
        format: (v) => fmt.pct(v, 0), onPick: pick })),
      h("p", { class: "note wide" }, "Standings are whole-week facts; the filters do not change them."),
    ];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
