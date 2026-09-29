// Games tab: every game within the filters, searchable and sortable; a row opens the game.
(function (P) {
  "use strict";
  const { h, resultTag, fmt } = P.ui;
  const F = P.filters;
  const COLUMNS = [
    ["week", "Wk"], ["kickoff", "Kickoff"], ["matchup", "Matchup"], ["line", "Line"], [null, "Final"],
    ["us", "Us"], ["model", "Model"], ["field", "Field"], [null, "Flags"],
  ];

  function header(key, label, redraw) {
    if (!key) return h("th", { scope: "col" }, label);
    const sort = P.app.sort;
    const active = sort.key === key;
    return h("th", { scope: "col", "aria-sort": active ? (sort.dir === "asc" ? "ascending" : "descending") : null },
      h("button", { type: "button", onclick: () => {
        P.app.sort = { key, dir: active && sort.dir === "asc" ? "desc" : "asc" };
        redraw();
      } }, label + (active ? (sort.dir === "asc" ? " ▲" : " ▼") : "")));
  }

  function row(g) {
    const share = F.homeShare(g);
    const disagreed = g.picks.us && g.picks.model && g.picks.us !== g.picks.model;
    return h("tr", {
      class: "game-row", tabindex: 0, onclick: () => P.app.openGame(g.id),
      onkeydown: (e) => { if (e.key === "Enter") P.app.openGame(g.id); },
    },
      h("td", null, g.week),
      h("td", null, fmt.kickoff(g.kickoff)),
      h("td", null, `${g.away} @ ${g.home}`),
      h("td", null, fmt.homeLine(g, g.line)),
      h("td", null, `${g.away_score}–${g.home_score}`),
      h("td", null, fmt.team(g, g.picks.us), " ", resultTag(g.results.us)),
      h("td", null, fmt.team(g, g.picks.model),
        g.model ? h("span", { class: "tier" }, F.TIER_LABELS[g.model.tier]) : null, " ", resultTag(g.results.model)),
      h("td", null, share === null ? "—" : `${fmt.pct(share, 0)} ${g.home}`),
      h("td", null,
        disagreed ? h("span", { class: "flag" }, "vs model") : null,
        g.against_field ? h("span", { class: "flag" }, "vs field") : null));
  }

  function table(view, redraw) {
    const games = F.sortGames(F.search(view.games, P.app.search), P.app.sort.key, P.app.sort.dir);
    if (!games.length) return h("p", { class: "empty" }, "No games match");
    return h("table", { class: "games" },
      h("thead", null, h("tr", null, COLUMNS.map(([key, label]) => header(key, label, redraw)))),
      h("tbody", null, games.map(row)));
  }

  P.tabs.games = function (view) {
    const wrap = h("div", { class: "scroll" });
    const redraw = () => wrap.replaceChildren(table(view, redraw));
    redraw();
    return [h("section", { class: "card" },
      h("div", { class: "games-head" }, h("h2", null, `Games through week ${view.state.week}`),
        h("input", {
          type: "search", placeholder: "Search teams", "aria-label": "Search teams", value: P.app.search,
          oninput: (e) => { P.app.search = e.target.value; redraw(); },
        })),
      wrap)];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
