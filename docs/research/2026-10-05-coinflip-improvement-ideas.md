# New ideas for the COINFLIP picks

**Written:** 2026-10-05
**Scope:** Research only. No code was changed and nothing was fit or tuned on 2026
results. The question: what evidence-backed ideas can we test, with data we hold
or can get cheaply, for (a) choosing a side on COINFLIP games, or (b) scoring
better on them for the *weekly-win* goal? Ideas already tried or settled
(`docs/results.md`, the two CFB COINFLIP results, the 2026-08-28 weekly-win
document, the threshold-tuning result, `docs/invariants.md`) are not repeated
unless the angle is different, and the difference is stated.

**How the numbers in this document were produced.** Figures marked "(this
session)" came from quick read-only queries against the local database and the
public nflverse schedule file. They are descriptive counts on 2020-2025 history,
not runs of the official replay harness, and they use the same proxies the
earlier backtests used (an early-week market snapshot standing in for the CBS
line). Treat them as sizing, not as results.

**Source standard.** Journal abstracts and working papers are cited by link.
Several publisher pages refused automated access (403), so for those I read the
abstract on an index page (RePEc, SSRN, arXiv) and say so. I did not read full
texts except Clair & Letscher and Sinkey & Logan. Where no primary source exists
the claim is labeled. Preprints are labeled as preprints.

## Executive summary

**The honest frame first.** A COINFLIP is a game where the CBS number equals
today's betting-market number. Beating it means beating the betting market, and
decades of papers show the market is hard to beat; the few documented biases are
small, mostly measured on older data, and often fail to survive after the
authors' own cost adjustments (see "Ideas considered and rejected"). Our own
two fitted attempts came back NULL. So none of the ideas below should be expected
to produce a visible jump in COINFLIP accuracy. The largest plausible gains are
in how we play the *pool*, not in predicting games: the simulation on 2026 data
moved the weekly-win chance from 2.6% to 4.9% by changing which side we take,
but only if COINFLIP favorites cover 50% of the time; at 51% the gain is 2.9% to
4.3%, and it reverses near 53% (`docs/results.md`).

Terms used below:
- **Spread / hook.** The number of points the favorite must win by. A "hook" is
  the half point (3.5 instead of 3), which removes the possibility of a tie
  (a "push", where the bet is refunded). CBS lines are always half points; we
  confirmed all 139 CBS lines stored for 2026 end in .5.
- **Key numbers.** Football margins are not smooth: NFL games end with the
  winner ahead by exactly 3 about 15.4% of the time and by exactly 7 about 7.8%
  (1,424 NFL games, 2021-2025, local database; see the correction under Idea 1). A half point that
  crosses one of those numbers is worth far more than a half point elsewhere.
- **Vig / juice.** The bookmaker's fee, built into the price. A standard spread
  bet pays -110 on both sides (risk 110 to win 100). If one side is -120 and the
  other +100, the book is leaning that way.
- **Closing line value.** Beating the final market number. Our STRONG and LEAN
  picks are exactly this: the CBS number was frozen early, so when the market
  later moves away from it we hold a better number than the market's close.
- **Sharp book.** A bookmaker that takes large bets from professionals and moves
  its line in response (Pinnacle is the usual example), as opposed to a
  "recreational" book that mostly serves casual bettors.

**Ranked shortlist** (ranking weighs evidence quality, plausible size, and cost;
all five are free to test or nearly so):

1. **Treat key-number hooks as real signal.** When the market sits exactly on 3
   or 7, CBS must post a half point on one side. That half point is worth about
   about +7.7 points of win probability at 3 and +3.9 at 7 for the side that receives it,
   far more than the 1-2 points our "coinflip" label assumes. About one NFL game
   in five is in this situation. Best evidence of the five (it is arithmetic on
   1,400 recent games, not a market anomaly). Small payoff: roughly +3 to +4
   correct picks per NFL season.
