from itertools import product

import pytest

from pickem.backtest.pool_sim import (
    Rule,
    SimGame,
    SimWeek,
    at_least_one,
    draw,
    evaluate,
    format_report,
    rule_sides,
    search_optimal,
    season_summary,
    simulate_week,
    win_chance,
)
from pickem.models import Side, Tier

H, A = Side.HOME, Side.AWAY


def game(i, tier=Tier.COINFLIP, spread=-3.5, model_side=H):
    return SimGame(
        game_id=f"g{i}", label=f"A{i} at H{i}", league_spread=spread,
        tier=tier, model_side=model_side,
    )


def week(games, others, pool_week=1, unrecommended=0):
    return SimWeek(
        pool_week=pool_week, games=tuple(games),
        others=tuple(tuple(row) for row in others), unrecommended=unrecommended,
    )


def test_rules_differ_only_on_coinflips():
    # g0: coinflip, home favored, every other entrant on the favorite.
    # g1: strong, model on the away side.
    w = week([game(0), game(1, tier=Tier.STRONG, model_side=A)], [[H, H]] * 3)
    assert rule_sides(w, Rule.CURRENT) == (H, A)
    assert rule_sides(w, Rule.UNDERDOG) == (A, A)
    assert rule_sides(w, Rule.MINORITY) == (A, A)
    assert rule_sides(w, Rule.LOPSIDED) == (A, A)


def test_minority_takes_the_favorite_on_an_exact_split():
    w = week([game(0)], [[H], [A]])
    assert rule_sides(w, Rule.MINORITY) == (H,)


def test_minority_takes_the_favorite_when_the_pool_prefers_the_underdog():
    w = week([game(0)], [[A], [A], [H]])
    assert rule_sides(w, Rule.MINORITY) == (H,)


def test_lopsided_threshold_is_seventy_percent_of_entrants_who_picked():
    seven_of_ten = [[H]] * 7 + [[A]] * 3 + [[None]] * 5
    six_of_ten = [[H]] * 6 + [[A]] * 4
    assert rule_sides(week([game(0)], seven_of_ten), Rule.LOPSIDED) == (A,)
    assert rule_sides(week([game(0)], six_of_ten), Rule.LOPSIDED) == (H,)


def test_favorite_follows_the_cbs_spread():
    # +6.5 home: the away team is favored.
    w = week([game(0, spread=6.5)], [[A]])
    assert rule_sides(w, Rule.CURRENT) == (A,)
    assert rule_sides(w, Rule.UNDERDOG) == (H,)


def test_optimal_is_not_a_fixed_rule():
    with pytest.raises(ValueError):
        rule_sides(week([game(0)], [[H]]), Rule.OPTIMAL)


def test_game_without_a_recommendation_is_even_and_keeps_our_side():
    g = game(0, tier=None, model_side=A)
    assert g.p_home == 0.5
    w = week([g], [[H]])
    assert all(rule_sides(w, rule) == (A,) for rule in Rule if rule is not Rule.OPTIMAL)


def test_blank_picks_are_always_wrong():
    w = week([game(0), game(1)], [[None, None], [H, None]])
    d = draw(w, 2000, "blank")
    for outcome, best in zip(d.outcomes, d.best, strict=True):
        expected = 1 if outcome & 1 else 0  # only entrant 2's home pick on g0 can score
        assert best == expected
    assert all(scores[0] == 0 for scores in d.others_sorted)


def test_a_tie_for_first_is_half_a_win():
    # One other entrant always picks exactly what we pick: always tied.
    w = week([game(0)], [[H]])
    d = draw(w, 2000, "tie")
    assert win_chance((H,), d) == pytest.approx(0.5)


def test_identical_sides_give_identical_results():
    w = week([game(0), game(1, tier=Tier.LEAN)], [[H, A], [A, A], [H, H]])
    d = draw(w, 2000, "same")
    a = evaluate(Rule.CURRENT, (H, A), d)
    b = evaluate(Rule.MINORITY, (H, A), d)
    assert (a.avg_points, a.win_chance, a.top3_chance, a.median_rank) == (
        b.avg_points, b.win_chance, b.top3_chance, b.median_rank,
    )


def test_going_against_a_unanimous_pool_raises_win_chance_at_the_same_average():
    # Four coinflips, five other entrants all on the (home) favorite everywhere.
    w = week([game(i) for i in range(4)], [[H] * 4] * 5)
    d = draw(w, 20_000, "contrarian")
    current = evaluate(Rule.CURRENT, rule_sides(w, Rule.CURRENT), d)
    underdog = evaluate(Rule.UNDERDOG, rule_sides(w, Rule.UNDERDOG), d)
    assert current.win_chance == pytest.approx(1 / 6)  # always a six-way tie
    assert underdog.win_chance == pytest.approx(0.375, abs=0.015)  # 5/16 + 6/16 * 1/6
    assert underdog.avg_points == pytest.approx(current.avg_points, abs=0.05)


@pytest.mark.parametrize(
    ("tier", "model_side", "rate"), [(Tier.STRONG, H, 0.637), (Tier.LEAN, A, 0.542)]
)
def test_model_side_wins_at_its_tier_rate(tier, model_side, rate):
    w = week([game(0, tier=tier, model_side=model_side)], [[H]])
    d = draw(w, 20_000, "rates")
    home_rate = sum(o & 1 for o in d.outcomes) / len(d.outcomes)
    model_rate = home_rate if model_side is H else 1 - home_rate
    assert model_rate == pytest.approx(rate, abs=0.015)


