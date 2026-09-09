# Tuning the divergence tier thresholds — a null result

**Written:** 2026-08-19
**Superseded in part (2026-09-09):** where this document says the tiebreak is
Elo, `COINFLIP` now takes the frozen-board favorite (`edge/favorite.py`);
`NO_MARKET` still uses Elo. The measurements and the threshold conclusions are
unaffected — the "A better tiebreak" recommendation was acted on by replacing
the rule, not by improving Elo, and the replacement scored 51.00% against Elo's
48.93%, still noise around 50%.
**Data:** NFL 2020–2025, 1,663 graded games, from a read-only copy of
`data/pickem.duckdb`. No production code was changed.
**Why this exists:** `Thresholds.strong = 2.0` and `Thresholds.lean = 1.0` were
guesses made before any data existed. Now there is data, so the guesses are
checkable.

## Recommendation

**Leave the thresholds as they are: `strong = 2.0`, `lean = 1.0`.**
Confidence: high that this is the right *decision*; the confidence comes from
the finding that there is nothing to gain, not from a belief that 2.0/1.0 is
special.

Two findings drive it, and the first is structural rather than statistical.

**1. `strong` cannot affect the objective at all.** STRONG and LEAN picks are
both the divergence side, passed through `apply_tiebreaks` untouched. Moving the
STRONG/LEAN boundary relabels games; it never changes a pick. Across all 56 grid
cells the overall record is a function of `lean` alone — every cell sharing a
`lean` value has a byte-identical win/loss/push record. `strong` is a *display*
parameter: it decides which games the sheet calls high-confidence. It is not a
tuning knob for total correct picks, and no amount of backtesting can make it
one.

**2. `lean` has a real gradient in-sample that does not survive holdout.** Lower
`lean` is monotonically better on the full span (53.7% at 0.5 down to 50.6% at
3.0), which is the sweep's one clear shape. But the best cell beats the incumbent
by **+9 correct picks in-sample and +1 out-of-sample**, against a paired noise
standard deviation of ~15 picks. That is a null result, stated plainly.

## The objective, and why the surface is one-dimensional

The league forces a pick on every game and scores 1 point flat, so the metric is
total correct picks — equivalently the overall hit rate on all graded games.
Pushes follow the existing convention in `src/pickem/backtest/stats.py`: excluded
from the denominator, not counted as half-wins.

Each game is decided one of two ways. If `abs(delta) >= lean` the pick is the
divergence side. Otherwise the game is COINFLIP (or NO_MARKET) and the Elo
tiebreak decides it. So `lean` is the only parameter that moves games between the
two deciders, and the entire tuning question reduces to: **for a given band of
divergence magnitude, does the divergence side beat Elo?**

Measured directly, by replaying every market game twice — once with `lean`
effectively 0 so everything is a divergence pick, once with `lean` effectively
infinite so everything falls to Elo, with identical Elo history in both runs:

| `abs(delta)` band | n | divergence | Elo | picks agree |
|---|---:|---|---|---:|
| exactly 0 | 386 | 50.0% | 50.0% | 100% |
| 0 – 0.5 | 34 | 44.1% | 32.4% | 59% |
| 0.5 – 1.0 | 506 | 52.1% | 50.3% | 51% |
| 1.0 – 1.5 | 288 | 55.9% | 50.2% | 54% |
| 1.5 – 2.0 | 174 | 51.5% | 52.1% | 53% |
| 2.0 – 2.5 | 95 | 68.1% | 43.6% | 44% |
| 2.5 – 3.0 | 46 | 58.7% | 50.0% | 39% |
| 3.0+ | 134 | 62.4% | 45.9% | 58% |

Elo sits at coin-flip everywhere, as expected. Divergence beats it in six of
seven bands, which is the mechanism argument for a *low* `lean` — but the bands
where it wins by a lot are small, and the one large band that would actually move
the needle (0.5–1.0, 506 games) wins by 1.8 points, which is a coin toss away
from zero.

