import test from "node:test";
import assert from "node:assert/strict";
import { load, plain } from "./load.mjs";

const P = load("stats.js", "scales.js");
const S = P.scales;

test("linear maps the domain onto the range", () => {
  const x = S.linear(0, 1, 10, 510);
  assert.equal(x(0), 10);
  assert.equal(x(0.5), 260);
  assert.equal(x(1), 510);
  const y = S.linear(0, 20, 150, 10);
  assert.equal(y(20), 10);
});

test("a zero-width domain maps to the middle of the range", () => {
  assert.equal(S.linear(3, 3, 0, 100)(3), 50);
});

test("niceMax rounds up to a readable axis top and never returns zero", () => {
  assert.equal(S.niceMax([13, 7]), 20);
  assert.equal(S.niceMax([31]), 40);
  assert.equal(S.niceMax([0, 0]), 1);
  assert.equal(S.niceMax([]), 1);
  assert.equal(S.niceMax([0.75]), 0.8);
});

test("extent pads both ends and widens a flat series", () => {
  assert.deepEqual(plain(S.extent([-3, -4.5], 0.5)), [-5, -2.5]);
  assert.deepEqual(plain(S.extent([-3, -3], 0.5)), [-3.5, -2.5]);
});

test("rate mark sits on the scale, with whiskers at the interval", () => {
  const x = S.linear(0, 1, 0, 100);
  const record = P.stats.record(
    [...Array(7).fill({ results: { m: "win" } }), ...Array(3).fill({ results: { m: "loss" } })], "m");
  const mark = S.rateMark(record, x);
  assert.ok(Math.abs(mark.x - 70) < 1e-9);
  assert.ok(Math.abs(mark.lo - 39.68) < 0.01 && Math.abs(mark.hi - 89.22) < 0.01);
  assert.equal(S.rateMark(P.stats.record([], "m"), x), null);
});
