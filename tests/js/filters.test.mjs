import test from "node:test";
import assert from "node:assert/strict";
import { load, plain, game } from "./load.mjs";

const P = load("stats.js", "filters.js");
const F = P.filters;
const data = {
  latest_week: 3,
  standings: [{ week: 1 }, { week: 2 }, { week: 3 }],
  games: [game({ id: "a" }), game({ id: "b" })],
};

test("no hash opens the latest week's Week tab with no filters", () => {
  assert.deepEqual(plain(F.parseHash("", data)),
    { week: 3, tab: "week", sport: "all", tiers: [], result: "all", game: null });
});

test("a full hash round-trips", () => {
  const state = { week: 2, tab: "games", sport: "cfb", tiers: ["strong", "none"], result: "loss", game: "b" };
  assert.deepEqual(plain(F.parseHash(F.toHash(state, data), data)), state);
});

test("defaults are left out of the address", () => {
  assert.equal(F.toHash(F.defaults(data), data), "");
});

test("bad or stale values fall back to defaults one by one", () => {
  const state = plain(F.parseHash("#week=9&tab=nope&sport=mlb&tier=strong,bogus,strong&result=tie&game=gone", data));
  assert.deepEqual(state, { week: 3, tab: "week", sport: "all", tiers: ["strong"], result: "all", game: null });
});

test("tier of a game the model never covered is 'none'", () => {
  assert.equal(F.tierOf(game({ model: null })), "none");
  assert.equal(F.tierOf(game()), "strong");
});

test("filters combine: week range, board, tiers and our result", () => {
  const games = [
    game({ id: "w1", week: 1 }),
    game({ id: "w3", week: 3 }),
    game({ id: "nfl", week: 1, sport: "nfl" }),
    game({ id: "lean", week: 1, model: { side: "home", tier: "lean", edge: 1, at: "" } }),
    game({ id: "nomodel", week: 1, model: null }),
    game({ id: "lost", week: 1, results: { us: "loss" } }),
    game({ id: "blank", week: 1, results: { us: null } }),
  ];
  const ids = (state) => F.applyFilters(games, { ...F.defaults(data), ...state }).map((g) => g.id);
  assert.deepEqual(ids({ week: 2 }), ["w1", "nfl", "lean", "nomodel", "lost", "blank"]);
  assert.deepEqual(ids({ week: 2, sport: "nfl" }), ["nfl"]);
  assert.deepEqual(ids({ week: 2, tiers: ["lean", "none"] }), ["lean", "nomodel"]);
  assert.deepEqual(ids({ week: 2, result: "loss" }), ["lost"]);
  assert.deepEqual(ids({ week: 2, result: "win", sport: "cfb", tiers: ["strong"] }), ["w1"]);
  assert.deepEqual(ids({ week: 2, tiers: ["coinflip"] }), []);
});

test("confident losses are strong-tier games the model lost", () => {
  const games = [
    game({ id: "hit" }),
    game({ id: "miss", results: { model: "loss" } }),
    game({ id: "leanmiss", results: { model: "loss" }, model: { side: "home", tier: "lean", edge: 1, at: "" } }),
  ];
  assert.deepEqual(F.confidentLosses(games).map((g) => g.id), ["miss"]);
});

test("a drill-down lists exactly the games its record counts", () => {
  const games = [
    game({ id: "w", results: { model: "win" } }), game({ id: "l", results: { model: "loss" } }),
    game({ id: "p", results: { model: "push" } }), game({ id: "n", results: { model: null } }),
  ];
  const listed = F.gradedFor(games, "model");
  const r = P.stats.record(games, "model");
  assert.deepEqual(listed.map((g) => g.id), ["w", "l", "p"]);
  assert.equal(listed.length, r.wins + r.losses + r.pushes);
});

test("search matches either team, ignoring case and spaces", () => {
  const games = [game({ id: "a", home: "MICH", away: "OU" }), game({ id: "b", home: "TEM", away: "PSU" })];
  assert.deepEqual(F.search(games, "  psu ").map((g) => g.id), ["b"]);
  assert.deepEqual(F.search(games, "").map((g) => g.id), ["a", "b"]);
});

test("sorting puts missing values last in both directions and breaks ties by id", () => {
  const games = [
    game({ id: "b", results: { us: "win" } }), game({ id: "a", results: { us: "win" } }),
    game({ id: "c", results: { us: null } }), game({ id: "d", results: { us: "loss" } }),
  ];
  assert.deepEqual(F.sortGames(games, "us", "asc").map((g) => g.id), ["d", "a", "b", "c"]);
  assert.deepEqual(F.sortGames(games, "us", "desc").map((g) => g.id), ["a", "b", "d", "c"]);
  assert.deepEqual(F.sortGames(games, "nope", "asc").map((g) => g.id), ["a", "b", "c", "d"]);
});