Note the granularity: the only observed `abs(delta)` values below 1.0 are 0,
0.25, 0.5 and 0.75. A 0.25 grid buys exactly one extra decision boundary over a
0.5 grid, covering 34 games. That is why the sweep below is on a 0.5 grid.

## Grid

`lean` in {0.5, 1.0, 1.5, 2.0, 2.5, 3.0}; `strong` in {1.0, 1.5, … 6.0}; cells
with `strong < lean` dropped. 56 cells, each a full 2020–2025 replay through
`run_backtest`'s exact loop (copied verbatim into a scratch script so each pick
could be tagged with its season; the copy reproduces the shipped
866-762-35 / 53.2% headline at 2.0/1.0 to the game).

Full-span in-sample, top cells:

| cell | overall | STRONG n | STRONG rate | COINFLIP n |
|---|---|---:|---:|---:|
| any `strong` / 0.5 | 875-753-35 (53.7%) | varies | varies | 420 |
| any `strong` / 1.0 — **incumbent** | 866-762-35 (53.2%) | 275 @ 2.0 | 63.7% | 926 |
| any `strong` / 2.0 | 851-777-35 (52.3%) | | | 1,388 |
| any `strong` / 1.5 | 850-778-35 (52.2%) | | | 1,214 |
| any `strong` / 2.5 | 828-800-35 (50.9%) | | | 1,483 |
| any `strong` / 3.0 | 824-804-35 (50.6%) | | | 1,529 |

The whole grid spans 50.6% to 53.7% — a range of 51 correct picks over six
seasons — and **49 of 56 cells fall inside the incumbent's own 95% Wilson
interval** (50.8%–55.6%). The surface is flat in the sense that matters.

**Degenerate optima, flagged as required.** Sorting cells by STRONG-tier hit
rate rather than by the objective produces `strong = 5.0` at 86.4% on 22 games
(CI is enormous) and `strong = 4.5` at 77.1% on 35 games. These are noise, and
because `strong` is objective-neutral they are not even tempting: adopting them
would change nothing except how confident the sheet claims to be. At
`strong = 2.0` the STRONG tier is 275 games (16.8% of the sheet) at 63.7%, which
is a defensible volume/purity point for a label.

## Scheme A — walk-forward (primary)

Tune on all strictly prior seasons, evaluate on the test season alone. Elo
history is continuous across the whole replay, so a "2023 only" evaluation still
sees the rating warmed by 2020–2022, exactly as the shipped run would.

Tuning rule: argmax overall hit rate on the training seasons; ties broken toward
fewer COINFLIP games, then toward the incumbent.

| test season | tuned cell | train rate | OOS tuned | OOS incumbent | Δ picks |
|---|---|---|---|---|---:|
| 2021 | 2.0/0.5 | 55.4% | 145-129 (52.9%) | 142-132 (51.8%) | +3 |
| 2022 | 2.0/0.5 | 54.1% | 141-125 (53.0%) | 142-124 (53.4%) | −1 |
| 2023 | 2.0/0.5 | 53.7% | 146-133 (52.3%) | 147-132 (52.7%) | −1 |
| 2024 | 2.0/0.5 | 53.4% | 154-124 (55.4%) | 158-120 (56.8%) | −4 |
| 2025 | 2.0/0.5 | 53.8% | 150-130 (53.6%) | 146-134 (52.1%) | +4 |
| **pooled** | | | **736-641 (53.4%, CI 50.8%–56.1%)** | **735-642 (53.4%, CI 50.7%–56.0%)** | **+1** |

The tuning is *stable* — every fold picks `lean = 0.5` — and that stability buys
one extra correct pick in 1,377 decided games. The sign of the per-season delta
flips three times.

## Scheme B — fixed holdout (train 2020–2023, test 2024–2025)

| | tuned (2.0/0.5) | incumbent (2.0/1.0) |
|---|---|---|
| train 2020–2023 | 571-499-23 (53.4%) | 562-508-23 (52.5%) |
| **test 2024–2025** | **304-254-12 (54.5%)** | **304-254-12 (54.5%)** |

