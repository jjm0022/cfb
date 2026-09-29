import test from "node:test";
import assert from "node:assert/strict";
import { load, plain, game } from "./load.mjs";

const P = load("stats.js");

test("record counts wins, losses and pushes, and rates only decided games", () => {
  const games = [
    game({ results: { us: "win" } }), game({ results: { us: "loss" } }),
    game({ results: { us: "push" } }), game({ results: { us: null } }),
  ];
  const r = plain(P.stats.record(games, "us"));
  assert.deepEqual(
    { wins: r.wins, losses: r.losses, pushes: r.pushes, decided: r.decided, rate: r.rate },
    { wins: 1, losses: 1, pushes: 1, decided: 2, rate: 0.5 },
  );
  assert.deepEqual(r.interval, plain(P.stats.wilson(1, 2)));
});

test("a record with nothing decided has no rate and no interval", () => {
  const r = P.stats.record([game({ results: { us: "push" } })], "us");
  assert.equal(r.rate, null);
  assert.equal(r.interval, null);
  assert.equal(P.stats.recordText(r), "—");
});

test("wilson interval", () => {
  const [lo, hi] = P.stats.wilson(7, 10);
  assert.ok(Math.abs(lo - 0.3968) < 1e-4, lo);
  assert.ok(Math.abs(hi - 0.8922) < 1e-4, hi);
  assert.deepEqual(plain(P.stats.wilson(0, 0)), [0, 1]);
});

test("record text matches the Markdown format", () => {
  const games = [
    ...Array(7).fill(game({ results: { us: "win" } })),
    ...Array(3).fill(game({ results: { us: "loss" } })),
    game({ results: { us: "push" } }),
  ];
  assert.equal(P.stats.recordText(P.stats.record(games, "us")), "7–3 (1) = 70.0% [40%–89%], n=10");
});

test("clv summary: mean, share above zero, and a normal interval from n=2", () => {
  const s = plain(P.stats.clvSummary([game({ clv: 1 }), game({ clv: -1 }), game({ clv: 2 }), game({ clv: null })]));
  assert.equal(s.n, 3);
  assert.ok(Math.abs(s.mean - 2 / 3) < 1e-12);
  assert.ok(Math.abs(s.positive - 2 / 3) < 1e-12);
  const half = 1.96 * Math.sqrt(42 / 18) / Math.sqrt(3);
  assert.ok(Math.abs(s.interval[0] - (2 / 3 - half)) < 1e-12);
  assert.deepEqual(plain(P.stats.clvSummary([game({ clv: 1 })])).interval, null);
  assert.deepEqual(plain(P.stats.clvSummary([])), { n: 0, mean: null, positive: null, interval: null });
});

test("pct prints a dash for no value", () => {
  assert.equal(P.stats.pct(null, 1), "—");
  assert.equal(P.stats.pct(0.637, 1), "63.7%");
});
