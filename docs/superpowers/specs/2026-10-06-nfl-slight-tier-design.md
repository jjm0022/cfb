# NFL "Slight" Tier: Follow the Market on Any Gap

## Purpose

On the NFL board, follow the betting market whenever it differs from the frozen
CBS line by any amount, instead of treating gaps under 1 point as coin flips
decided by the CBS favorite. The college board is unchanged.

The goal is more correct NFL picks. Success is judged by the new tier's own
record in the weekly report over the rest of the 2026 season, with an early
stop declared in advance (below).

## Why, and why this overrides a recorded decision

`docs/invariants.md` says the tier thresholds stay at 2.0/1.0. That decision
rests on the 2020–2025 NFL threshold sweep, where moving `lean` to 0.5 gained
+9 picks in-sample and +1 out-of-sample
(`docs/research/2026-08-19-threshold-tuning.md`).

The owner's objection, accepted here: every historical test uses a Tuesday
sportsbook snapshot as a stand-in for the CBS line. It cannot show whether
CBS's own lines are worse than the books', which is exactly what small gaps
would expose. The only real-CBS evidence is 2026:

- CBS lines differ from the first sportsbook consensus we capture after CBS
  posts on about half of games (NFL 30 of 64, CFB 41 of 75 off by 0.5 or
  more).
- Following the closing market against the real CBS line, pool weeks 1–5,
  NFL: all gaps 32–15 (68%, 95% interval 54–80%); gaps under 1 point 15–8;
  of those, gaps crossing 3 or 7 went 10–6 and other small gaps 5–2.
- CFB went the other way (28–31 overall, 11–17 on small gaps), so CFB is not
  changed.

Caveat recorded with the decision: the rule was chosen after seeing its 2026
record, which flatters it. The early-stop rule exists for that reason.

## The rule

NFL tiers, by the gap between the frozen CBS line and the market consensus:

| Gap | Tier | Side |
|---|---|---|
| ≥ 2.0 | STRONG | market's side (unchanged) |
| ≥ 1.0 and < 2.0 | LEAN | market's side (unchanged) |
| > 0 and < 1.0 | **SLIGHT** (new) | market's side |
| exactly 0 | COINFLIP | CBS favorite (unchanged rule) |
| no market line | NO_MARKET | Elo (unchanged) |

CFB keeps today's tiers: under 1.0 is COINFLIP, decided by the CBS favorite.

"The market" is unchanged: `consensus_spread`, the median of the US books'
latest spreads, Pinnacle excluded. This is the same number the results report
uses for its "close divergence" row. The gap uses the same `delta` the tiers
already use (`league spread − consensus`), so a 0.25 gap from a median of two
books counts as SLIGHT.

## Where it lives

- `Tier` gains `SLIGHT = "slight"`.
- `Thresholds` gains an optional `slight` setting, off by default. When on,
  any non-zero gap under `lean` is SLIGHT and keeps the market's side;
  SLIGHT is never sent to a tiebreak. When off, behaviour is exactly today's.
- The per-sport choice lives where live recommendations are produced
  (`operations/recommendations.py`, used by the `report` command and the
  Discord bot): NFL passes thresholds with `slight` on; CFB passes the
  defaults. One named constant per sport, so reverting is a one-line change.
- `decide_edges`'s defaults do not change, so the backtest runner, the COINFLIP
  experiments and the weekly-win simulator replay history exactly as before.

### Key-number note

A SLIGHT pick "crosses a key number" when 3 or 7 (either sign, in home-spread
terms) lies between the CBS line and the market consensus, inclusive. Example:
CBS −3.5, market −3.0 crosses −3. It does not change the side. It is:

- added to the pick's rationale on the sheet and in bot messages
  ("crosses key number 3");
- used to split the SLIGHT row in the weekly report into
  "crosses 3 or 7" and "other".

## Displays

- **Pick sheet and Discord bot:** SLIGHT gets its own label (the bot's tier
  labels map gains an entry). Side-flip DMs and the 1-hour pick check behave
  as today; a flip caused by a small gap is a flip like any other.
- **Weekly results report and dashboard:** SLIGHT appears in the model-by-tier
  tables, split into "crosses 3 or 7" and "other". Its reference rate is
  52.1%, labelled as the 0.5–1.0 band from the 2020–2025 NFL test on
  stand-in lines. The dashboard's tier lists (filters and Model tab) gain
  SLIGHT.
- CFB never produces SLIGHT, so its displays are unchanged in practice.

## Early-stop rule (declared 2026-10-06)

Each weekly results report computes the season record of NFL SLIGHT picks
(the model's last recommendation before kickoff, graded against the CBS
line). If the upper end of its 95% Wilson interval is below 50%, the report's
findings and the Tuesday results DM carry a warning naming this spec and
saying to revert. The warning does not change any setting; reverting is the
owner's decision and a one-line change.

## Documentation

- `docs/invariants.md`: amend the "thresholds stay at 2.0/1.0" entry with the
  NFL SLIGHT exception, its reasons (stand-in lines; 2026 real-CBS evidence),
  and the early-stop rule.
- `README.md`: add SLIGHT to the tier table.

## Testing

- Decision code: with NFL settings, a 0.5 gap and a 0.25 gap give SLIGHT on
  the market's side; a zero gap gives COINFLIP with the CBS favorite; 1.0 and
  2.0 gaps give LEAN and STRONG as before. With default settings, every
  existing test passes unchanged and a 0.5 gap is still COINFLIP.
- Key-number note: CBS −3.5 / market −3.0 and CBS +6.5 / market +7.0 cross;
  CBS −4.5 / market −5.0 does not; the note never appears outside SLIGHT.
- Live recommendations: an NFL week uses the SLIGHT settings and a CFB week
  does not.
- Report: the SLIGHT rows appear with the split; the warning appears when the
  interval's upper end is below 50% and not otherwise.
- Dashboard: SLIGHT renders in the filters and the Model tab (existing
  dashboard JS test pattern).
- Bot: SLIGHT has a label.

## Out of scope

- Any change to CFB picks.
- Acting on the market after the bot's last poll (1 hour before kickoff).
- Re-running the historical threshold sweep.
