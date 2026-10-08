// The detail panel: a drill-down list of games, or one game in full.
(function (P) {
  "use strict";
  const { h, LABELS, resultTag, fmt } = P.ui;
  const TIER = () => P.filters.TIER_LABELS;

  function shell(title, body) {
    return h("div", { class: "panel-wrap" },
      h("div", { class: "backdrop", onclick: () => P.app.closePanel() }),
      h("aside", { class: "panel", role: "dialog", "aria-modal": "true", "aria-label": title },
        h("div", { class: "panel-head" }, h("h2", null, title),
          h("button", { class: "close", type: "button", "aria-label": "Close", onclick: () => P.app.closePanel() }, "✕")),
        h("div", { class: "panel-body" }, body)));
  }

  function gameButton(data, g, strategy) {
    const side = g.picks[strategy];
    return h("button", { class: "row-button", type: "button", onclick: () => P.app.openGame(g.id) },
      h("span", { class: "grow" },
        `W${g.week} · `, P.ui.matchup(data, g),
        h("br"),
        h("span", { class: "muted" }, `${LABELS.strategy[strategy]}: ${side ? fmt.sideLine(g, side) : "no pick"}`)),
      resultTag(g.results[strategy]));
  }

  function gameList(list, data) {
    if (!list.games.length) return h("p", { class: "empty" }, "No games");
    return h("ul", { class: "list" }, list.games.map((g) => h("li", null, gameButton(data, g, list.strategy))));
  }

  function fact(label, value) {
    return [h("dt", null, label), h("dd", null, value)];
  }

  function timeline(g) {
    if (!g.history.length) return h("p", { class: "empty" }, "The model never covered this game.");
    // The changes come from the same rule as the chart markers; unchanged refreshes fold into one line.
    return h("ol", { class: "timeline" }, P.filters.timelineRows(g.history).map((row) => {
      if (row.type === "gap") {
        return h("li", { class: "gap" },
          `unchanged through ${fmt.time(row.through)} (${row.count} ${row.count === 1 ? "refresh" : "refreshes"})`);
      }
      const { entry: r, change } = row;
      return h("li", { class: change ? "changed" : null },
        `${fmt.time(r.at)} · ${fmt.team(g, r.side)} · ${TIER()[r.tier]} · edge ${fmt.signed(r.edge, 1)}`,
        change ? h("span", { class: "badge" }, change.kind === "side" ? "side changed" : "tier changed") : null);
    }));
  }

  function gameDetail(g, data) {
    return [
      h("div", { class: "tw-head" },
        P.ui.logo(data, g.sport, g.away, true), h("span", { class: "at" }, "@"), P.ui.logo(data, g.sport, g.home, true)),
      h("p", { class: "muted" }, `${LABELS.sport[g.sport]} · Week ${g.week} · ${fmt.kickoff(g.kickoff)}`),
      h("dl", { class: "facts" },
        fact("Final", `${g.away} ${g.away_score} – ${g.home} ${g.home_score}`),
        fact("CBS line", fmt.homeLine(g, g.line)),
        fact("Closing line", g.close === null ? "Not captured" : fmt.homeLine(g, g.close)),
        fact("Field", `${g.field_away} took ${g.away} · ${g.field_home} took ${g.home}`)),
      h("h3", null, "Every strategy"),
      h("table", null, h("tbody", null, data.strategies.map((st) => h("tr", null,
        h("th", null, LABELS.strategy[st]),
        h("td", null, g.picks[st] ? fmt.sideLine(g, g.picks[st]) : "no pick"),
        h("td", null, resultTag(g.results[st])))))),
      h("h3", null, "The model through the week"),
      timeline(g),
      h("h3", null, "Line movement"),
      P.charts.lineMove(g),
    ];
  }

  P.panel = function (view) {
    const id = view.state.game;
    const current = id && view.data.this_week && view.data.this_week.games.find((x) => x.id === id);
    const past = id && view.data.games.find((x) => x.id === id);
    if (current && (view.state.tab === "thisweek" || !past)) {
      return shell(`${current.away} @ ${current.home}`, P.thisWeek.detail(current, view.data, view.now));
    }
    const g = past;
    if (g) return shell(`${g.away} @ ${g.home}`, gameDetail(g, view.data));
    if (P.app.list) return shell(P.app.list.title, gameList(P.app.list, view.data));
    return null;
  };
  P.panel.fact = fact;
  P.panel.timeline = timeline;
})(globalThis.Pickem = globalThis.Pickem || {});
