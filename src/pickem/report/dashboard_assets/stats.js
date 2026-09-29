// Records, intervals and CLV, computed the same way as pickem.report.results.
(function (P) {
  "use strict";
  const Z = 1.96;

  function wilson(successes, trials) {
    if (trials === 0) return [0, 1];
    const p = successes / trials;
    const z2 = Z * Z;
    const denominator = 1 + z2 / trials;
    const center = (p + z2 / (2 * trials)) / denominator;
    const spread = (Z * Math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))) / denominator;
    return [Math.max(0, center - spread), Math.min(1, center + spread)];
  }

  function record(games, strategy) {
    let wins = 0, losses = 0, pushes = 0;
    for (const game of games) {
      const result = game.results[strategy];
      if (result === "win") wins++;
      else if (result === "loss") losses++;
      else if (result === "push") pushes++;
    }
    const decided = wins + losses;
    return {
      wins, losses, pushes, decided,
      rate: decided ? wins / decided : null,
      interval: decided ? wilson(wins, decided) : null,
    };
  }

  function clvSummary(games) {
    const values = games.map((g) => g.clv).filter((v) => v !== null && v !== undefined);
    const n = values.length;
    if (!n) return { n: 0, mean: null, positive: null, interval: null };
    const mean = values.reduce((a, b) => a + b, 0) / n;
    const positive = values.filter((v) => v > 0).length / n;
    if (n < 2) return { n, mean, positive, interval: null };
    const variance = values.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1);
    const half = (Z * Math.sqrt(variance)) / Math.sqrt(n);
    return { n, mean, positive, interval: [mean - half, mean + half] };
  }

  function pct(x, digits) {
    return x === null || x === undefined ? "—" : (x * 100).toFixed(digits) + "%";
  }

  function recordText(r) {
    if (!r.decided) return "—";
    return `${r.wins}–${r.losses} (${r.pushes}) = ${pct(r.rate, 1)} ` +
      `[${pct(r.interval[0], 0)}–${pct(r.interval[1], 0)}], n=${r.decided}`;
  }

  P.stats = { wilson, record, clvSummary, pct, recordText };
})(globalThis.Pickem = globalThis.Pickem || {});
