# Weekly-Win Simulator

## Purpose

Decide, once, whether to change the COINFLIP rule (currently: take the frozen
CBS favorite) to a contrarian one, judged by the objective the pool actually
pays on: **finishing first in a pool week**, not raw against-the-spread
accuracy.

This is a one-off decision tool. It is a read-only command whose output is
written up by hand in `docs/results.md`, like the two earlier COINFLIP
experiments. It does not feed the pick sheet, the Discord bot, or the weekly
results report.

## What prompted this

After pool week 4 of 2026 (ranks 21, 17, 3, 22 of 53), the user asked how to
improve weekly performance. Two facts from the stored pool picks:

- On COINFLIP games the pool's majority took the CBS favorite 55 of 66 times
  (83%). Our COINFLIP rule also takes the favorite, so we mostly duplicate the
  crowd where we have no edge.
- A hindsight replay that took the pool-minority side on every COINFLIP game
  would have scored 54 points over weeks 1–4 instead of 60, and ranked worse in
  three of four weeks.

That replay measures only the cost in points. A contrarian rule is meant to
trade a little average score for more variance and therefore more first-place
finishes, and with a ~2–4% weekly win rate a season of real weeks cannot
resolve that. Simulation can, under stated assumptions.

## Assumptions (fixed in advance, not tuned)

- **Per-game probabilities by tier.** The model's side wins with probability
  0.637 on STRONG and 0.542 on LEAN (the NFL 2020–2025 backtest,
  `docs/results.md`). COINFLIP and NO_MARKET games are 0.5 for either side.
  2026 outcomes are not used to set any probability.
- **The tier and the model's side** for a game are the last
  `recommendation_history` row generated before kickoff, the same rule the
  results report uses for "model". If a game has none, our actual submitted
  pick is used and the game is treated as 0.5; the output counts such games.
- **Opponents are fixed.** The other entrants keep their real picks from
  `pool_picks`. A missing pick is always wrong.
- **Ties for first** share the win equally (a two-way tie counts as half a
  win). The real Monday-night-total tiebreak is not modelled.
- **Pushes** are not modelled; every game has a winner. CBS lines on the
  boards so far are half-points, so this costs nothing in practice.

## Rules compared

Every rule gives our entry the model's side on STRONG, LEAN and NO_MARKET
games. Rules differ only on COINFLIP games:

| Rule | COINFLIP side | Playable before kickoff? |
|---|---|---|
| current | the CBS favorite (home when the CBS spread ≤ 0) | yes |
| underdog | the CBS underdog | yes |
| minority | the side fewer other entrants picked; the favorite on an exact split | no — ceiling |
| lopsided underdog | the underdog when ≥ 70% of other entrants who picked took the favorite, otherwise the favorite | no — ceiling |
| optimal mix | chosen by search, below | no — ceiling |

The 70% threshold is fixed here and must not be changed after seeing results.
Rules marked "ceiling" use pool picks that are hidden until each kickoff, and
the output labels them as such.

## How one simulated week works

1. For each game in the pool week (both boards), draw which side wins, once
   per simulated week, using the probabilities above. The draw is shared by
   every entrant.
2. Score every entrant: one point per correct pick.
3. Our entry's score depends on the rule. A rule's result for that draw is a
   win share: 1 if we beat every other entrant, 1/k if we tie with k−1 others
   for first, else 0. Also record our score and rank.
4. Repeat for N draws (default 20,000) from a seeded generator (default seed
   fixed) so reruns are identical.

All rules are evaluated on the **same** draws (common random numbers), so the
differences between rules are not draw-to-draw noise.

Implementation note: encode each entrant's picks and each draw's winners as
bitmasks over the week's games (plus a "picked" mask for missing picks); a
score is a popcount. The other entrants' best score and the count of entrants
holding it are computed once per draw, so evaluating a rule is one popcount
and one comparison per draw. Standard library only (`random`, `int.bit_count`);
no new dependencies.

## The optimal-mix search

Start from the current rule. Repeatedly try switching one COINFLIP game's side;
keep the switch if the win chance rises; stop when no single switch helps
(coordinate ascent). Safeguards:

- **Held-out evaluation.** The search uses one set of draws (search seed); the
  reported win chance comes from a separate set (evaluation seed) it never saw.
- **Explained.** The output lists each game the search switched, with the
  CBS spread and the pool's favorite share, so we can judge whether a simple
  playable rule would capture most of the gain.

## Command and output

```
uv run pickem simulate-weekly-win --season 2026 \
  [--pool-weeks 1-4] [--draws 20000] [--seed N] [--entry-name Jota] [--db PATH]
```

- Default weeks: every imported pool week of the season. Default entry name:
  the existing `config.DEFAULT_ENTRY_NAME`.
- Read-only: it never writes to the database.

Per pool week, one row per rule: average points, win chance, top-3 chance,
median rank. Below the table: games with no stored recommendation, and the
optimal mix's switched games.

Season summary, one row per rule: average weekly win chance, and the chance of
at least one weekly win in an 18-week season at that rate
(`1 − (1 − p)^18`). A reference line shows a no-edge entrant: 1/53 per week
(using the actual entrant count).

## Code layout

- `src/pickem/backtest/pool_sim.py` — pure: the week input type, the rules,
  the simulation, the search, and the summary. No I/O, no database access.
- Loading reuses the stores and the "last recommendation before kickoff" logic
  the results report already uses; the loader lives beside the CLI command
  (or in `operations/`), not in the pure module.
- `src/pickem/cli.py` — the command: load, run, print.

## Testing

Pure-module tests on small hand-built weeks:

- A two-way tie for first gives each a half win.
- Two rules that produce identical picks produce identical results.
- Every other entrant on the favorite in every COINFLIP game: the underdog
  rule has a higher win chance than the current rule, at the same average
  points (both sides are 50/50, so going contrarian costs nothing on average
  under these assumptions).
- With many draws, STRONG and LEAN games come out near 0.637 and 0.542.
- The same seed gives byte-identical results.
- The search never ends below its starting rule, no single switch improves its result,
  and on a 3-COINFLIP week it finds the best of all 8 combinations (checked
  by brute force).

One end-to-end test runs the real command against a small temporary database
and checks the printed tables, and that the database is unchanged.

## Out of scope

- Any change to live picks, the pick sheet, the bot, or the results report.
- Modelling the Monday-night-total tiebreak.
- Predicting pool picks before kickoff. If a ceiling rule wins clearly and the
  playable rules do not, that is a separate design.
