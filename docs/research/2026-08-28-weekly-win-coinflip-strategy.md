# Improving COINFLIP picks for a weekly win

**Written:** 2026-08-28
**Scope:** CFB/NFL CBS pick'em decision support, with the immediate evidence base
coming from the CFB archive. The objective here is winning at least one weekly
pool, not merely maximizing long-run ATS accuracy. No paid data was purchased,
and no 2026 outcomes were fit.

## Executive recommendation

**Do not buy a new training-data product yet.** The best next move is a
no-cost, prospective opponent-aware COINFLIP experiment. Keep the current
market-divergence decisions for STRONG and LEAN, and keep Elo as the live
COINFLIP fallback until the new rule has evidence. The likely opportunity is an
objective change: choose a side that is both defensible against the spread and
less duplicated by the pool, rather than treating every COINFLIP as an
independent accuracy contest.

For each future slate:

1. Preserve the incumbent pick and record a paper alternative that uses the
   market-based cover estimate plus expected opponent popularity.
2. Prospectively retain each bookmaker's spread, price/juice, `last_update`,
   and book identity at the submission snapshot. The current Odds API model
   keeps spread and book identity but discards price and `last_update`; the
   historical fixture shows that the feed supplies those fields
   ([model](../../src/pickem/models.py),
   [odds parser](../../src/pickem/ingest/odds.py),
   [fixture](../../tests/fixtures/odds_historical_nfl.json)).
