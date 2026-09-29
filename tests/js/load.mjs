// Load the dashboard's classic scripts into a fresh context, the way the page concatenates them.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ASSETS = new URL("../../src/pickem/report/dashboard_assets/", import.meta.url);

export function load(...names) {
  const context = vm.createContext({ URLSearchParams });
  for (const name of names) {
    vm.runInContext(readFileSync(new URL(name, ASSETS), "utf8"), context, { filename: name });
  }
  return context.Pickem;
}

// Values built inside the vm carry that context's prototypes; compare them as plain data.
export const plain = (value) => JSON.parse(JSON.stringify(value));

const STRATEGIES = ["us", "model", "first sheet", "close divergence", "favorites", "home", "field consensus"];

export function game(overrides = {}) {
  const base = {
    id: "g1", sport: "cfb", week: 1, kickoff: "2026-09-05T16:00:00Z",
    home: "MICH", away: "OU", home_score: 17, away_score: 10,
    line: -3.5, close: -4.0, field_home: 10, field_away: 5, against_field: false, clv: 0.5,
    picks: Object.fromEntries(STRATEGIES.map((s) => [s, "home"])),
    results: Object.fromEntries(STRATEGIES.map((s) => [s, "win"])),
    model: { at: "2026-09-05T14:00:00Z", side: "home", tier: "strong", edge: 2.5 },
    history: [], lines: [],
  };
  return {
    ...base, ...overrides,
    picks: { ...base.picks, ...(overrides.picks || {}) },
    results: { ...base.results, ...(overrides.results || {}) },
  };
}
