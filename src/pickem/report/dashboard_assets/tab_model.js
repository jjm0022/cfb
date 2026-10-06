// Model tab: tier hit rates, us against baselines, closing-line value, findings, glossary.
(function (P) {
  "use strict";
  const { h, card, LABELS, fmt } = P.ui;
  const F = P.filters;
  const TIERS = ["strong", "lean", "slight", "coinflip"];

  function drill(prefix) {
    return (row) => P.app.showList(`${prefix}${row.label}: ${P.stats.recordText(row.record)}`, row.games, row.strategy);
  }

  function tierRows(view) {
    const sports = ["cfb", "nfl"].filter((sp) => view.games.some((g) => g.sport === sp));
    return sports.flatMap((sport) => TIERS.flatMap((tier) => {
      const games = view.games.filter((g) => g.sport === sport && F.tierOf(g) === tier);
      const expected = view.data.backtest[tier];
      const source = tier === "slight" ? "stand-in-line test"
        : sport === "cfb" ? "NFL backtest" : "backtest";
      const row = (label, subset) => ({
        label: `${LABELS.sport[sport]} ${label}`,
        note: `${source} ${fmt.pct(expected, 1)}`,
        record: P.stats.record(subset, "model"), expected,
        games: F.gradedFor(subset, "model"), strategy: "model",
      });
      if (tier !== "slight") return [row(F.TIER_LABELS[tier].toLowerCase(), games)];
      if (!games.length) return [];
      return [
        row("slight · crosses 3 or 7", games.filter((g) => g.key_number != null)),
        row("slight · other", games.filter((g) => g.key_number == null)),
      ];
    }));
  }

  function baselineRows(view) {
    return ["us", ...view.data.baselines].map((st) => ({
      label: LABELS.strategy[st], record: P.stats.record(view.games, st),
      games: F.gradedFor(view.games, st), strategy: st,
    }));
  }

  function tile(value, label) {
    return h("div", { class: "tile" }, h("span", { class: "value" }, value), h("span", { class: "label" }, label));
  }

  function clv(view) {
    const summary = P.stats.clvSummary(view.games);
    const against = view.games.filter((g) => g.against_field);
    const interval = summary.interval
      ? `[${fmt.signed(summary.interval[0], 2)}, ${fmt.signed(summary.interval[1], 2)}]` : "no interval yet";
    return card("Closing-line value",
      h("p", { class: "note" }, "Points the market moved toward our pick after CBS froze its line. Positive is good even when the game lost."),
      h("div", { class: "tiles" },
        tile(summary.n ? fmt.signed(summary.mean, 2) : "—", `mean pts ${interval}`),
        tile(fmt.pct(summary.positive, 0), "picks the line moved toward"),
        tile(String(summary.n), "picks with a close"),
        tile(P.stats.record(against, "us").decided ? fmt.pct(P.stats.record(against, "us").rate, 0) : "—",
          `against the field: ${P.stats.recordText(P.stats.record(against, "us"))}`)));
  }

  function findingsCard(view) {
    const found = view.data.findings[String(view.state.week)] || { claims: [], not_yet: [] };
    return card("What the data says",
      h("p", { class: "note" }, `All games through week ${view.state.week}; the filters do not apply here.`),
      found.claims.length ? h("ul", { class: "claims" }, found.claims.map((c) => h("li", null, c)))
        : h("p", { class: "empty" }, "No difference is clear yet."),
      found.not_yet.length ? h("details", { class: "fold" }, h("summary", null, "Not distinguishable yet"),
        h("ul", { class: "claims" }, found.not_yet.map((c) => h("li", null, c)))) : null);
  }

  function glossary(data) {
    return h("section", { class: "card wide" }, h("details", { class: "fold" },
      h("summary", null, "What the terms mean"),
      h("p", null, data.glossary_intro),
      h("dl", null, data.strategies.map((st) => [h("dt", null, LABELS.strategy[st]), h("dd", null, data.definitions[st])]))));
  }

  P.tabs.model = function (view) {
    return [
      card("Model by tier", h("p", { class: "note" }, "Dot: win rate. Whisker: 95% range. Gold tick: backtest. Tap a row for its games."),
        P.charts.rateRows(tierRows(view), drill("Model · "))),
      card("Us against baselines", P.charts.rateRows(baselineRows(view), drill(""))),
      clv(view),
      findingsCard(view),
      glossary(view.data),
    ];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