3. Use Action Network's public **% of bets** as a free, explicitly imperfect
   crowd proxy. It is not the CBS pool: its line, population, and timestamp
   differ, and it should not be presented as a sharp-money signal
   ([Action Network CFB public betting](https://www.actionnetwork.com/ncaaf/public-betting)).
4. Retain the private-pool picks once CBS unlocks each game. Because a pick is
   visible only when that particular game starts, those observations are for
   later-week calibration only; they cannot support same-week adaptation.
   Never use a pick that was unavailable at the decision deadline.
5. Paper-test the predeclared **home-on-mean-tie** rule: when the market mean
   leaves the two sides exactly tied, choose home. This is a prospective rule
   to evaluate, not a claimed edge or an immediate live change.

The weekly optimizer should alter only COINFLIP cases and should trade away
accuracy for uniqueness only when the estimated increase in weekly prize share
justifies it. This follows the pool literature: an optimal pool strategy uses
both outcome probabilities and a model of competitors' selections, and can
accept a less popular underdog to improve first-place prospects
([Clair and Letscher, *Optimal Strategies for Sports Betting Pools*](https://www.stat.berkeley.edu/~aldous/157/Papers/clair.pdf)).

## What the frozen replay actually says

The existing market-feature and team-residual experiments are useful negative
evidence. They were season-locked, walk-forward, deterministic replays over
the 2021–2025 CFB archive; the archive completed 1,018 requests for 10,180
credits. Their historical frozen and submission lines are Odds API proxies,
not historical CBS lines.

| Frozen experiment | Result on 1,577 outer-fold predictions | Why it is not a live artifact |
| --- | --- | --- |
| Candidate 1: three market features | 50.68% (785–764) versus Elo 48.93% (758–791); 28 pushes; Brier 0.2502; paired bootstrap 95% interval [-2.07%, 5.47%] | The result failed its Brier gate. It also lost four net picks to always-home (50.94%, 789–760) and trailed frozen-line favorite (51.00%, 790–759). [Full result](2026-08-25-cfb-coinflip-result.md) |
| Candidate 2: market-anchored team residual | 51.45% (797–752) versus frozen-line favorite 51.00% (790–759), only +0.45 percentage points/+7 picks; Brier 0.2505; paired season/week bootstrap [-2.61%, 3.32%] | It was positive in only two of four test seasons and failed the +1 point, season-consistency, bootstrap, and Brier gates. [Full result](2026-08-27-cfb-coinflip-residual-result.md) |

Both candidates are therefore **NULL**. Elo remains the CFB COINFLIP tiebreak,
and neither candidate has a fitted or shipped artifact. The practical local
lesson is not that Elo is strong; it is that small refinements to market
features and team residuals have not established a reproducible ATS edge.
The reliable baseline remains the frozen line and the market divergence used
for the higher-confidence tiers.

This is also why a weekly-prize objective matters. A raw ATS result asks,
“Was this side correct?” A pool result asks, “Did this whole vector of picks
beat the other entrants, including ties and the tiebreak?” If the pool is flat
one point per correct pick with a separate tiebreak, then the joint distribution
of picks and opponent duplication matters. More data that improves a marginal
per-game probability may still be a poor purchase if it does not change weekly
placement.

## Pool-specific ROI update

The current pool inputs make the purchase case weaker, not stronger: 51 active
entrants (with at least a few more expected), approximately $200 per week, and
the full 18-week NFL regular season beginning next week and ending when the
playoffs begin. The tiebreak is the total for the Monday Night Football game.
Each opponent's pick becomes visible only when that particular game starts, so
same-week adaptation is unavailable; those picks can train a later-week model
only.

The following is a transparent benchmark, not an observed forecast. It assumes
15 games per week, incumbent and opponent entries at 53.2% overall, 55.6% of
games in COINFLIP at 49.5%, a hypothetical COINFLIP improvement to 52.5%, a
random/neutral tiebreak benchmark (the actual tiebreak is the MNF total), and
independent weeks. The implied overall rate is
53.2% + 55.6% × (52.5% − 49.5%) = **54.87%**. It does not assume that a paid
feed can deliver that lift.

| Active entries | Weekly win probability | Probability of at least one win in 18 weeks | Illustrative cumulative payout lift at $200/week |
| ---: | ---: | ---: | ---: |
| 51 | 1.961% → 2.580% | 30.0% → 37.5% | +$22.30 |
| 55 | 1.818% → approximately 2.40% | 28.1% → 35.4% | +$20.97 |

With the full 18-week season, the simple subscription break-even lift is
`cost / (18 × $200)`:

| Cost | Required weekly lift |
| ---: | ---: |
| $30 | +0.833 percentage points |
| $59 | +1.639 percentage points |

The benchmark treats the MNF tiebreak as neutral; a dedicated total forecast
could change close-week outcomes, but no such advantage has been measured.
If the value is restricted to the event “win at least one week” rather than
expected payout across all weeks, the illustrative incremental value is only
about **$15.00** for 51 entries and **$14.60** for 55 entries. This is below a
$30 purchase before considering model risk, ties, or data-quality failures.
Consequently, even with the supplied assumptions, the decision remains **do
not purchase**. Capture complete post-lock pick vectors and book-level
juice/timestamps prospectively, and use walk-forward-only evaluation to test
whether the weekly objective improves.

## No-cost plan

### 1. Make the market snapshot richer

At every submission deadline, record one immutable row per book and market:
spread, price/juice, book identity, source timestamp, capture timestamp, and
whether the row is the frozen or submission snapshot. Derive consensus and
pressure features only from rows available by the deadline. Useful predeclared
features are mean/median movement, dispersion, fraction of books moving toward
each side, and price movement while the spread is unchanged.

The Odds API documentation states that historical queries return the closest
snapshot at or before the requested time, and that historical featured markets
cost 10 credits per region per market ([official v4 documentation](https://the-odds-api.com/liveapi/guides/v4/)).
That makes the timestamp rule auditable, but it does not prove that a price or
book feature will beat the current baseline.

### 2. Estimate the crowd, then optimize the week

Use public % of bets as a prior for duplication. Use actual private-pool picks
from prior locked weeks if available; these are much better for this objective
than a generic betting-site population. For each COINFLIP, retain:

```text
incumbent probability of covering the frozen line
estimated probability that opponents select the same side
expected weekly score/prize share under each side
```

Choose the less popular side only when its estimated weekly prize value is
higher. Run the optimization over the complete slate because a single pick's
value depends on the other picks and on the tiebreak. Log both the incumbent
and alternative before results are known. Report raw ATS accuracy, weekly
rank, outright wins, ties, and prize share separately.

### 3. Paper-test the deterministic tie rule

The home-on-mean-tie rule is cheap to test and easy to audit. It must be
evaluated prospectively against the current Elo tie behavior and against
always-home. It should not be tuned after looking at 2026 outcomes. A tie rule
that changes only a few games cannot be advertised as a model improvement
without a sufficiently long log.

## Paid options, ranked

### 1. The Odds API historical price/pressure backfill — best paid candidate,
but not yet justified

This is the only paid option that directly extends the existing high-value
market signal rather than introducing another likely-correlated public rating.
The official pricing page lists 20,000 credits for $30/month and 100,000 for
$59/month ([official pricing](https://the-odds-api.com/)). Historical featured
markets are 10 credits per region per market; the archive begins in 2020 and
the response includes book-level prices and timestamps ([official v4 guide](https://the-odds-api.com/liveapi/guides/v4/)).

Known CFB budget from the completed archive: 943 submission snapshots would
cost 9,430 credits for one market and one region; adding a second region such
as the EU/Pinnacle coverage would be 18,860 credits. A full 1,018-request CFB
pass is 10,180 credits. These estimates assume the same request plan and
available event coverage; they do not guarantee that the selected books are
present for every game or that the historical book composition matches CBS.

If eventually purchased, use one narrow, predeclared experiment: book-level
juice, update timing, and book identity only; no feature shopping. Treat the
$30 plan as an experiment cost, not as a forecast of profit. There is currently
no evidence that these fields have a good chance of paying the purchase back.

### 2. CFBD ratings/advanced metrics — low-cost but lower priority

CFBD publishes API tiers from free (1,000 calls/month) through $5 for 30,000
calls, while its site separately advertises a 2026 Starter Pack at $49
([CFBD API tiers](https://collegefootballdata.com/api-tiers)). Its ratings and
play-by-play can support strictly through-week, rolling features
([CFBD CORE ratings](https://api.collegefootballdata.com/core-ratings)).

This is lower priority because public team strength, EPA/PPA, recruiting,
weather, rest, and injury information is likely already reflected in the
market; Candidate 2's leakage-safe team-residual replay was NULL. Historical
ratings also need a timestamp discipline: a season aggregate or a rating
computed after kickoff is unusable. The cheap price does not create a
demonstrated edge.

### 3. nflverse and other team-data feeds — free/audit value, not a purchase

nflverse provides useful schedule and result fields, including spreads and
rest, for cross-checking the market archive ([official schedule dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)).
Additional paid team, injury, recruiting, or weather feeds are lower priority
until the no-cost opponent-aware experiment shows that the pool can be beaten
with a stable, timestamp-safe decision rule. No such paid feed has a
demonstrated probability of paying for itself in the current evidence.

## Predeclared experiment and purchase gate

The following is the proposed gate; it is a decision rule, not a result:

1. Before the first test week, freeze the feature list, snapshot deadline,
   home-on-mean-tie behavior, opponent-popularity proxy, and optimizer. Keep
   the incumbent output beside the paper output. Do not fit or tune on any
   2026 result.
2. Accumulate a prospective log of untouched weeks. Score the incumbent and
   candidate on paired ATS results and on weekly outcomes. The per-game
   opponent picks become available only after kickoff, so use them to train
   later-week estimates and label any public `% of bets` substitute as a proxy;
   do not claim a simulated prize result is observed evidence.
3. A paid backfill is **GO** only if one named data field is available at the
   correct timestamp, the predeclared candidate beats the incumbent by at least
   1 percentage point in paired COINFLIP accuracy, the lift is not confined to
   one season/week cluster, and the weekly prize/share objective improves under
   the actual pool rules. Otherwise it is **STOP**. The existing C1/C2
   acceptance gates provide the appropriate standard for an accuracy claim,
   rather than selecting whichever historical slice looks best.
4. Even after a statistical GO, buy only if the incremental expected payout
   clears the subscription cost. If a purchase is made, use one billing period
   and freeze the test before reading its result; cancel when the gate fails.

The current recommendation is **STOP / do not purchase yet**: the final
entrant count, exact payout and tie-split rules, and a measured MNF-total
tiebreak advantage are not known, and the existing replays do not establish a
predictive edge.

## ROI calculation and missing inputs

Let `C` be the data cost, `V` the weekly first-place payout (or expected value
of the relevant prize share), `W` the remaining weeks, and `q_new` and `q_old`
the candidate and incumbent probabilities of the targeted weekly outcome.
The simple expected-value gate is:

```text
W * V * (q_new - q_old) >= C
```

For the narrower “at least one win” framing, assuming independent weeks:

```text
P(any win) = 1 - (1 - q)^W
V * [P(any win_new) - P(any win_old)] >= C
```

These are sensitivity formulas, not a forecast. Ties, shared first place,
the tiebreak, correlated picks, and a changing weekly prize require simulation
from actual rules and observed opponent behavior. To compute a real break-even
number, the parent still needs:

- final active entrants (currently 51, with at least a few more expected, and
  whether one entrant can submit multiple entries);
- weekly prize amount and how ties are split;
- confirmation of the exact scoring slate and the approximately $200 weekly
  prize;
- how the MNF-total tiebreak is scored and how close-week ties are resolved;
- the quality of the MNF-total forecast; and
- post-start opponent-pick history, which is available only after each game's
  kickoff and cannot be used for same-week decisions.

Until those values are available, “I can make the subscription back by winning
one week” is not a probability that can be responsibly estimated. The evidence
supports measuring the weekly objective first and spending only behind a
predeclared, timestamp-safe result.

## Source and evidence register

- [CFB Candidate 1 frozen result](2026-08-25-cfb-coinflip-result.md)
- [CFB Candidate 2 frozen residual result](2026-08-27-cfb-coinflip-residual-result.md)
- [Historical Odds API archive plan and credit accounting](2026-08-19-odds-api-historical.md)
- [The Odds API official pricing](https://the-odds-api.com/)
- [The Odds API official v4 documentation](https://the-odds-api.com/liveapi/guides/v4/)
- [Action Network official CFB public-betting page](https://www.actionnetwork.com/ncaaf/public-betting)
- [CFBD official API tiers](https://collegefootballdata.com/api-tiers)
- [CFBD official CORE ratings](https://api.collegefootballdata.com/core-ratings)
- [nflverse official schedule dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)
- [Clair and Letscher, *Optimal Strategies for Sports Betting Pools*](https://www.stat.berkeley.edu/~aldous/157/Papers/clair.pdf)
- [Incentive-Compatible Forecasting Competitions, *Management Science*](https://pubsonline.informs.org/doi/10.1287/mnsc.2022.4410)
- [Levitt, *How Do Markets Use Information?* (NBER)](https://www.nber.org/papers/w9422)
