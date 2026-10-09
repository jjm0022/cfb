// This week tab: every game on the current boards with the model's pick.
(function (P) {
  "use strict";
  const { h, card, LABELS, resultTag, fmt, matchup, TIER_BADGES } = P.ui;
  const F = P.filters;

  function pick(g) {
    if (!g.model) return h("span", { class: "muted" }, "No model");
    return [h("span", { class: "tier-badge" }, TIER_BADGES[g.model.tier]), " ", fmt.sideLine(g, g.model.side)];
  }

  function status(g, now) {
    if (g.final) return [h("span", { class: "muted" }, `${g.away_score}–${g.home_score}`), resultTag(g.result)];
    if (F.isLocked(g, now)) {
      return h("span", { class: "lock", title: "Kicked off: the pick can no longer change" }, "🔒 Locked");
    }
    return null;
  }

  function row(view, g) {
    return h("li", null, h("button", {
      class: "row-button tw-row", type: "button", "aria-label": `${g.away} @ ${g.home}`,
      onclick: () => P.app.openGame(g.id),
    },
      h("span", { class: "grow" },
        matchup(view.data, g, g.model && g.model.side), h("br"),
        h("span", { class: "muted" }, `${fmt.kickoff(g.kickoff)} · CBS ${fmt.homeLine(g, g.line)}`)),
      h("span", { class: "tw-pick" }, pick(g)),
      h("span", { class: "tw-status" }, status(g, view.now))));
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

  function gapText(g) {
    if (g.gap === null || g.gap === undefined) return "—";
    if (g.gap === 0) return "None";
    return `${Math.abs(g.gap).toFixed(1)} pts toward ${g.gap > 0 ? g.home : g.away}`;
  }

  function changeLabel(g, c) {
    const T = F.TIER_LABELS;
    return `${T[c.from.tier]} ${fmt.team(g, c.from.side)} → ${T[c.to.tier]} ${fmt.team(g, c.to.side)}`;
  }

  P.thisWeek = {
    detail(g, data, now) {
      const fact = P.panel.fact;
      const m = g.model;
      const marks = F.pickChanges(g.history).map((c) => ({ at: c.at, label: changeLabel(g, c) }));
      const ring = (side) => P.ui.pickClass(m && m.side, side);
      return [
        h("div", { class: "tw-head" },
          P.ui.logo(data, g.sport, g.away, true, ring("away")), h("span", { class: "at" }, "@"),
          P.ui.logo(data, g.sport, g.home, true, ring("home"))),
        h("p", { class: "muted" },
          `${LABELS.sport[g.sport]} · Week ${g.week} · ${fmt.kickoff(g.kickoff)}${F.isLocked(g, now) ? " · Locked" : ""}`),
        h("h3", null, "Lines now"),
        h("dl", { class: "facts" },
          g.final ? fact("Final", `${g.away} ${g.away_score} – ${g.home} ${g.home_score}`) : null,
          fact("CBS line", fmt.homeLine(g, g.line)),
          fact("US books", g.market.us === null ? "No quotes yet" : fmt.homeLine(g, g.market.us)),
          fact("Pinnacle", g.market.pinnacle === null ? "No quote yet" : fmt.homeLine(g, g.market.pinnacle)),
          fact("Gap", gapText(g))),
        h("h3", null, "The pick and why"),
        m ? h("p", null, h("span", { class: "tier-badge" }, TIER_BADGES[m.tier]), " ", fmt.sideLine(g, m.side),
              g.final ? [" ", resultTag(g.result)] : null)
          : h("p", { class: "empty" }, "The model has no pick for this game."),
        m ? h("p", { class: "muted" }, m.rationale || "No reason recorded for this pick.") : null,
        g.key_number
          ? h("p", { class: "note" }, `Crosses ${g.key_number}, one of the margins football games most often end on.`)
          : null,
        h("h3", null, "How the pick changed this week"),
        P.panel.timeline(g),
        h("h3", null, "Spread over time"),
        P.charts.lineMove(g, marks),
      ];
    },
  };
})(globalThis.Pickem = globalThis.Pickem || {});