2. **Learn how the pool picks, then be contrarian only where the crowd is most
   lopsided.** Upgrades the plain "take the underdog" rule using our own 53
   entrants' picks. Biggest plausible payoff of the five; evidence is a 2007
   operations-research paper plus our own simulation, and it is only testable
   prospectively.
3. **Make late-kickoff COINFLIP picks standings-aware.** Picks lock at each
   game's own kickoff, so for Sunday-night, Monday-night and late-Saturday games
   we can see the standings first. When trailing, differentiate; when leading,
   stay with the crowd. New angle, plausible, no direct literature.
4. **Optimize the Monday-night tiebreak guess.** Two of the four 2026 weeks had a
   tie for first on points, and in one of them two entrants gave the identical
   tiebreak guess. Not a COINFLIP idea strictly, but it decides the same
   objective. Needs the exact tiebreak rule from the owner.
5. **Price (juice) and Pinnacle: start capturing, decide later.** Price is
   discarded today. NFL closing prices exist for free in the nflverse file; CFB
   and Pinnacle history would cost credits. Evidence is weak and the two
   plausible readings of a price tilt predict opposite picks. Low expected value,
   mainly worth doing because collection is cheap and cannot be reconstructed
   later.

**New measurement relevant to the pending underdog-versus-favorite decision
(this session).** The 2026-10-05 simulation said the underdog rule helps only if
COINFLIP favorites cover at about 51% or less, and noted no NFL rate had been
measured. Using the early-week proxy line, the frozen-board favorite on COINFLIP
games covered **50.1% in the NFL (453 of 904 decided, 2020-2025)** and **51.3% in
CFB (1,029 of 2,005, 2021-2025)**. The NFL figure swings from 44% to 57% by season
(44.4, 47.4, 45.5, 54.6, 56.5, 50.3), a reminder of how noisy a 150-game season
is. Both sit under the 52% reversal point, but neither is separable from 50%.
This does not settle the decision; it says the simulation's break-even range is
where the data lives.

## Idea 1: Key-number hooks (price of a half point)

> **Correction (2026-10-05, after review).** The first draft of this section
> counted only games the *home* team won by exactly 3 or 7, which roughly halves
> the true rate. Both sides counted, our local database gives NFL exactly-3 at
> 15.4% and exactly-7 at 7.8% (1,424 games, 2021-2025), and CFB 10.6% and 8.7%
> (3,942 games). The figures below are corrected; rates conditional on the
> market line (for example "when the line is 2.5-3.5") were not re-measured and
> are removed.

**What it is.** Today a COINFLIP is any game where CBS and the market differ by
less than 1 point, and we take the favorite. But a 0.5-point difference is not
equally valuable everywhere. Example: the market is Home -3 (home must win by
more than 3; exactly 3 is a refund). CBS cannot post -3, so it posts -3.5 or
-2.5.
- CBS -3.5 home: the road team getting +3.5 now *wins* if the home team wins by
  exactly 3. Fair chance for that road side: 50% of the non-tie games, plus the
  15.4% of games that land on exactly 3, so about 0.5 + 15.4%/2 = **57.7%
  against 42.3%**. That is larger than the whole LEAN tier's measured rate
  (54.2%), hiding inside COINFLIP, and the current favorite rule takes the
  *losing* side of it whenever CBS posts the favorite at 3.5.
- CBS -2.5 home: the home side wins when it wins by exactly 3, same edge for the
  favorite instead.
- Why this is only half-new: the earlier test of a lower LEAN cutoff (0.5) found
  the 0.5-1.0 band at 52.1% (506 games), which is what you would expect if about
  45% of them touch a key number and carry a large edge while the rest carry
  about +1. That test lumped all 0.5 gaps together and had no power to separate
  them. The different angle here is to apply the 0.5 rule **only when the
  market is on or straddles 3 or 7**, which is where the value is predicted to
  be.

