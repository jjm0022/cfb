from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from dashboard_helpers import synthetic_season

from pickem.models import Game, RecommendationRecord, Side, Sport, Tier
from pickem.notify.discord_dm import build_results_embed
from pickem.report.results import (
    Strategy,
    findings,
    grade_game,
    record_for,
    slight_stop_warning,
)
from pickem.report.results_markdown import _season

KICK = datetime(2026, 10, 4, 17, tzinfo=UTC)


def graded(gid="g", *, sport=Sport.NFL, line=-3.5, tier=Tier.SLIGHT, delta=-0.5,
           side=Side.AWAY, home=20, away=17):
    game = Game(game_id=gid, sport=sport, season=2026, week=6, kickoff_utc=KICK,
                home_team_id="H", away_team_id="A", home_score=home, away_score=away)
    rec = RecommendationRecord(game_id=gid, sport=sport, season=2026, week=6, side=side,
                               tier=tier, edge_points=delta,
                               generated_at=KICK - timedelta(hours=1), source="refresh")
    return grade_game(game=game, pool_week=6, league_spread=line, close_spread=None,
                      our_side=side, field_home=0, field_away=0, history=[rec])


def test_key_number_comes_from_the_models_market():
    assert graded(line=-3.5, delta=-0.5).key_number == 3  # market -3.0
    assert graded(line=-4.5, delta=0.5, side=Side.HOME).key_number is None  # market -5.0
    assert graded(tier=Tier.LEAN, delta=-1.5).key_number is None


def test_record_for_splits_slight_by_key_number():
    win_key = graded("a")  # away covers -3.5 on a 3-point home win
    loss_other = graded("b", line=-4.5, delta=0.5, side=Side.HOME, home=20, away=17)
    games = [win_key, loss_other]
    crossed = record_for(games, Strategy.MODEL, tier=Tier.SLIGHT, key_number=True)
    other = record_for(games, Strategy.MODEL, tier=Tier.SLIGHT, key_number=False)
    assert (crossed.wins, crossed.losses) == (1, 0)
    assert (other.wins, other.losses) == (0, 1)


def losing_slight(n, sport=Sport.NFL):
    # Model on the home side at -4.5, home wins by 3: a loss every time.
    return [graded(f"l{i}", sport=sport, line=-4.5, delta=0.5, side=Side.HOME)
            for i in range(n)]


def test_warning_when_slight_is_clearly_below_half():
    text = slight_stop_warning(losing_slight(12))
    assert text is not None
    assert "2026-10-06-nfl-slight-tier-design.md" in text
    assert "revert" in text


def test_no_warning_when_slight_is_uncertain():
    games = losing_slight(5) + [graded(f"w{i}") for i in range(5)]
    assert slight_stop_warning(games) is None
    assert slight_stop_warning([]) is None


def test_cfb_games_never_trigger_the_warning():
    assert slight_stop_warning(losing_slight(12, sport=Sport.CFB)) is None


def test_findings_lead_with_the_warning():
    assert findings(losing_slight(12)).claims[0] == slight_stop_warning(losing_slight(12))


def test_markdown_splits_slight_rows_and_labels_the_reference():
    games = [graded("a"), graded("b", line=-4.5, delta=0.5, side=Side.HOME)]
    text = "\n".join(_season(SimpleNamespace(season_games=tuple(games), unknown_model_games=0),
                             [Sport.NFL]))
    assert "| NFL | slight, crosses 3 or 7 |" in text
    assert "| NFL | slight, other |" in text
    assert "stand-in" in text


def test_markdown_omits_slight_rows_for_a_board_without_slight_picks():
    games = [graded("c", sport=Sport.CFB, tier=Tier.COINFLIP, delta=0.0, side=Side.HOME)]
    text = "\n".join(_season(SimpleNamespace(season_games=tuple(games), unknown_model_games=0),
                             [Sport.CFB]))
    assert "slight" not in text


def test_dm_carries_the_warning(monkeypatch):
    monkeypatch.setattr("pickem.notify.discord_dm.slight_stop_warning", lambda games: "WARN")
    embed = build_results_embed(synthetic_season(), Path("report.md"))
    assert any(field.value == "WARN" for field in embed.fields)


def test_dm_has_no_warning_field_normally(monkeypatch):
    monkeypatch.setattr("pickem.notify.discord_dm.slight_stop_warning", lambda games: None)
    embed = build_results_embed(synthetic_season(), Path("report.md"))
    assert not any("early stop" in field.name.lower() for field in embed.fields)
