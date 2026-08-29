# Props — NFL and CFB player-prop decision system

**Date:** 2026-08-29  
**Status:** Approved design, pending vendor acceptance trial and implementation plan

## 1. Decision and goal

`props` is a new, standalone project. It is not an extension of the existing
CBS ATS pick'em application. It may later share extracted, stable utilities,
but initially owns its own data model, release cycle, evaluation ledger, and
Discord service.

The system produces one owner-only Discord DM at 11:00 AM America/New_York on
each NFL or eligible CFB gameday. It identifies favorable player-prop entries
available on PrizePicks and Underdog, then presents at most one $10 candidate
entry for each platform. It never submits an entry automatically.

Initial sport scope is NFL plus Power 4 and Notre Dame CFB. Initial prop scope
is common, liquid QB passing and RB/WR/TE rushing/receiving markets. Obscure,
thin, or experimental markets are excluded.

## 2. Non-goals and safety constraints

- No direct scraping, reverse engineering, or use of undocumented operator
  endpoints.
- No account access, automated contest entry, or automatic money movement.
- No claim that an entry is placeable until the displayed app line, contest
  format, and payout have been manually confirmed.
- No correlation-based combination optimization in the first release.
- No more messages after the 11 AM gameday DM; a good later line does not
  justify breaking the delivery rule.

PrizePicks and Underdog remain intended execution platforms, but their data
feeds are a conditional choice. Direct access is rejected by their terms; the
system must use a licensed third-party feed and pass the acceptance trial in
§9. See [board-data research](../../research/2026-08-29-player-prop-board-data-access.md).

## 3. User-visible behavior

At 11:00 AM ET on a gameday, the service sends one Discord DM containing:

1. A PrizePicks primary card, if one meets the quality gate.
2. An Underdog primary card, if one meets the quality gate.
3. A ranked 5–10-prop watchlist, including viable but non-primary candidates.

Each primary card has 2–6 legs and a flat $10 nominal stake. A slate may have
zero, one, or two cards; it never forces an entry. Games that begin less than
30 minutes after message generation are excluded.

Each card lists platform, captured timestamp, legs and lines, projected
probabilities, uncertainty/freshness rationale, the data-derived payout
assumption, and a clear instruction to confirm the actual app terms before
placement. The user can send a simple owner-authorized `placed` confirmation;
this records the actual accepted card separately from the recommendation.

## 4. Market-first strategy

The shadow pilot is market-first. A licensed provider supplies both the
PrizePicks/Underdog board and consensus sportsbook player-prop prices. The
engine uses consensus prices to estimate an outcome distribution or event
probability after removing vig, then evaluates the platform line and allowed
directions against the card's payout terms.

An in-house player projection runs only as a parallel scorecard in the pilot.
It cannot affect recommendations until a separately predeclared validation
shows incremental, out-of-sample value over the market-first baseline. A later
hybrid model may make transparent, validated adjustments for usage, injury,
weather, matchup, and CFB data quality; it is out of scope for the first
release.

## 5. Architecture

```text
Licensed board and sportsbook feeds
  → source adapters and canonical player/game/market identities
  → append-only board and consensus snapshots
  → implied-probability and vig-removal evaluator
  → eligibility/freshness/uncertainty gates
  → independent-card entry optimizer
  → manual board reconciliation
  → Discord DM and placed-confirmation boundary
  → official-stat settlement and evaluation ledger
```

### Data boundaries

- **Feed adapters** obtain only licensed data and retain raw responses plus
  vendor/source timestamps.
- **Canonical identity** resolves teams, games, athletes, stat definitions,
  period, and event start time. Unknown or ambiguous identity fails closed.
- **Snapshot store** is append-only. It preserves source time, retrieval time,
  line, direction availability, multiplier information, and all fields needed
  to recreate a recommendation.
- **Reconciliation input** is an immutable manual app-board snapshot. It is the
  final reference for a sent or placed entry until a provider demonstrates
  complete agreement.

### Decision boundaries

- **Probability evaluator** is pure and returns a probability, uncertainty,
  supporting consensus, and explicit invalid/missing reason.
- **Eligibility gate** rejects non-liquid or unsupported markets, stale input,
  games within the 30-minute exclusion window, uncertainty beyond a configured
  bound, and unavailable/ambiguous platform variants.
- **Entry optimizer** considers 2–6-leg, single-platform cards only. It uses
  conservative, independence-based expected-value bounds, rejects repeated or
  correlated same-game combinations, and may return no card.
- **Delivery boundary** sends only the scheduled DM; it cannot trigger
  automated placement.
- **Ledger** separately records recommendation, user-confirmed placement,
  stat outcome, platform settlement, and any void/reboot/adjustment.

## 6. Platform semantics

The app-visible terms always win over a vendor-derived representation. The
entry optimizer is allowed to describe an estimated payout only when the feed
provides the relevant multiplier and the format is understood. It must never
infer user-specific promotions, boosted lines, flex/power differences, void
rules, or a final account-specific payout from a generic price.

If the final app display disagrees with the recommendation, the `placed`
confirmation captures the actual accepted terms. The result ledger grades the
actual entry separately and preserves the mismatch as vendor-quality evidence.

## 7. Error handling

- Missing, stale, or mismatched board data: omit that platform/card and explain
  why in the DM or operational log.
- Unknown player or stat mapping: fail that proposition closed; do not guess.
- Vendor outage or quota issue: send no forced card; retain the failure event.
- Settlement ambiguity or provider correction: retain raw facts, mark outcome
  pending, and reconcile only against official statistics and platform rules.
- Discord failure: record the unsent recommendation package and alert the
  owner through a recoverable operational path; never duplicate a card
  silently.

## 8. Testing and auditability

Tests use captured/licensed fixtures and cover source normalization, player and
game identity, vig removal, market-line direction, stale-data rejection,
30-minute cutoff behavior, 2–6-leg optimization, correlation rejection,
platform-format handling, manual reconciliation, schedule timing, owner-only
confirmation, and settlement exceptions.

Every card must be reproducible from immutable board/consensus snapshots and a
versioned configuration. Reports distinguish recommendations, simulated shadow
cards, and confirmed placed entries. Evaluation reports calibration,
probability-bucket error, coverage, feed-to-manual agreement, line movement,
settlement adjustments, and uncertainty-aware entry-level results. Raw ROI is
reported but never treated as sufficient evidence by itself.

## 9. Vendor acceptance and shadow trial

Before implementation relies on a vendor, run a paid/read-only 4–6 week trial
with Prop Professor first and SportsGameOdds or OpticOdds as an independent
fallback. Obtain written license confirmation for private storage and use of
the platform data.

The trial measures, for NFL and eligible CFB separately:

- board and market coverage;
- player/game/stat mapping failures;
- refresh latency and stale-line rate;
- agreement with immutable manual PrizePicks and Underdog snapshots;
- multiplier and displayed-payout agreement;
- vendor outage/quality behavior; and
- historical-data availability sufficient for the planned evaluation.

During the trial, all proposed cards are simulated at $10. Real-money use is
not enabled by a single profitable period: predeclared coverage, reconciliation,
calibration, and entry-level evaluation gates must all pass first. Failure to
prove a reliable board source removes the affected platform from scope rather
than falling back to direct scraping.

## 10. Deferred work

- Automated provider selection after the trial.
- Projection-first or hybrid model decisions.
- Additional sports and markets.
- Explicit correlation modeling.
- Any user interface beyond owner-only Discord DMs and confirmation commands.
- Shared extraction with the existing `cfb` repository.