def test_rank_counts_entrants_strictly_ahead():
    # We pick away, the other two pick home: rank 1 when away covers, 3 when home does.
    w = week([game(0)], [[H], [H]])
    d = draw(w, 2000, "rank")
    result = evaluate(Rule.UNDERDOG, (A,), d)
    away_rate = 1 - sum(o & 1 for o in d.outcomes) / len(d.outcomes)
    assert result.top3_chance == 1.0
    assert result.win_chance == pytest.approx(away_rate)
    assert result.median_rank in (1, 2, 3)


def test_same_seed_same_draws():
    w = week([game(i) for i in range(3)], [[H, A, H], [A, A, H]])
    assert draw(w, 500, "s") == draw(w, 500, "s")
    assert draw(w, 500, "s").outcomes != draw(w, 500, "t").outcomes
def three_coinflip_week():
    return week(
        [game(0), game(1, spread=2.5), game(2), game(3, tier=Tier.STRONG)],
        [[H, A, H, H], [H, A, A, H], [A, A, H, H], [H, H, H, A], [H, A, H, H]],
    )


def test_search_never_ends_below_the_current_rule():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    assert win_chance(search_optimal(w, d), d) >= win_chance(rule_sides(w, Rule.CURRENT), d)


def test_search_ends_where_no_single_switch_helps():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    found = search_optimal(w, d)
    base = win_chance(found, d)
    for i, g in enumerate(w.games):
        if g.is_coinflip:
            flipped = list(found)
            flipped[i] = A if flipped[i] is H else H
            assert win_chance(flipped, d) <= base + 1e-12


def test_search_finds_the_brute_force_best_on_three_coinflips():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    best = max(
        win_chance((a, b, c, H), d) for a, b, c in product((H, A), repeat=3)
    )
    assert win_chance(search_optimal(w, d), d) == pytest.approx(best)


def test_search_leaves_non_coinflip_games_alone():
    w = three_coinflip_week()
    assert search_optimal(w, draw(w, 2000, "x"))[3] is H


def test_week_without_coinflips_keeps_the_current_rule():
    w = week([game(0, tier=Tier.LEAN), game(1, tier=Tier.STRONG, model_side=A)], [[H, H]] * 3)
    result = simulate_week(w, draws=2000, seed=1)
    assert result.result(Rule.OPTIMAL).sides == result.result(Rule.CURRENT).sides
    assert result.switched == ()


def test_simulate_week_reports_every_rule_in_order():
    w = three_coinflip_week()
    result = simulate_week(w, draws=2000, seed=1)
    assert [r.rule for r in result.results] == list(Rule)
    assert result.entrants == 6 and result.draws == 2000 and result.pool_week == 1


def test_switched_lists_coinflips_the_optimal_mix_took_against_the_favorite():
    w = three_coinflip_week()
    result = simulate_week(w, draws=2000, seed=1)
    optimal = result.result(Rule.OPTIMAL).sides
    expected = [
        g.label for g, side in zip(w.games, optimal, strict=True)
        if g.is_coinflip and side is not g.favorite
    ]
    assert [s.label for s in result.switched] == expected


def test_simulate_week_is_repeatable():
    w = three_coinflip_week()
    assert simulate_week(w, draws=1000, seed=7) == simulate_week(w, draws=1000, seed=7)
def test_at_least_one_win_in_eighteen_weeks():
    assert at_least_one(0.02) == pytest.approx(1 - 0.98**18)
    assert at_least_one(0.0) == 0.0


def test_season_summary_averages_weekly_win_chance():
    w1 = simulate_week(three_coinflip_week(), draws=1000, seed=1)
    w2 = simulate_week(week([game(0)], [[H]] * 3, pool_week=2), draws=1000, seed=1)
    rows = {row.label: row for row in season_summary([w1, w2])}
    expected = (w1.result(Rule.CURRENT).win_chance + w2.result(Rule.CURRENT).win_chance) / 2
    assert rows["current"].weekly_win == pytest.approx(expected)
    assert rows["current"].season_win == pytest.approx(at_least_one(expected))
    assert rows["minority*"].weekly_win == pytest.approx(
        (w1.result(Rule.MINORITY).win_chance + w2.result(Rule.MINORITY).win_chance) / 2
    )
    assert rows["no-edge entrant"].weekly_win == pytest.approx((1 / 6 + 1 / 4) / 2)


def test_report_labels_ceilings_and_counts_unrecommended_games():
    w = week(
        [game(0), game(1, tier=None, model_side=A)], [[H, H]] * 4, pool_week=3, unrecommended=1
    )
    text = format_report([simulate_week(w, draws=1000, seed=1)])
    assert "Pool week 3: 5 entrants, 1,000 simulated weeks" in text
    for rule in Rule:
        assert str(rule) in text
    assert "minority*" in text and "current*" not in text
    assert "* ceiling" in text
    assert "no stored recommendation (treated as 50/50): 1" in text
    assert "no-edge entrant" in text
    assert "Season, pool week 3" in text


def test_report_names_the_switched_games():
    # Every other entrant on the favorite everywhere: the search takes underdogs.
    w = week([game(i) for i in range(4)], [[H] * 4] * 5)
    text = format_report([simulate_week(w, draws=2000, seed=1)])
    assert "optimal mix took the underdog on:" in text
    assert "A0 at H0 (-3.5, pool 100% on the favorite)" in text