**Evidence and its strength.**
- The margin pattern is our own data: 15.4% of NFL margins on exactly 3 and
  7.8% on 7 (2021-2025, local database, both sides counted).
- The pattern is known in the literature: the standard normal model for NFL
  margins assumes a smooth distribution and misses it
  ([Stern 1991, American Statistician](https://www.semanticscholar.org/paper/On-the-Probability-of-Winning-a-Football-Game-Stern/c6de3d74feb7cc2879c393bee1d59b5b575db2a0)).
  For college football, Sides and Harvill build a correction that re-weights
  common margins
  ([arXiv 2212.08116, preprint](https://arxiv.org/abs/2212.08116)); I read the
  abstract only.
- In CFB our 2021-2025 data show exactly-3 margins at 10.6% and exactly-7 at
  8.7% (both sides counted), so the half point is worth about +5.3 at 3 and +4.4
  at 7 in CFB.
- Strength: high that the arithmetic holds **if integer market lines are fair**
  (pushes refunded, equal price both sides). That is the one assumption to test.
  Not evidence of a market anomaly; it is a correction to how the gap is
  measured.

**How often it applies (this session).** The market consensus sits exactly on 3
for 15.1% of NFL games and on 7 for 6.1% (CFB: 6.7% and 4.6%), 2020-2025. In
2026 the saved data show the pattern directly, in both directions: CBS posts
3.5 against a market 3 (MIA at LV, NE at SEA) and 2.5 against a market 3 (CHI at
CAR, DAL at NYG). Under the proxy-versus-proxy measure used in earlier
backtests, 247 of 540 non-zero NFL COINFLIPs and 350 of 1,253 CFB ones involve 3
or 7; because live CBS can never equal an integer, the live share is higher than
the proxy count suggests.

**How to test with our data.**
1. Mechanism check (no COINFLIP sample needed). Using nflverse closers 1999-2025
   and the CFB archive: among games whose market line is exactly 3 or 7, does the
   favorite cover about as often as the underdog among non-pushes, and does the
   exactly-3/7 margin rate match the figures above? **Pass:** the favorite share
   of non-push results is within 47-53% (the 95% band at about 1,000 games) and
   the exactly-3 and exactly-7 rates are at least 8% and 5% (NFL) and 5% and 4%
   (CFB) in the most recent five seasons.
2. Prospective paper pick from the next pool week, logged beside the live pick:
   follow the hook side when the market is on 3 or 7 and CBS differs by 0.5;
   otherwise leave the favorite rule. Declared in advance, no cutoffs tuned.
   Retrospective hit rate is not the pass test because 247 games has a standard
   error of about 3 points; **pass** is (1) plus no season-level sign reversal
   large enough to reject the predicted +3 to +4 at the 95% level across the
   pooled 2020-2025 proxy sample.
3. Cost: free.

**Main way it could fool us.** (a) Integer lines may not be fair: books often
shade the price (-3 at -120) instead of moving to 3.5, so a market "3" can really
mean about 3.25, which shrinks or erases the edge (this is why Idea 5 matters).
(b) The proxy for the CBS line was a market snapshot, so historical gaps do not
perfectly reproduce what CBS posted. (c) Sizing: with the corrected margin rates,
expected gain is roughly 1.5 points of accuracy on all NFL games and about 0.5
on CFB, about +3 to +4 correct picks per NFL season: too small to see in a backtest, so the case rests on the
arithmetic holding.

## Idea 2: Learn the pool's habits, be contrarian only where it pays

**What it is.** The plain underdog rule changes every COINFLIP pick. The
simulation's "optimal mix" (a hindsight ceiling) took the underdog on only 8-12
of the weekly COINFLIPs, where 48-92% of the pool sat on the favorite, and
reached 5.7% against the underdog rule's 4.9%. A model that predicts how
crowded each side will be, before kickoff, would capture some of that gap and
would also limit damage if favorites turn out to cover more often.

**Evidence and its strength.**
- Clair and Letscher show that in a winner-take-all pool the best picks
  maximize the ratio of "our chance of being right" to "share of entrants on
  that side," and that all-favorites is the wrong play in large pools. They
  modeled the crowd from published pick percentages (they used ESPN's) and
  found more than half of games had a favorite with over 80% of the pool
  ([Operations Research 2007](https://pubsonline.informs.org/doi/abs/10.1287/opre.1070.0448);
  I read the full text from the
  [author copy](https://www.stat.berkeley.edu/~aldous/157/Papers/clair.pdf)).
  Strength: strong theory and a field test, but pools of 400 to 200,000 and free
  entries; ours has 53.
- Our own data: 62% of individual COINFLIP picks in the pool were on the
  favorite, and most of the pool was on the favorite in 55 of 66 COINFLIP games
  (`docs/results.md`, simulation section).
- Same-direction hints that favorites are slightly over-bet by the public,
  which is the reverse of what we would need for "favorites cover more":
  [Paul and Weinbach (2011)](https://ideas.repec.org/a/taf/apeclt/v18y2011i2p193-197.html)
  find big and road favorites draw more than half the bets and that betting
  against that public was profitable on NFL spreads (sample size and years not
  visible in the abstract); [Levitt](https://www.nber.org/papers/w9422) argues
  bookmakers do not balance their books because bettors' preferences are
  predictable. Both are older and about betting, not pools.

**How to test with our data.**
- Data: the pool picks table (every entrant's pick on every game after lock, all
  of 2026 so far: weeks 1-4, 53 entrants) and the pool results table. This is
  the only place pool behavior lives, and it exists only for 2026, so the test
  must be **walk-forward and prospective**: build the crowd model from weeks
  1..N, freeze it, score weeks N+1 onward in the existing weekly-win
  simulation. Do not choose cutoffs by looking at the same weeks they are scored on.
- Crowd model: predict the share of entrants on the favorite from spread size,
  home or away, sport, kickoff slot, and (CFB) a simple team-prominence flag.
  Predeclare one rule: take the underdog only when predicted favorite share is
  at least 70% (the level the simulation's own switching pattern suggested, 60-80%).
- **Pass:** (1) predicted shares beat "always 62%" on out-of-sample error in at
  least 4 of the next 6 weeks, and (2) simulated weekly win chance is at least
  +0.3 points above the plain underdog rule when favorites cover 50% and 51%,
  and no more than 0.3 points below the favorite rule at 53%.
- Cost: free.

**Main way it could fool us.** Four weeks of one pool; entrants change habits as
the season goes (the top finishers' picks are a visible, learnable thing, so the
crowd may adapt to us). The simulation holds opponents' actual picks fixed, so
it cannot capture them responding. The hindsight ceiling is, by construction,
an overfit number; only the prospective result counts.

## Idea 3: Make late COINFLIP picks standings-aware

**What it is.** The 2026-08-28 document says same-week adaptation is
unavailable because a pick is visible only when its game starts. That is true
for the first game of the week, but not for later ones. Picks lock at each
game's own kickoff, and results of earlier games are known by then. So by the
time Sunday night, Monday night, or a late-Saturday CFB game kicks off, we can
see everyone's points from the games already played. Example: if two entrants
lead us by 2 with only the Monday game left, taking the favorite (what most
entrants do) cannot win us the week unless the favorite loses, so the useful
play is the one that wins only when the field's common pick loses. If we lead,
the crowd's pick is the safe one. This is "chase or protect," the usual logic
of winner-take-all contests, applied to the games that come last.

**Evidence and its strength.** The structure (per-game lock, picks revealed at
kickoff) is from our own runbooks and notes. I found **no peer-reviewed source
that addresses sequential information in a pick'em pool**; Clair and Letscher
assume all picks are made before play. So this is a plausible idea with
mechanism but no external evidence, labeled as such. Roughly 3-6 of the
weekly COINFLIP picks fall in the late slots (the two prime-time NFL games and
the later Saturday CFB games).

**How to test.**
- Data: pool picks (every entrant's pick and whether CBS marked it correct,
  which lets us rebuild standings after each kickoff) plus the games table for
  kickoff order. Check first that the CFB kickoff times are real, not the
  placeholders the CBS import writes.
- Extend the existing weekly-win simulation with a rule that reads the standings
  at each late game's kickoff. Predeclare: if trailing the leader by 1 or more
  and the leader's likely pick is the favorite, take the underdog; otherwise,
  take the favorite.
- **Pass:** at least +0.5 points of weekly-win chance over the better of the
  two static rules (always favorite, always underdog) in the 2026 weeks, in a
  majority of weeks, and the effect survives a favorite-cover assumption from
  50% to 53%.
- Cost: free.

**Main way it could fool us.** The effect is only on a handful of games a week,
so it can be at most a small multiple of the late-game share of the sheet. We
cannot see the leaders' pick on the very game in question until after
kickoff, so the rule rests on a guess about it. And standings are only known
exactly when a game kicks off; picks can be entered earlier, so the owner must
be willing to hold late COINFLIP picks until shortly before kickoff (the bot
already re-polls the market at 12, 6, 2 and 1 hours before, which would be
the natural place to decide).

## Idea 4: Optimize the Monday-night tiebreak guess

**What it is.** Winning a week often comes down to a tie on points broken by
the Monday-night total guess. In 2026 so far, the top score was shared by 3
entrants in week 1 (10 points, partial week), was unique in weeks 2 and 3,
and was shared by two in week 4 (22 points), and in week 4 both guessed 47.
Entrants' guesses cluster tightly (median 43-49.5, with outliers up to 825 and
75). A guess shaded slightly away from the crowd's cluster, but still centered
on the market total, wins ties more often than the crowd's number does.

**Evidence and its strength.** Clair and Letscher state that the pools they
entered all broke ties with the Monday-night score, treat it as independent of
the picks, and do not optimize it. I found no published treatment of optimal
tiebreak guessing. Support for the guess's center is the market total itself
(the totals line is an accurate average predictor but, per Kain and Logan,
a weaker predictor of totals than the spread is of margins:
[J. Sports Economics 2014](https://ideas.repec.org/a/sae/jospec/v15y2014i1p45-63.html),
abstract only; and nflverse has the actual totals and total lines for 2020-2025).
Strength: mechanism-level; unquantified.

**How to test.** (1) Measure how often a simulated finish for us ends in a
points tie for first (the weekly-win simulation already splits ties, so the
count is available; the benefit scales with it). (2) Fit the spread of the
actual total around the posted total on 2020-2025 Monday-night games (free,
nflverse). (3) Replay the four 2026 weeks: for the entrants tied at the top,
compare who wins under a few predeclared guess rules. **Pass:** the rule wins
more than its fair share of simulated ties in at least 3 of the 4 weeks and
across the 2020-2025 total distribution, **and** the owner confirms the exact
tiebreak rule (closest guess or closest-without-going-over; what happens on an
exact tie; whether the tiebreak game is always Monday night). Cost: free; the
only paid piece would be adding the totals market to the odds poll, which is
1 credit per request.

**Main way it could fool us.** Zero evidence until the rule is known. The
benefit is nonzero only in weeks where we are tied at the top, which is a small
fraction of weeks, and four weeks is too few to measure that fraction.

## Idea 5: Price (juice), timing, and Pinnacle: capture now, decide later

**What it is.** We store only the spread each book posted. Two things we throw
away might say which side of a COINFLIP is likelier:
1. **Price tilt.** When the market spread equals CBS's, one side can still be
   priced at -120 and the other +100. Read as an efficient market, the tilted
   side is likelier (no-vig 52.2% against 47.8% in that example). Read the way
   Levitt reads it, the price is shaded to exploit public bias, so the shaded
   side is the one to fade. The two readings predict opposite picks.
2. **Sharp versus recreational books.** Pinnacle in particular moves with
   professional money; a gap between its number and the US median might point
   the way. The odds service puts Pinnacle in its EU region and US books in the US region
   ([book list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)).

**Evidence and its strength.**
- Weak and mixed. [Franck, Verbeek and Nüesch (2010)](https://ideas.repec.org/a/eee/intfor/v26yi3p448-459.html)
  find a betting exchange predicts soccer outcomes better than bookmakers;
  [Kaunitz et al.](https://arxiv.org/abs/1710.02824) (preprint, soccer) profit
  by betting where one book's price differs from the consensus of many, but
  report being limited by the books for winning; neither is about NFL or college
  spreads. A well-known claim that Pinnacle's closing price is the best
  estimate of the true probability appears in bookmaker and tout pages; I found
  no peer-reviewed NFL or college test, so treat it as unsupported.
- Our own spike (invariants file): over 2021-2025 no US book was sharper than the
  US median. Pinnacle was not in that archive (we bought the US region only;
  confirmed this session, zero Pinnacle rows in the 2020-2025 proxy data), so
  the comparison is untested for Pinnacle. Live Pinnacle collection started
  2026-09-25 and covers about 60 games so far.
- A paper on how much late betting is informed finds it is not: nearly a
  quarter of NFL bets arrive in the last hour and appear to be recreational, and
  betting against that late favorite loading earned significant profits
  ([Paul and Weinbach 2011, Int. J. Sport Finance](https://ideas.repec.org/a/jsf/intjsf/v6y2011i4p307-316.html)).
  That argues against "decide later to catch late information," and for
  keeping the early snapshot we already take.

**How to test.**
- NFL price, free: the nflverse schedule file has spread prices and moneylines
  for 2020-2025 (the data dictionary states what the fields are, not which book
  or what time, so provenance is unknown;
  [dictionary](https://github.com/nflverse/nfldata/blob/master/DICTIONARY.md)).
  About 86% of games have a price other than -110 on one side (this session).
  Predeclare one sign (follow the shorter-priced side) and score it on NFL
  COINFLIPs, along with a hold-out check by season.
- CFB price: needs a re-purchase. The archive did not keep the raw responses,
  and the odds service charges 10 credits per request per region per market for
  history ([guide](https://the-odds-api.com/liveapi/guides/v4/)); a pre-kickoff
  snapshot for every CFB game week was 9,430 credits and about 9,300 for NFL,
  so one month of the 20,000-credit plan (about $30) would cover both sports
  for the US region. Adding Pinnacle's EU region would double that, so a
  Pinnacle backfill would be a second purchase for one sport at a time. The historical response carries a
  last-updated time only per market, not per book.
- Going forward, store price, last-update time and Pinnacle on every poll
  (free: the poll already requests them; we simply do not keep them), then
  evaluate on a full season.
- **Pass (predeclared):** at least 52.5% on at least 400 decided COINFLIPs for
  one sign, positive in at least 4 of 6 seasons.
- Honest power: if 20% of COINFLIPs show a usable tilt, the NFL test has about
  150-200 games and a standard error near 4 points; it could only detect a
  very large effect. Do not buy history on this evidence.

**Main way it could fool us.** Tested both directions on one sample, so a
"significant" sign proves little unless it was declared first; the nflverse
price has unknown source and timing; and prices at the market's close embed
information we would not have at the time CBS froze its number.

## Ideas considered and rejected

| Idea | Why it is set aside |
| --- | --- |
| Home underdogs / "bet the home side" | Szalkowski and Nelson ([arXiv 2012](https://arxiv.org/abs/1211.4000), not peer reviewed, 2002-2011) report home underdogs at 53.5%; Shank ([J. Economics and Finance 2018](https://ideas.repec.org/a/spr/jecfin/v42y2018i4d10.1007_s12197-018-9431-4.html)) finds home teams cover when they are large underdogs. Both are older, subgroup findings with many possible slices, and the market has had a decade to absorb them. Our always-home result on CFB COINFLIPs was 50.94% (a pass-or-fail cut at that level is already in the 2026-08-28 home-on-mean-tie proposal). |
| Favorite-longshot / big CFB favorites overpriced | [Sinkey and Logan](https://www.aeaweb.org/conference/2010/retrieve.php?pdfid=406) (working paper, 11,000 games, 1985-2003) find favorites overpriced. The sample ends in 2003; our COINFLIP favorites cover 51.3% in 2021-2025. Relevant only as support if the underdog rule is chosen. |
| Momentum / recent against-the-spread record | [Moskowitz 2021 (Journal of Finance)](https://doi.org/10.1111/jofi.13082) finds momentum in betting but returns too small to beat betting costs; [Cox et al. 2021 (J. Sports Economics)](https://journals.sagepub.com/doi/abs/10.1177/1527002520975837) find inefficiency in handling momentum, "within transaction costs." Our pool has no betting fee, so a 1-2 point edge would count, but the market-anchored team residual (Candidate 2) already used this kind of information and was NULL. |
| Travel, time zone, rest, Thursday games | [Coleman 2017](https://doi.org/10.1177/1527002515574514) finds college travel effects mispriced, mostly for late-season underdogs a time zone behind. A re-analysis preprint of the earlier "West Coast night game" claim reports no effect ([medRxiv](https://www.medrxiv.org/content/10.1101/2023.10.19.23296960v1); I saw only a search summary). [Lopez and Bliss](https://arxiv.org/abs/2408.10867) (preprint) find no remaining rest edge in the NFL after 2011. Conflicting, small, and subgroup-driven. |
| Weather and totals | Weather mostly moves totals. [Arscott 2023](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4197428) finds a college totals bias worth over 55% on team totals (abstract only; page refused access), but that is a different bet than our sides. The nflverse file does carry wind and temperature if ever needed. |
| Public bet percentages / reverse line movement | The NFL evidence ([Paul and Weinbach](https://ideas.repec.org/a/taf/apeclt/v18y2011i2p193-197.html)) and a one-season CFB study ([Traugutt and Morton 2022](https://ideas.repec.org/a/ebl/ecbull/eb-22-00065.html), 2020-21, a pandemic year) are positive but old or narrow, and public percentages are not available historically for free, so we cannot backtest. The 2026-08-28 document already suggests Action Network percentages as a proxy for crowd habits, which belongs under Idea 2. |
| Later decision time to catch news | Late money is mostly recreational (see Idea 5). Any real news already shows up in the market-versus-CBS gap that defines STRONG and LEAN; a COINFLIP is defined as no gap. |
| Low-visibility games have mispriced lines | [Krieger and Davis 2024](https://ideas.repec.org/a/spr/jecfin/v48y2024i2d10.1007_s12197-023-09656-5.html) (NFL, 2007-2021) find larger line movement in less-watched games. That is where the CBS-versus-market gap already arises, so it feeds the tiers, not COINFLIP. |
| Follow the CFBD Model Pick'em models | [predictions.collegefootballdata.com](https://predictions.collegefootballdata.com/models): about 100 hobbyist models post predicted margins for every FBS game; the site's public API (`predictionsapi.collegefootballdata.com/api/crowd-wisdom/games?season=Y`) returns every model's prediction, the spread, and the result for 2021-2026. Checked 2026-10-05 (this session): the models' median prediction went 45.5%, 49.2%, 51.3%, 52.4%, 51.0% and 46.5% against the spread in 2021-2026, and 49.6% even where it disagreed with the spread by 4+ points (n=500). Each season's top five models by ATS record went about 51% the next season, and year-to-year correlation of records was about zero (-0.04 to +0.27). No persistent edge. The site does not say which line or timestamp it grades against. |
| More model features, paid data | Same reasoning as the 2026-08-28 document and the two NULL results: public ratings are likely already in the market. |

## How much can a COINFLIP improvement be worth?

Using the 2026-08-28 benchmark (15 games a week, COINFLIP 55.6% of the sheet,
51 entrants, about $200 a week, 18 weeks): moving COINFLIP accuracy from 49.5% to
52.5% (+3 points) raised the weekly win chance from 1.96% to 2.58%. Scaling:
**each +1 point of COINFLIP accuracy is worth about +0.2 points of weekly win
chance, roughly $0.40 a week or $7 across an 18-week season.**

| Idea | Plausible COINFLIP-accuracy gain | Plausible weekly-win gain | Confidence |
| --- | --- | --- | --- |
| 1. Key-number hooks | roughly +1.5 to +2 points on NFL COINFLIPs (about +3 to +4 picks per season, corrected margin rates) | about +0.3 to +0.4 points | High that the arithmetic holds; the edge on a real CBS line is unproven |
| 5. Price / Pinnacle | 0 to +1 | 0 to +0.2 | Low; may be negative |
| Any prediction model | 0 (two NULLs) | 0 | High |
| 2. Pool-aware contrarian | not an accuracy gain | the simulation's 2.6% to 4.9% gap, of which the learned version might add up to about +0.8 over the plain underdog rule | Medium for the direction, low for the size; depends on favorites covering 51% or less |
| 3. Late standings-aware picks | not an accuracy gain | unmeasured; bounded by the 3-6 late picks per week | Low |
| 4. Tiebreak guess | not an accuracy gain | unmeasured; applies only in weeks tied at the top | Low |

The pattern matches the 2026-08-28 conclusion: any plausible accuracy gain on
coin-flip games is worth a few dollars a season, and the pool-structure levers are
worth an order of magnitude more but rest on weaker, simulation-only evidence.
Nothing here justifies buying data.

## Source register

Own data: the odds-lines, games, pool-picks and pool-results tables in the local
database (2026-10-05); `docs/results.md`; `docs/invariants.md`;
`docs/data-inventory.md`; `docs/research/2026-08-28-weekly-win-coinflip-strategy.md`;
the nflverse [schedule file and dictionary](https://github.com/nflverse/nfldata/blob/master/DICTIONARY.md).

Read in full: [Clair and Letscher (2007)](https://pubsonline.informs.org/doi/abs/10.1287/opre.1070.0448)
(author copy at Berkeley); [Sinkey and Logan (2009 working paper)](https://www.aeaweb.org/conference/2010/retrieve.php?pdfid=406) (introduction and abstract).

Abstract or index-page only (publisher refused full access): Levitt
([NBER 9422](https://www.nber.org/papers/w9422); [Economic Journal 2004](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-0297.2004.00207.x));
Paul and Weinbach ([2011a](https://ideas.repec.org/a/taf/apeclt/v18y2011i2p193-197.html),
[2011b](https://ideas.repec.org/a/jsf/intjsf/v6y2011i4p307-316.html));
Shank; Cox et al.; Coleman; Moskowitz; Arscott; Kain and Logan; Franck et al.;
Krieger and Davis; Traugutt and Morton; Stern (via Semantic Scholar listing);
Sides and Harvill, Szalkowski and Nelson, Lopez and Bliss, Kaunitz et al.
(arXiv preprints); the medRxiv jet-lag preprint (search summary only).
Odds service: [v4 guide](https://the-odds-api.com/liveapi/guides/v4/) and
[bookmaker regions](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html).

Not used as evidence, deliberately: bookmaker and tout pages on "sharp" books and
key numbers, except where noted as unsupported.
