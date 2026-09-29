// Print the page's records and CLV summaries for every board × tier, for comparison with Python.
import { readFileSync } from "node:fs";
import { load } from "./load.mjs";

const P = load("stats.js", "filters.js");
const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = { records: {}, clv: {} };
for (const sport of ["all", "cfb", "nfl"]) {
  for (const tier of ["any", "strong", "lean", "coinflip", "no_market"]) {
    const state = { ...P.filters.defaults(data), sport, tiers: tier === "any" ? [] : [tier] };
    const games = P.filters.applyFilters(data.games, state);
    for (const strategy of data.strategies) {
      out.records[`${strategy}|${sport}|${tier}`] = P.stats.record(games, strategy);
    }
  }
  out.clv[sport] = P.stats.clvSummary(
    P.filters.applyFilters(data.games, { ...P.filters.defaults(data), sport }),
  );
}
process.stdout.write(JSON.stringify(out));
