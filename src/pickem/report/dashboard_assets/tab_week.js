// Week tab: the headline, the analysis, each board, and the confident picks that lost.
(function (P) {
  "use strict";
  const { h, card, LABELS, resultTag, fmt } = P.ui;

  function tile(value, label) {
    return h("div", { class: "tile" }, h("span", { class: "value" }, value), h("span", { class: "label" }, label));
  }

  function tiles(view, standing) {
    const games = view.data.games.filter((g) => g.week === standing.week).length;
    const gap = standing.winner - standing.points;
    return h("div", { class: "tiles wide" },
      tile(`${standing.points}/${games}`, "our points"),
      tile(`#${standing.rank}`, `of ${standing.entrants} entrants`),
      tile(fmt.signed(standing.points - standing.median, 1), `vs field median (${standing.median})`),
      tile(gap ? String(gap) : "Top", gap ? `behind the winner (${standing.winner})` : "top score this week"));
  }

  function analysis(data, week) {
    const text = data.analysis && data.analysis[String(week)];
    const body = typeof text === "string" && text.trim()
      ? text.trim().split(/\n\s*\n/).map((para) => h("p", null, para))
      : h("p", { class: "empty" }, "No analysis for this week yet.");
    return h("section", { class: "card analysis wide" }, h("h2", null, "Analysis"), body);
  }

  function boards(standing) {
    return card("By board", h("ul", { class: "list" }, standing.boards.map((b) => h("li", null,
      h("div", { class: "row-button" },
        h("strong", null, LABELS.sport[b.sport]),
        h("span", { class: "grow" }, ` ${b.points} pts · median ${b.median} · best ${b.best}`))))));
  }

  function confidentLosses(view) {
    const games = P.filters.confidentLosses(view.weekGames);
    const body = games.length
      ? h("ul", { class: "list" }, games.map((g) => h("li", null,
          h("button", { class: "row-button", type: "button", onclick: () => P.app.openGame(g.id) },
            h("span", { class: "grow" }, fmt.sideLine(g, g.model.side),
              h("br"), h("span", { class: "muted" }, `Final ${g.away} ${g.away_score}–${g.home_score} ${g.home} · edge ${fmt.signed(g.model.edge, 1)}`)),
            resultTag("loss")))))
      : h("p", { class: "empty" }, "None");
    return card("Confident picks that lost", h("p", { class: "note" }, "Strong-tier model picks that lost this week."), body);
  }

  function boardTables(view) {
    const sports = ["cfb", "nfl"].filter((sp) => view.weekGames.some((g) => g.sport === sp));
    if (!sports.length) return [card("This week", h("p", { class: "empty" }, "No games match the filters."))];
    return sports.map((sport) => {
      const games = view.weekGames.filter((g) => g.sport === sport);
      return card(`${LABELS.sport[sport]} this week`,
        h("div", { class: "scroll" }, h("table", { class: "board-table" }, h("tbody", null,
          view.data.week_strategies.map((st) => {
            const record = P.stats.record(games, st);
            const graded = P.filters.gradedFor(games, st);
            return h("tr", null,
              h("th", null, graded.length
                ? h("button", { class: "linkish", type: "button",
                    onclick: () => P.app.showList(`${LABELS.sport[sport]} week ${view.state.week} · ${LABELS.strategy[st]}`, graded, st) },
                    LABELS.strategy[st])
                : LABELS.strategy[st]),
              h("td", null, P.stats.recordText(record)));
          })))));
    });
  }

  P.tabs.week = function (view) {
    const standing = view.data.standings.find((s) => s.week === view.state.week);
    return [tiles(view, standing), analysis(view.data, view.state.week), boards(standing),
      confidentLosses(view), ...boardTables(view)];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
