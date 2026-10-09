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

// The live settings as the page data carries them: NFL follows the market on any gap, CFB does not.
const NFL = { strong: 2, lean: 1, slight: true };
const CFB = { strong: 2, lean: 1, slight: false };
const bandAt = (bands, spread) => bands.find((b) => b.from <= spread && spread < b.to).label;

test("pick bands follow the tier rule level by level: TB @ DAL, CBS line DAL −8.5, NFL", () => {
  const bands = plain(S.pickBands(-8.5, NFL, -11.25, -5.75, "DAL", "TB"));
  assert.deepEqual(bands, [
    { from: -11.25, to: -10.25, side: "home", tier: "strong", label: "DAL strong" },
    { from: -10.25, to: -9.25, side: "home", tier: "lean", label: "DAL lean" },
    { from: -9.25, to: -8.75, side: "home", tier: "slight", label: "DAL slight" },
    { from: -8.75, to: -8.25, side: null, tier: "coinflip", label: "Coinflip" },
    { from: -8.25, to: -7.75, side: "away", tier: "slight", label: "TB slight" },
    { from: -7.75, to: -6.75, side: "away", tier: "lean", label: "TB lean" },
    { from: -6.75, to: -5.75, side: "away", tier: "strong", label: "TB strong" },
  ]);
  const table = [
    [-10.5, "DAL strong"], [-10, "DAL lean"], [-9.5, "DAL lean"], [-9, "DAL slight"], [-8.5, "Coinflip"],
    [-8, "TB slight"], [-7.5, "TB lean"], [-7, "TB lean"], [-6.5, "TB strong"],
  ];
  for (const [us, label] of table) assert.equal(bandAt(bands, us), label, `US books ${us}`);
});

test("on the CFB board the half-point gaps either side of the line are a coinflip", () => {
  assert.deepEqual(plain(S.pickBands(-8.5, CFB, -11.25, -5.75, "DAL", "TB")), [
    { from: -11.25, to: -10.25, side: "home", tier: "strong", label: "DAL strong" },
    { from: -10.25, to: -9.25, side: "home", tier: "lean", label: "DAL lean" },
    { from: -9.25, to: -7.75, side: null, tier: "coinflip", label: "Coinflip" },
    { from: -7.75, to: -6.75, side: "away", tier: "lean", label: "TB lean" },
    { from: -6.75, to: -5.75, side: "away", tier: "strong", label: "TB strong" },
  ]);
});

test("a pick'em CBS line takes the home side when the market favors home", () => {
  const bands = plain(S.pickBands(0, NFL, -3, 3, "KC", "BUF"));
  assert.deepEqual(bands.map((b) => [b.from, b.to, b.label]), [
    [-3, -1.75, "KC strong"], [-1.75, -0.75, "KC lean"], [-0.75, -0.25, "KC slight"],
    [-0.25, 0.25, "Coinflip"],
    [0.25, 0.75, "BUF slight"], [0.75, 1.75, "BUF lean"], [1.75, 3, "BUF strong"],
  ]);
});

test("bands are clipped to the range drawn, and none is left with no height", () => {
  assert.deepEqual(plain(S.pickBands(-8.5, NFL, -9.6, -7.9, "DAL", "TB")).map((b) => [b.from, b.to, b.label]), [
    [-9.6, -9.25, "DAL lean"], [-9.25, -8.75, "DAL slight"], [-8.75, -8.25, "Coinflip"], [-8.25, -7.9, "TB slight"],
  ]);
  assert.deepEqual(plain(S.pickBands(-8.5, NFL, -9.25, -8.25, "DAL", "TB")).map((b) => b.label),
    ["DAL slight", "Coinflip"]);
});

test("spread ticks fall on every half point when the range is small, every point when it is large", () => {
  assert.deepEqual(plain(S.spreadTicks(-11.25, -5.75, 15)),
    [-11, -10.5, -10, -9.5, -9, -8.5, -8, -7.5, -7, -6.5, -6]);
  assert.deepEqual(plain(S.spreadTicks(-20.25, -5.75, 15)),
    [-20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8, -7, -6]);
  // A blowout line that moved a long way thins out further rather than crowding the labels.
  assert.deepEqual(plain(S.spreadTicks(-30.25, -5.75, 15)), [-30, -28, -26, -24, -22, -20, -18, -16, -14, -12, -10, -8, -6]);
  assert.deepEqual(plain(S.spreadTicks(-0.75, 0.75, 15)), [-0.5, 0, 0.5]);
});

test("Eastern midnights inside a span, through the end of daylight saving time", () => {
  // Tue 5:01 PM to Sat 10:30 PM ET: Wednesday through Saturday begin inside it.
  assert.deepEqual(plain(S.etMidnights(Date.parse("2026-09-29T21:01:00Z"), Date.parse("2026-10-04T02:30:00Z")))
    .map((t) => new Date(t).toISOString()),
  ["2026-09-30T04:00:00.000Z", "2026-10-01T04:00:00.000Z", "2026-10-02T04:00:00.000Z",
    "2026-10-03T04:00:00.000Z"]);
  // Clocks go back at 2 AM on Sunday, November 1, 2026.
  assert.deepEqual(plain(S.etMidnights(Date.parse("2026-10-31T12:00:00Z"), Date.parse("2026-11-03T12:00:00Z")))
    .map((t) => new Date(t).toISOString()),
  ["2026-11-01T04:00:00.000Z", "2026-11-02T05:00:00.000Z", "2026-11-03T05:00:00.000Z"]);
  assert.deepEqual(plain(S.etMidnights(Date.parse("2026-10-01T05:00:00Z"), Date.parse("2026-10-01T20:00:00Z"))), []);
});