Identical to the pick. Not the same picks — 174 games move out of the Elo pool
into the divergence pool — but the two deciders trade wins one-for-one on that
band, so the totals land on the same number. Tier composition on the holdout:

- tuned: STRONG 49-26-1 (65.3%, n=76), LEAN 170-147-11 (53.6%, n=328), COINFLIP 85-81 (51.2%, n=166)
- incumbent: STRONG 49-26-1 (65.3%, n=76), LEAN 85-65-4 (56.7%, n=154), COINFLIP 170-163-7 (51.1%, n=340)

**Where the schemes agree:** both pick `lean = 0.5` on every training window,
both find the out-of-sample gain indistinguishable from zero (+1 pick and +0
picks), and both leave `strong` unresolved because it cannot matter.
**Where they disagree:** nowhere material. Scheme A's pooled gain is +1;
Scheme B's is exactly 0. The agreement is itself the finding — a threshold that
mattered would not produce the same answer twice by rounding to nothing.

## In-sample vs out-of-sample gap, in those words

Moving `lean` from 1.0 to 0.5 gains **+9 correct picks in-sample over the full
2020–2025 span (+0.5 points of hit rate) and +1 correct pick out-of-sample
(+0.0 points)**. Nearly the entire in-sample gain is one season: 2020 alone
contributes +8 of the +9 (2020 goes 139-112 at `lean` 0.5 versus 131-120 at 1.0).
Drop 2020 and the effect is gone. That is the textbook shape of an in-sample
artifact, and it is why the walk-forward, which never lets 2020 into a test set,
returns nothing.

## Noise floor

With ~272 graded games per season, the standard deviation of a season's correct
picks is ~8.2. Every claim above should be read against that number:

- Season-to-season swing of the *incumbent alone*, with no parameter change:
  51.8% (2021) to 56.8% (2024) — a range of 16 picks, or two standard
  deviations, from luck alone.
- The `lean` 1.0 → 0.5 change is a paired comparison, so the relevant floor is
  tighter than the season floor: 250 games have discordant picks over the full
  span, giving a standard deviation of ~15.8 picks on the net gain. Observed
  gain: +9. Out-of-sample (2021–2025) the discordant count is 211, sd ~14.5,
  observed gain +1.
- The one band where divergence looks genuinely better than Elo, 1.0–1.5 points,
  contributes +16 picks full-span on 132 discordant games (sd ~11.5) and +14
  out-of-sample on 112 discordant (sd ~10.6). That is the strongest signal in the
  whole exercise and it is still only ~1.3 standard deviations — and it argues
  for keeping `lean` at or below 1.0, which is where it already is.

## What could change this answer

- **More data.** Detecting a genuine 1-point-of-hit-rate difference on the
  0.5–1.0 band at 80% power needs roughly four times the discordant sample —
  another ~15 seasons of NFL, or NCAAF added to the pool. Nothing short of that
  resolves it.
- **A better tiebreak.** The whole `lean` question is "divergence versus Elo",
  and Elo is currently ~49.5% — noise. If the tiebreak ever beat 50% by a real
  margin, `lean` would want to *rise*, and this sweep would be worth rerunning.
  Improving the tiebreak is a far larger prize than tuning `lean`: 926 games —
  55.6% of the sheet — are currently decided by a coin flip.
- **A real CBS frozen line.** The frozen end is still a Tuesday-morning market
  proxy. If the true CBS number is systematically staler than Tuesday, every
  observed `delta` is understated and the whole grid shifts.

## Caveats

- Single sport (NFL), single tiebreak configuration (default `EloConfig`).
- ~35 games are ungraded (no frozen snapshot); that behaviour was left alone and
  is identical across every cell, so it cannot bias the comparison.
- The sweep replays a verbatim copy of `run_backtest`'s loop rather than calling
  it, in order to tag picks with their season. The copy is validated by
  reproducing the shipped headline record exactly at the default thresholds; if
  `run_backtest` changes, this document's numbers are stale.
