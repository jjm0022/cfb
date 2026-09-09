# Results — what do we actually know?

Every measured result, with what each one does and does not establish.
These are the numbers the strategy rests on.

Split out of `HANDOFF.md` on 2026-09-09; content unchanged.

## The phase-exit result (2026-08-19)

`pickem backtest --from 2020 --to 2025`, NFL, 1,663 graded games:

```
overall: 866-762-35 (53.2%, 95% CI 50.8%-55.6%)
  strong     174-99-2    (63.7%, CI 57.9%-69.2%)
  lean       244-206-12  (54.2%, CI 49.6%-58.8%)
  coinflip   448-457-21  (49.5%, CI 46.3%-52.8%)
```

**Read the tiers, not the overall number.** The overall rate mixes every game
including the ones with no signal, which is not how the sheet is played.

- **STRONG (>=2.0 pts of divergence) is the finding.** 273 decided picks, 63.7%,
  and the whole interval sits above 50%. Scoring is flat with no vig, so 50% is
  the real bar, not a juice-adjusted breakeven.
- **COINFLIP landing at 49.5% is the control that makes the rest credible.**
  Those are games where divergence said nothing and the Elo tiebreak picked.
  Sitting on 50% is evidence the measurement carries no systematic bias.
- **The ordering is monotonic and was not fitted.** The 2.0/1.0 thresholds were
  guesses made in Phase A before any data existed.
- **Every season agrees.** STRONG by season: 60.7, 62.1, 62.5, 68.2, 65.6,
  65.1. Six for six above 60%, so no single season carries it.

### Tier volume is the other half of the result

The hit rates alone overstate what this buys, because the league makes you pick
**every** game. What matters is how much of the sheet each tier covers:

| tier | share of sheet | rate | extra correct picks/season |
|---|---|---|---|
| strong | 16.8% (273 decided) | 63.7% | **+6.2** |
| lean | 27.6% (450) | 54.2% | +3.2 |
| coinflip | **55.6% (905)** | 49.5% | -0.8 |
| | | | **+8.7 total** |

STRONG fires on about **2.1 games per week** out of ~12.4 decided. So the
realized season is the overall 53.2%, worth roughly **+8.7 correct picks over a
coin flip per season**. Against the ~8.2-pick standard deviation of a 272-game
season that is about one sigma: a real edge that wins more often than not, not
a dominant one.

**What this implies for Phase B — read this carefully, an earlier version of
this analysis got it wrong.** The tempting reading is "divergence clears the
noise floor, so the modeling stack is unnecessary." That is too quick.
Divergence works where it fires but is **silent on 55.6% of the sheet**, and
those coinflip games sit at 49.5% — pure noise. That silent majority is now the
largest untapped pool: moving coinflips from 49.5% to even 52.5% would add
roughly what the entire STRONG tier contributes today.

So the honest framing is that Phase A succeeded at what it measured and left
the bigger half of the problem untouched. Modeling (opponent-adjusted EPA,
injury, weather) is best justified **as a replacement for the Elo tiebreak on
coinflip games**, not as a competitor to divergence — which it should never
override (see `docs/invariants.md`).

Threshold tuning was the cheaper thing to try first, and it has now been tried
and returned nothing (2026-08-19): the boundary between the tiers cannot be
moved to any profit, because `strong` changes no pick at all and `lean` only
trades games between divergence and a tiebreak that is itself a coin flip.
That closes the cheap option and leaves the tiebreak as the only lever with
headroom.

**What the number does NOT establish.** The frozen line is proxied by a Tuesday
market snapshot, not by a real CBS line, because historical CBS numbers were
never recorded. If CBS's number sits systematically off the Tuesday market, the
real edge differs from this one in a direction this backtest cannot see. That
becomes checkable within weeks, once real pastes accumulate next to live
`poll-odds` snapshots — and it is the single most valuable thing left to do.

## The calibration result (2026-08-20)

**The assumption the backtest rests on survived its first real test.** CFB 2026
week 1: the saved CBS page ingested, polled against the live odds feed 11
seconds later, and calibrated on all 15 games.

```
bias (mean residual):         -0.07 pts
dispersion (mean |residual|):  0.33 pts
agreement: 73.3% exact, 80.0% within 0.5, 93.3% within 1.0
```

