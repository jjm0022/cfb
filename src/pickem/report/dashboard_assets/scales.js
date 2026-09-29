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

  P.scales = { linear, niceMax, extent, rateMark };
})(globalThis.Pickem = globalThis.Pickem || {});
