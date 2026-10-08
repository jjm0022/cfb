// Page state in the address, and which games each view counts.
(function (P) {
  "use strict";
  const TABS = ["thisweek", "week", "season", "model", "games"];
  const SPORTS = ["all", "cfb", "nfl"];
  const TIERS = ["strong", "lean", "slight", "coinflip", "no_market", "none"];
  const RESULTS = ["all", "win", "loss"];
  const TIER_LABELS = {
    strong: "Strong", lean: "Lean", slight: "Slight", coinflip: "Coinflip", no_market: "No market",
    none: "No model",
  };

  function defaults(data) {
    return {
      week: data.latest_week, tab: data.standings.length ? "week" : "thisweek",
      sport: "all", tiers: [], result: "all", game: null,
    };
  }

  function thisWeekList(data) {
    return data.this_week ? data.this_week.games : [];
  }

  function parseHash(hash, data) {
    const state = defaults(data);
    const params = new URLSearchParams(String(hash || "").replace(/^#/, ""));
    const week = Number(params.get("week"));
    if (data.standings.some((s) => s.week === week)) state.week = week;
    if (TABS.includes(params.get("tab"))) state.tab = params.get("tab");
    if (SPORTS.includes(params.get("sport"))) state.sport = params.get("sport");
    if (RESULTS.includes(params.get("result"))) state.result = params.get("result");
    const tiers = (params.get("tier") || "").split(",");
    state.tiers = TIERS.filter((t) => tiers.includes(t));
    const id = params.get("game");
    if (id && (data.games.some((g) => g.id === id) || thisWeekList(data).some((g) => g.id === id))) state.game = id;
    return state;
  }

  function toHash(state, data) {
    const params = new URLSearchParams();
    if (state.week !== data.latest_week) params.set("week", String(state.week));
    if (state.tab !== defaults(data).tab) params.set("tab", state.tab);
    if (state.sport !== "all") params.set("sport", state.sport);
    if (state.tiers.length) params.set("tier", state.tiers.join(","));
    if (state.result !== "all") params.set("result", state.result);
    if (state.game) params.set("game", state.game);
    const text = params.toString();
    return text ? "#" + text : "";
  }

  function tierOf(game) {
    return game.model ? game.model.tier : "none";
  }

  function applyFilters(games, state) {
    return games.filter((g) =>
      g.week <= state.week &&
      (state.sport === "all" || g.sport === state.sport) &&
      (!state.tiers.length || state.tiers.includes(tierOf(g))) &&
      (state.result === "all" || g.results.us === state.result));
  }

  // This week's board takes the board and tier filters; no game on it has our result yet.
  function thisWeekGames(data, state) {
    return thisWeekList(data).filter((g) =>
      (state.sport === "all" || g.sport === state.sport) &&
      (!state.tiers.length || state.tiers.includes(tierOf(g))));
  }

  function confidentLosses(games) {
    return games.filter((g) => g.model && g.model.tier === "strong" && g.results.model === "loss");
  }

  function gradedFor(games, strategy) {
    return games.filter((g) => g.results[strategy] !== null && g.results[strategy] !== undefined);
  }

  function search(games, text) {
    const q = String(text || "").trim().toLowerCase();
    if (!q) return games;
    return games.filter((g) => g.home.toLowerCase().includes(q) || g.away.toLowerCase().includes(q));
  }

  function homeShare(g) {
    const total = g.field_home + g.field_away;
    return total ? g.field_home / total : null;
  }

  const SORT_KEYS = {
    week: (g) => g.week,
    kickoff: (g) => g.kickoff,
    matchup: (g) => g.away + " " + g.home,
    line: (g) => g.line,
    us: (g) => g.results.us,
    model: (g) => g.results.model,
    tier: (g) => TIERS.indexOf(tierOf(g)),
    field: homeShare,
  };

  function sortGames(games, key, dir) {
    const value = SORT_KEYS[key];
    const sign = dir === "desc" ? -1 : 1;
    const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    if (!value) return games.slice().sort(byId);
    return games.slice().sort((a, b) => {
      const x = value(a), y = value(b);
      const xMissing = x === null || x === undefined, yMissing = y === null || y === undefined;
      if (xMissing || yMissing) return xMissing && yMissing ? byId(a, b) : xMissing ? 1 : -1;
      if (x === y) return byId(a, b);
      return (x < y ? -1 : 1) * sign;
    });
  }

  function chips(state) {
    const out = [];
    if (state.sport !== "all") out.push({ key: "sport", label: state.sport.toUpperCase() });
    if (state.tiers.length) out.push({ key: "tiers", label: state.tiers.map((t) => TIER_LABELS[t]).join(" + ") });
    if (state.result !== "all") out.push({ key: "result", label: state.result === "win" ? "Our wins" : "Our losses" });
    return out;
  }

  function clearFilter(state, key) {
    const cleared = { sport: "all", tiers: [], result: "all" };
    return Object.assign({}, state, { [key]: cleared[key] });
  }

  // Where the model's side or tier moved, for the timeline and the chart markers.
  function pickChanges(history) {
    const list = history || [];
    const out = [];
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1], cur = list[i];
      if (prev.side !== cur.side || prev.tier !== cur.tier) {
        out.push({ at: cur.at, from: prev, to: cur, kind: prev.side !== cur.side ? "side" : "tier" });
      }
    }
    return out;
  }

  P.filters = {
    TABS, TIERS, TIER_LABELS, defaults, parseHash, toHash, tierOf, applyFilters, thisWeekGames, pickChanges,
    confidentLosses, gradedFor, search, sortGames, homeShare, chips, clearFilter,
  };
})(globalThis.Pickem = globalThis.Pickem || {});