A bias of -0.07 is as close to unbiased as 15 games can show. For comparison,
the archive-vs-nflverse cross-check that validated the joins came in at 0.141
mean ABSOLUTE difference; this is 0.33 absolute, wider but on a fifteenth of
the sample and on a sport with thinner books.

**What it does NOT establish.** (1) It is CFB, and the phase-exit result is
NFL; the two slates have different book coverage. (2) It is one week, n=15 —
one more UCLA-sized disagreement would move the bias by 0.17. (3) It compares
CBS against the market *at paste time*, which is the right comparison for the
proxy but says nothing about whether CBS's number was frozen days earlier or
minutes earlier. (4) Most of the agreement is CBS simply copying the market:
11 of 15 matched exactly, so the sample of informative disagreements is 4.

The one real disagreement was UCLA at California — CBS -1.5, market +1.0, a
2.5-point gap and the only STRONG edge on the sheet. That is the strategy
firing exactly as designed, on the first real week it ever saw.

## Why the tiebreak cannot rescue coinflips (2026-08-20)

Measured after the CFB backfill, training on 2020-2024 and holding out 2025
(807 games):

```
straight-up winner picked:  64.8%      <- the rating knows who is better
mean |projected margin|:     9.35
mean |actual margin|:       16.40      <- projections are compressed
mean signed error:          -2.23
```

The compression looks like a mis-scaled constant, and it is not. A
least-squares fit of actual margin on rating difference over the training
seasons returns `points_per_elo = 0.0341` against the current default of
`0.0400` — i.e. the honest fit is *more* compressed, not less, and on held-out
2025 it moves mean absolute error only 14.45 -> 14.25. **Do not "fix" the
compression by rescaling.** A conditional mean from a weak predictor shrinks
toward zero; that is correct behaviour, not a calibration error.

The consequence is structural. `tiebreak_side` compares a shrunken projection
against a sharp market spread, so on any sizeable spread the projection sits
inside the number and the rule resolves to "take the underdog" — 11 of 15
coinflips on the 2026 CFB week 1 sheet. Since ATS underdogs are close to a coin
flip, this reproduces exactly the 49.5% COINFLIP rate the NFL backtest measured.

**So the coinflip problem is not a tuning problem and cannot be closed by a
better-fitted Elo.** Beating it needs a predictor strong enough that its
conditional mean can cross a sharp line — which is the Phase B modelling
question, not a parameter change.

**Resolved 2026-09-09, and this analysis is why.** Two fitted candidates then
confirmed the prediction by returning NULL. The response was not a stronger
predictor but removing the mechanism: `COINFLIP` now takes the frozen-board
favorite, which has no crossover and so cannot be dragged onto the dog by a
shrunken projection meeting a sharp number. The "11 of 15 coinflips on the dog"
pattern above was the symptom being treated. This does not beat the market — it
scored 51.00% against Elo's 48.93%, both noise around 50% — it just stops
paying a modelling cost for a rule that was losing to always-pick-home. Elo is
untouched for `NO_MARKET`, where no sharp market number exists to sit inside.

## Bugs the real data found, and what they teach

All four were found by running against real APIs and real stored data, not by
tests. Three of them could not have been caught by fixtures, because the
fixtures encoded the same wrong assumption as the code.

1. **`_kickoff` parsed `gameday` only**, so every NFL kickoff was midnight UTC.
   Phase A deferred this as "operationally inert" and it genuinely was, until a
   feature was built that depended on kickoff times. *A deferral is only valid
   against the code that exists when it is made.*
2. **`run_backtest` kept one arbitrary opener per game** (a last-wins dict
   comprehension). Correct with one nflverse line per game; silently wrong the
   moment a multi-book snapshot arrived. *Found by reading the code while
   planning, not by any test — the plan document paid for itself here.*
3. **The 5-minute submission lead** collided with the sources' kickoff
   disagreement. *Found by buying one snapshot for 10 credits and diffing it
   against the schedule, before spending 9,000 more.*
4. **The missing "Washington Football Team" alias** silently dropped that
   team's games, and only surfaced because an unrelated empty-writer crash
   halted the run. *The loud crash was lucky; the silent alias gap was the real
   bug. Prefer checks that fail loudly over ones that produce a
   complete-looking result.*

The general lesson, worth keeping: **spend a small amount of real money early to
check assumptions, before spending a large amount.** Task 6 of the backfill plan
was ordered cheapest-verification-first for this reason, and it is why the total
damage from all four bugs was ~110 wasted credits.
