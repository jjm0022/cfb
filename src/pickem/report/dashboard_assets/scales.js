// One linear scale per chart places every mark, whisker, tick and label.
(function (P) {
  "use strict";

  function linear(d0, d1, r0, r1) {
    const span = d1 - d0;
    return (v) => (span === 0 ? (r0 + r1) / 2 : r0 + ((v - d0) / span) * (r1 - r0));
  }

  function niceMax(values) {
    const finite = values.filter((v) => Number.isFinite(v));
    const max = finite.length ? Math.max(...finite) : 0;
    if (max <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(max)));
    for (const step of [1, 2, 2.5, 4, 5, 6, 8, 10]) {
      const top = Number((step * power).toPrecision(12));
      if (top >= max) return top;
    }
    return 10 * power;
  }

  function extent(values, pad) {
    const lo = Math.min(...values), hi = Math.max(...values);
    return [lo - pad, hi + pad];
  }

  function rateMark(record, x) {
    if (!record.decided) return null;
    return { x: x(record.rate), lo: x(record.interval[0]), hi: x(record.interval[1]) };
  }

  // The model's pick for every US books spread across [lo, hi], as horizontal bands. The same rule
  // as the bot's (edge/divergence.py): the gap is the CBS line minus US books; a positive gap picks
  // home and a negative one away, and its size sets the tier. Spreads move in half points, so each
  // half-point level owns the space halfway to its neighbours, and neighbours with the same pick merge.
  function tierFor(gap, t) {
    const size = Math.abs(gap);
    if (size >= t.strong) return "strong";
    if (size >= t.lean) return "lean";
    if (t.slight && size > 0) return "slight";
    return "coinflip";  // the side comes from a tiebreak, not the gap
  }

  function pickBands(line, thresholds, lo, hi, home, away) {
    const bands = [];
    for (let k = Math.floor(lo * 2) - 1; k <= Math.ceil(hi * 2) + 1; k++) {
      const level = k / 2;
      const from = Math.max(level - 0.25, lo), to = Math.min(level + 0.25, hi);
      if (to <= from) continue;
      const gap = line - level;
      const tier = tierFor(gap, thresholds);
      const side = tier === "coinflip" ? null : gap > 0 ? "home" : "away";
      const last = bands[bands.length - 1];
      if (last && last.side === side && last.tier === tier) { last.to = to; continue; }
      bands.push({ from, to, side, tier, label: side ? `${side === "home" ? home : away} ${tier}` : "Coinflip" });
    }
    return bands;
  }

  // Spread ticks inside [lo, hi]: every half point when at most `max` fit, else every point, and so on.
  function spreadTicks(lo, hi, max) {
    for (const step of [0.5, 1, 2, 5, 10]) {
      const first = Math.ceil(lo / step), last = Math.floor(hi / step);
      if (last - first + 1 <= max || step === 10) {
        return Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => (first + i) * step || 0);
      }
    }
  }

  const ET_PARTS = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hourCycle: "h23",
    year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  });

  // The Eastern wall-clock reading at instant t, written as if it were UTC.
  function easternWall(t) {
    const p = Object.fromEntries(ET_PARTS.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
    return Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour), Number(p.minute));
  }

  // Every Eastern midnight after start and up to end, as instants.
  function etMidnights(start, end) {
    const out = [];
    if (!(end >= start)) return out;
    const wall = new Date(easternWall(start));
    for (let day = Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth(), wall.getUTCDate() + 1); ; day += 864e5) {
      // The offset at 5 AM UTC, midnight or 1 AM Eastern: before any 2 AM clock change.
      const probe = day + 5 * 36e5;
      const midnight = day + (probe - easternWall(probe));
      if (midnight > end) return out;
      if (midnight > start) out.push(midnight);
    }
  }

  P.scales = { linear, niceMax, extent, rateMark, pickBands, spreadTicks, etMidnights };
})(globalThis.Pickem = globalThis.Pickem || {});
