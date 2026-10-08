// This week tab: every game on the current boards with the model's pick.
(function (P) {
  "use strict";
  const { h, card, LABELS, resultTag, fmt, matchup, TIER_BADGES } = P.ui;
  const F = P.filters;

  function pick(g) {
    if (!g.model) return h("span", { class: "muted" }, "No model");
    return [h("span", { class: "tier-badge" }, TIER_BADGES[g.model.tier]), " ", fmt.sideLine(g, g.model.side)];
  }

  function status(g) {
    if (g.final) return [h("span", { class: "muted" }, `${g.away_score}–${g.home_score}`), resultTag(g.result)];
    if (g.locked) return h("span", { class: "lock", title: "Kicked off: the pick can no longer change" }, "🔒 Locked");
    return null;
  }

  function row(view, g) {
    return h("li", null, h("button", {
      class: "row-button tw-row", type: "button", "aria-label": `${g.away} @ ${g.home}`,
      onclick: () => P.app.openGame(g.id),
    },
      h("span", { class: "grow" },
        matchup(view.data, g), h("br"),
        h("span", { class: "muted" }, `${fmt.kickoff(g.kickoff)} · CBS ${fmt.homeLine(g, g.line)}`)),
      h("span", { class: "tw-pick" }, pick(g)),
      h("span", { class: "tw-status" }, status(g))));
  }

  P.tabs.thisweek = function (view) {
    const board = view.data.this_week;
    if (!board) return [card("This week", h("p", { class: "empty" }, "No board is loaded yet."))];
    const games = F.thisWeekGames(view.data, view.state);
    const head = h("p", { class: "note wide" }, `Pool week ${board.pool_week}`);
    const sports = ["cfb", "nfl"].filter((sp) => games.some((g) => g.sport === sp));
    if (!sports.length) return [head, card("This week", h("p", { class: "empty" }, "No games match the filters."))];
    return [head, ...sports.map((sport) => card(`${LABELS.sport[sport]} board`,
      h("ul", { class: "list" }, games.filter((g) => g.sport === sport).map((g) => row(view, g)))))];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
