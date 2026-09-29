// Season tab: how we have stood against the field, week by week, through the selected week.
(function (P) {
  "use strict";
  const { h, card, fmt } = P.ui;

  // A tapped week's values as text, so they can be read on a phone; defaults to the last week shown.
  function readout(week, series, format, index) {
    return h("p", { class: "readout" },
      `Week ${week}: ` + series.map((se) => `${se.label} ${format(se.values[index])}`).join(" · "),
      " ",
      h("button", { class: "linkish", type: "button", onclick: () => P.app.set({ week, tab: "week" }) },
        `Open week ${week}`));
  }

  function chart(title, weeks, series, opts) {
    if (!weeks.length) return card(title, P.charts.trend(weeks, series, opts));
    const shown = weeks.includes(P.app.seasonWeek) ? P.app.seasonWeek : weeks[weeks.length - 1];
    const pick = (week) => { P.app.seasonWeek = week; P.app.render(); };
    return card(title, P.charts.trend(weeks, series, { ...opts, onPick: pick }),
      readout(shown, series, opts.format, weeks.indexOf(shown)));
  }

  P.tabs.season = function (view) {
    const standings = view.data.standings.filter((s) => s.week <= view.state.week);
    const weeks = standings.map((s) => s.week);
    return [
      chart("Points by week", weeks, [
        { key: "us", label: "Us", values: standings.map((s) => s.points) },
        { key: "median", label: "Field median", values: standings.map((s) => s.median) },
        { key: "winner", label: "Winner", values: standings.map((s) => s.winner) },
      ], { caption: "Tap a week to see its numbers.", label: "Points by week", format: (v) => String(Math.round(v * 10) / 10) }),
      chart("Share of the field we beat", weeks, [
        { key: "us", label: "Beaten", values: standings.map((s) => s.beat_share) },
      ], { caption: "50% is the middle of the pool.", label: "Share of the field beaten", max: 1,
        format: (v) => fmt.pct(v, 0) }),
      h("p", { class: "note wide" }, "Standings are whole-week facts; the filters do not change them."),
    ];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