test("chips name each active filter and clear one at a time", () => {
  const state = { ...F.defaults(data), sport: "cfb", tiers: ["strong", "none"], result: "win" };
  assert.deepEqual(plain(F.chips(state)), [
    { key: "sport", label: "CFB" },
    { key: "tiers", label: "Strong + No model" },
    { key: "result", label: "Our wins" },
  ]);
  assert.deepEqual(plain(F.clearFilter(state, "tiers")).tiers, []);
  assert.deepEqual(plain(F.chips(F.defaults(data))), []);
});

test("the slight tier is a valid filter", () => {
  const state = plain(F.parseHash("#tier=slight,lean", data));
  assert.deepEqual(state.tiers, ["lean", "slight"]);
  assert.equal(F.TIER_LABELS.slight, "Slight");
});

const thisWeek = {
  pool_week: 6,
  games: [
    { id: "tw-cfb", sport: "cfb", model: { side: "home", tier: "strong", edge: 3, at: "" } },
    { id: "tw-nfl", sport: "nfl", model: { side: "away", tier: "slight", edge: -0.5, at: "" } },
    { id: "tw-none", sport: "nfl", model: null },
  ],
};

test("this week is the first tab", () => {
  assert.equal(F.TABS[0], "thisweek");
});

test("with results, the bare page still opens the results tab, so old links keep working", () => {
  const withBoard = { ...data, this_week: thisWeek };
  assert.equal(F.parseHash("", withBoard).tab, "week");
  assert.equal(F.parseHash("#week=2", withBoard).tab, "week");
  assert.equal(F.toHash(F.defaults(withBoard), withBoard), "");
});

test("before any results, the page opens on this week", () => {
  const empty = { latest_week: null, standings: [], games: [], this_week: thisWeek };
  assert.equal(F.parseHash("", empty).tab, "thisweek");
  assert.equal(F.toHash(F.defaults(empty), empty), "");
  assert.equal(F.toHash({ ...F.defaults(empty), tab: "season" }, empty), "#tab=season");
});

test("a this-week game id survives the address", () => {
  const withBoard = { ...data, this_week: thisWeek };
  const state = plain(F.parseHash("#tab=thisweek&game=tw-nfl", withBoard));
  assert.equal(state.tab, "thisweek");
  assert.equal(state.game, "tw-nfl");
});

test("this week's games follow the board and tier filters, not the result filter", () => {
  const withBoard = { ...data, this_week: thisWeek };
  const ids = (patch) => F.thisWeekGames(withBoard, { ...F.defaults(withBoard), ...patch }).map((g) => g.id);
  assert.deepEqual(ids({}), ["tw-cfb", "tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ sport: "nfl" }), ["tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ tiers: ["slight", "none"] }), ["tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ result: "loss" }), ["tw-cfb", "tw-nfl", "tw-none"]);
  assert.deepEqual(plain(F.thisWeekGames(data, F.defaults(data))), []);
});

test("pick changes are the history entries whose side or tier moved", () => {
  const h = (at, side, tier) => ({ at, side, tier, edge: 0 });
  const changes = F.pickChanges([h("1", "away", "lean"), h("2", "away", "lean"),
    h("3", "home", "lean"), h("4", "home", "strong")]);
  assert.deepEqual(plain(changes.map((c) => [c.at, c.kind])), [["3", "side"], ["4", "tier"]]);
  assert.deepEqual(plain(F.pickChanges([])), []);
  assert.deepEqual(plain(F.pickChanges([h("1", "home", "lean")])), []);
});

test("a game is locked by its flag, or once its kickoff is at or before the viewer's clock", () => {
  const now = Date.parse("2026-10-10T16:00:00Z");
  assert.equal(F.isLocked({ locked: true, kickoff: "2026-10-11T16:00:00Z" }, now), true);
  assert.equal(F.isLocked({ locked: false, kickoff: "2026-10-10T15:59:00Z" }, now), true);
  assert.equal(F.isLocked({ locked: false, kickoff: "2026-10-10T16:00:00Z" }, now), true);
  assert.equal(F.isLocked({ locked: false, kickoff: "2026-10-10T16:01:00Z" }, now), false);
});
